"""
Pipeline Executor — Main Orchestrator.

Mirrors the executor.py pattern from the SuperNodes platform. Orchestrates
all 7 agents in sequence, handling early exits, graceful degradation,
and building the final ClaimDecisionOutput.

Execution flow:
1. Agent 1: Document Classifier → classify each document
2. Agent 2: Document Validator → validate required docs present
   → If fails: HALT (return document error, no claim decision)
3. Agent 3: Document Parser → OCR + structured extraction
4. Agent 4: Cross-Document Validator → patient name consistency
   → If fails: HALT (return document error)
5. Agent 5: Policy Evaluator → evaluate all policy rules
6. Agent 6: Claim Adjudicator → calculate amounts and make decision
7. Agent 7: Fraud Detector → fraud signals
   → Can OVERRIDE decision to MANUAL_REVIEW

Design: Sequential execution because each agent depends on prior output.
If an agent fails (exception), the pipeline continues with degraded confidence.
Only document validation halts stop the pipeline entirely.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.agents.claim_adjudicator import ClaimAdjudicatorAgent
from app.agents.cross_validator import CrossDocumentValidatorAgent
from app.agents.document_classifier import DocumentClassifierAgent
from app.agents.document_parser import DocumentParserAgent
from app.agents.document_validator import DocumentValidatorAgent
from app.agents.fraud_detector import FraudDetectorAgent
from app.agents.policy_evaluator import PolicyEvaluatorAgent
from app.ai import ModelClient
from app.models.claim import (
    AmountBreakdown,
    ClaimDecision,
    ClaimDecisionOutput,
    ClaimSubmissionRequest,
)
from app.models.trace import AgentStatus, CheckSeverity
from app.pipeline.claim_context import ClaimContext
from app.policy import PolicyRulesEngine

logger = logging.getLogger(__name__)


class PipelineExecutor:
    """
    Orchestrates the 7-agent claims processing pipeline.
    
    Thread-safe: each execute() call creates its own ClaimContext.
    The executor is stateless — all state lives in ClaimContext.
    """

    def __init__(
        self,
        policy_engine: PolicyRulesEngine,
        ai_client: ModelClient | None = None,
    ):
        self._policy = policy_engine
        self._ai_client = ai_client

        # Initialize all agents
        self._agents_config = [
            ("classifier", DocumentClassifierAgent(ai_client=ai_client)),
            ("validator", DocumentValidatorAgent(policy_engine=policy_engine)),
            ("parser", DocumentParserAgent(ai_client=ai_client)),
            ("cross_validator", CrossDocumentValidatorAgent()),
            ("policy_evaluator", PolicyEvaluatorAgent(policy_engine=policy_engine)),
            ("adjudicator", ClaimAdjudicatorAgent(policy_engine=policy_engine)),
            ("fraud_detector", FraudDetectorAgent(policy_engine=policy_engine)),
        ]

        logger.info(
            "Pipeline initialized with %d agents: %s",
            len(self._agents_config),
            ", ".join(name for name, _ in self._agents_config),
        )

    async def execute(
        self,
        request: ClaimSubmissionRequest,
    ) -> ClaimDecisionOutput:
        """
        Process a claim through the full pipeline.
        
        Args:
            request: The claim submission from the API.
            
        Returns:
            ClaimDecisionOutput with decision, amounts, and full trace.
        """
        # Create execution context
        context = ClaimContext.from_request(request)

        # Pre-populate member name from roster for cross-validation in Agent 4
        member = self._policy.get_member(context.member_id)
        if member:
            context.member_name = member.get("name")

        logger.info(
            "═══ Pipeline started: claim_id=%s, member=%s, category=%s, amount=₹%.0f ═══",
            context.claim_id,
            context.member_id,
            context.claim_category,
            context.claimed_amount,
        )

        # ── Execute agents in sequence ───────────────────────────
        for agent_key, agent in self._agents_config:
            # Check if pipeline should halt (document validation failure)
            if context.should_halt:
                logger.info(
                    "⏸ Pipeline halted before [%s]: %s",
                    agent.agent_name,
                    context.halt_reason,
                )
                break

            # Execute agent (never throws — errors caught by BaseAgent)
            trace = await agent.execute(context)

            # Check for halt after document-related agents
            if context.should_halt:
                logger.info(
                    "⏸ Pipeline halted after [%s]: %s",
                    agent.agent_name,
                    context.halt_reason,
                )
                break

        # Mark pipeline complete
        context.finish()

        # Build the decision output
        output = self._build_decision_output(context)

        logger.info(
            "═══ Pipeline completed: claim_id=%s, decision=%s, amount=₹%.0f, confidence=%.3f, time=%dms ═══",
            context.claim_id,
            output.decision,
            output.approved_amount or 0,
            output.confidence_score,
            output.processing_time_ms,
        )

        return output

    def _build_decision_output(self, context: ClaimContext) -> ClaimDecisionOutput:
        """
        Build the final ClaimDecisionOutput from the context.
        
        Reads adjudicator and fraud detector results to determine
        the final decision, with possible override to MANUAL_REVIEW.
        """
        output = ClaimDecisionOutput(
            claim_id=context.claim_id,
            member_id=context.member_id,
            member_name=context.member_name,
            policy_id=context.policy_id,
            claim_category=context.claim_category,
            processing_time_ms=context.processing_time_ms,
            execution_trace=context.trace_summary,
            degraded_components=context.degraded_components,
        )

        # ── Document error (early halt) ──────────────────────────
        if context.is_document_error:
            output.is_document_error = True
            output.document_issues = [context.halt_reason or "Document validation failed"]
            output.confidence_score = 0.0
            # No decision for document errors — just return the error
            return output

        # ── Extract adjudicator decision ─────────────────────────
        adjudicator_output = self._get_agent_output(context, "claim_adjudicator")
        
        if adjudicator_output:
            decision_str = adjudicator_output.get("decision")
            if decision_str:
                output.decision = ClaimDecision(decision_str)
            output.approved_amount = adjudicator_output.get("approved_amount", 0)
            output.rejection_reasons = adjudicator_output.get("rejection_reasons", [])
            output.decision_reasons = adjudicator_output.get("decision_reasons", [])

            # Parse amount breakdown
            breakdown_data = adjudicator_output.get("amount_breakdown")
            if breakdown_data:
                output.amount_breakdown = AmountBreakdown(**breakdown_data)
        else:
            # Adjudicator didn't run (pipeline halted or failed)
            output.decision = ClaimDecision.MANUAL_REVIEW
            output.manual_review_recommended = True
            output.decision_reasons = ["Adjudicator did not complete — manual review required."]

        # ── Check fraud detector for override ────────────────────
        fraud_output = self._get_agent_output(context, "fraud_detector")
        
        if fraud_output:
            output.fraud_score = fraud_output.get("fraud_score", 0.0)
            output.fraud_signals = fraud_output.get("signals", [])

            # Override to MANUAL_REVIEW if fraud detected and claim was otherwise approved
            if fraud_output.get("recommend_review") and output.decision in (
                ClaimDecision.APPROVED,
                ClaimDecision.PARTIAL,
            ):
                output.decision = ClaimDecision.MANUAL_REVIEW
                output.manual_review_recommended = True
                output.decision_reasons.append(
                    f"Escalated to MANUAL_REVIEW due to fraud signals: "
                    + ", ".join(output.fraud_signals)
                )

        # ── Check cross-document validator for overrides ─────────
        for trace in context.agent_traces:
            if trace.agent_type == "cross_document_validator":
                for check in trace.checks:
                    if not check.passed and check.severity in (CheckSeverity.WARN, CheckSeverity.BLOCK):
                        if check.check_name in (
                            "category_content_verification",
                            "member_name_verification",
                            "treatment_date_verification",
                            "hospital_name_verification",
                            "claimed_amount_verification",
                        ):
                            if output.decision in (ClaimDecision.APPROVED, ClaimDecision.PARTIAL):
                                output.decision = ClaimDecision.MANUAL_REVIEW
                                output.manual_review_recommended = True
                                output.decision_reasons.append(
                                    f"Escalated to MANUAL_REVIEW due to cross-validation failure: {check.reason}"
                                )

        # ── Check policy evaluator for overrides ──────────────────
        for trace in context.agent_traces:
            if trace.agent_type == "policy_evaluator":
                for check in trace.checks:
                    # Ignore normal exclusions (exclusion_check, line_item_exclusion_*) which are handled by claim_adjudicator as PARTIAL
                    if check.check_name == "exclusion_check" or check.check_name.startswith("line_item_exclusion_"):
                        continue
                    if not check.passed:
                        if output.decision in (ClaimDecision.APPROVED, ClaimDecision.PARTIAL):
                            output.decision = ClaimDecision.MANUAL_REVIEW
                            output.manual_review_recommended = True
                            output.decision_reasons.append(
                                f"Escalated to MANUAL_REVIEW due to failed policy check '{check.check_name}': {check.reason}"
                            )

        # ── Handle degradation ───────────────────────────────────
        if context.degraded_components:
            output.manual_review_recommended = True
            output.decision_reasons.append(
                f"Manual review recommended due to incomplete processing. "
                f"Failed components: {', '.join(context.degraded_components)}."
            )
            # Reduce confidence for degraded pipeline
            context.reduce_confidence(0.7, "Pipeline had degraded components")

        # ── Final override handling / reset approved amount ──────
        if output.decision == ClaimDecision.MANUAL_REVIEW:
            # Preserve the adjudicator's original calculation for human review
            if adjudicator_output:
                output.pre_review_decision = adjudicator_output.get("decision")
                output.pre_review_approved_amount = adjudicator_output.get("approved_amount", 0)
            else:
                output.pre_review_decision = None
                output.pre_review_approved_amount = 0.0

            output.approved_amount = 0.0
            if output.amount_breakdown:
                output.amount_breakdown.approved_amount = 0.0

        # ── Final confidence score ───────────────────────────────
        output.confidence_score = round(context.overall_confidence, 3)

        # Additional timestamp
        output.processed_at = context.finished_at

        return output

    @staticmethod
    def _get_agent_output(context: ClaimContext, agent_type: str) -> dict[str, Any] | None:
        """Get the output_summary from a specific agent's trace."""
        for trace in context.agent_traces:
            if trace.agent_type == agent_type and trace.status == AgentStatus.SUCCESS:
                return trace.output_summary
        return None
