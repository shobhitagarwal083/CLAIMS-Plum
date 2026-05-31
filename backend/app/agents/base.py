"""
Base Agent Contract.

Every agent in the claims pipeline implements this interface.
This is the Component Contract deliverable — precise enough that
another engineer could reimplement any agent without reading its code.

Contract:
    Input:  ClaimContext (mutable pipeline state)
    Output: AgentTraceEntry (immutable execution record)
    Errors: Never raises to the pipeline. All errors are caught,
            recorded in the trace, and the agent returns a FAILED status.

The pipeline executor calls execute() on each agent in sequence.
The agent reads from context, does its work, mutates context if needed,
and returns a trace entry recording what happened.
"""

from __future__ import annotations

import logging
import time
import traceback
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from app.models.trace import AgentStatus, AgentTraceEntry, CheckResult
from app.pipeline.claim_context import ClaimContext

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Abstract base for all pipeline agents.

    Subclasses implement _execute() with their specific logic.
    The base class handles:
    - Timing measurement
    - Error catching (agents never crash the pipeline)
    - Trace entry construction
    - Logging
    """

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Human-readable agent name (e.g., 'Document Classifier')."""
        ...

    @property
    @abstractmethod
    def agent_type(self) -> str:
        """Agent class identifier (e.g., 'document_classifier')."""
        ...

    async def execute(self, context: ClaimContext) -> AgentTraceEntry:
        """
        Execute this agent and return a trace entry.

        This is the public API called by the pipeline executor.
        It wraps _execute() with timing, error handling, and trace recording.

        Returns:
            AgentTraceEntry with full execution record.
            NEVER raises an exception — failures are recorded in the trace.
        """
        order_index = context.next_order_index()
        started_at = datetime.utcnow()
        start_time = time.monotonic()

        trace = AgentTraceEntry(
            agent_name=self.agent_name,
            agent_type=self.agent_type,
            order_index=order_index,
            started_at=started_at,
        )

        # Build input summary before execution to record the exact incoming state
        trace.input_summary = self._get_input_summary(context)

        try:
            logger.info("▶ [%s] Starting execution (order=%d)", self.agent_name, order_index)

            # Call the concrete implementation
            checks, confidence, output_summary = await self._execute(context)

            # Record success
            trace.status = AgentStatus.SUCCESS
            trace.checks = checks
            trace.confidence = confidence
            trace.output_summary = output_summary

            logger.info(
                "✓ [%s] Completed: %d checks (%d passed), confidence=%.3f",
                self.agent_name,
                len(checks),
                sum(1 for c in checks if c.passed),
                confidence,
            )

        except Exception as exc:
            # Agents NEVER crash the pipeline
            error_msg = f"{type(exc).__name__}: {exc}"
            tb = traceback.format_exc()
            logger.error("✗ [%s] Failed: %s\n%s", self.agent_name, error_msg, tb)

            trace.status = AgentStatus.FAILED
            trace.error = error_msg
            trace.confidence = 0.0
            trace.checks = [
                CheckResult(
                    check_name="agent_execution",
                    passed=False,
                    reason=f"Agent failed with error: {error_msg}",
                    severity="warn",
                )
            ]

            # Mark degradation on the context
            context.mark_degraded(self.agent_name, error_msg)

        finally:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            trace.finished_at = datetime.utcnow()
            trace.duration_ms = elapsed_ms
            context.add_trace(trace)

        return trace

    def _get_input_summary(self, context: ClaimContext) -> dict[str, Any]:
        """Automatically construct input summary based on agent type."""
        try:
            if self.agent_type == "document_classifier":
                return {
                    "documents": [
                        {
                            "file_id": doc.file_id,
                            "file_name": doc.file_name,
                            "actual_type": doc.actual_type,
                            "quality": doc.quality,
                            "has_preextracted_content": doc.content is not None
                        }
                        for doc in context.documents
                    ]
                }
            elif self.agent_type == "document_validator":
                return {
                    "classified_documents": [
                        {
                            "file_id": d.file_id,
                            "classified_type": d.classified_type.value if hasattr(d.classified_type, 'value') else str(d.classified_type),
                            "quality": d.quality.value if hasattr(d.quality, 'value') else str(d.quality)
                        }
                        for d in context.classified_documents
                    ]
                }
            elif self.agent_type == "document_parser":
                return {
                    "classified_documents": [
                        {
                            "file_id": d.file_id,
                            "classified_type": d.classified_type.value if hasattr(d.classified_type, 'value') else str(d.classified_type)
                        }
                        for d in context.classified_documents
                    ]
                }
            elif self.agent_type == "cross_document_validator":
                return {
                    "member_id": context.member_id,
                    "member_name": context.member_name,
                    "claim_category": context.claim_category,
                    "treatment_date": context.treatment_date,
                    "hospital_name": context.hospital_name,
                    "extracted_patient_names": context.extracted_patient_names,
                    "extracted_diagnosis": context.extracted_diagnosis,
                    "extracted_treatment": context.extracted_treatment,
                    "parsed_documents": [
                        {
                            "file_id": d.file_id,
                            "document_type": d.document_type.value if hasattr(d.document_type, 'value') else str(d.document_type)
                        }
                        for d in context.parsed_documents
                    ]
                }
            elif self.agent_type == "policy_evaluator":
                return {
                    "policy_id": context.policy_id,
                    "claim_category": context.claim_category,
                    "claimed_amount": context.claimed_amount,
                    "treatment_date": context.treatment_date,
                    "hospital_name": context.hospital_name,
                    "parsed_documents": [
                        {
                            "file_id": d.file_id,
                            "document_type": d.document_type.value if hasattr(d.document_type, 'value') else str(d.document_type),
                            "keys_extracted": list(d.extracted_data.keys()) if hasattr(d.extracted_data, 'keys') else []
                        }
                        for d in context.parsed_documents
                    ]
                }
            elif self.agent_type == "claim_adjudicator":
                return {
                    "policy_id": context.policy_id,
                    "claim_category": context.claim_category,
                    "claimed_amount": context.claimed_amount,
                    "ytd_claims_amount": context.ytd_claims_amount,
                    "claims_history_count": len(context.claims_history)
                }
            elif self.agent_type == "fraud_detector":
                return {
                    "member_id": context.member_id,
                    "claim_category": context.claim_category,
                    "claimed_amount": context.claimed_amount,
                    "treatment_date": context.treatment_date,
                    "hospital_name": context.hospital_name,
                    "claims_history_count": len(context.claims_history)
                }
        except Exception as e:
            logger.warning("Failed to generate input summary for %s: %s", self.agent_name, e)
        return {}

    @abstractmethod
    async def _execute(
        self,
        context: ClaimContext,
    ) -> tuple[list[CheckResult], float, dict[str, Any]]:
        """
        Concrete agent logic. Subclasses implement this.

        Args:
            context: The mutable pipeline state. Read input data from here
                     and write results back to it.

        Returns:
            Tuple of:
            - checks: List of CheckResult (what was examined and the verdict)
            - confidence: Agent-level confidence score (0.0 to 1.0)
            - output_summary: Dict summarizing the output (for trace display)

        Raises:
            Any exception — will be caught by execute() and recorded as FAILED.
        """
        ...
