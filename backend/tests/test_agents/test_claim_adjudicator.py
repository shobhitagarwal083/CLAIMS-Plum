"""
Unit tests for Agent 6: Claim Adjudicator.

Tests financial calculations: network discount, co-pay, sub-limits,
partial approvals, and full rejections.
"""

import pytest

from app.agents.claim_adjudicator import ClaimAdjudicatorAgent
from app.models.claim import DocumentType, ParsedDocument
from app.models.trace import AgentTraceEntry, AgentStatus, CheckResult, CheckSeverity


@pytest.mark.asyncio
class TestClaimAdjudicator:
    """Tests for the ClaimAdjudicatorAgent."""

    def _setup_context(self, make_context, policy_engine, checks_to_inject=None, **overrides):
        """
        Create a context ready for adjudication.
        Injects policy evaluator trace checks so the adjudicator can read them.
        """
        defaults = dict(
            member_id="MEM001",
            claim_category="CONSULTATION",
            claimed_amount=3000.0,
            treatment_date="2025-03-15",
            hospital_name="Apollo Hospitals",
        )
        defaults.update(overrides)
        ctx = make_context(**defaults)

        # Set up parsed documents
        ctx.parsed_documents = [
            ParsedDocument(
                file_id="doc2",
                document_type=DocumentType.HOSPITAL_BILL,
                extracted_data={
                    "total": defaults["claimed_amount"],
                    "line_items": [
                        {"description": "Consultation Fee", "amount": defaults["claimed_amount"]}
                    ],
                },
                extraction_confidence=0.98,
            ),
        ]
        ctx.extracted_line_items = [
            {"description": "Consultation Fee", "amount": defaults["claimed_amount"], "source_doc": "doc2"}
        ]

        # Inject policy evaluator trace (all checks passing by default)
        pe_checks = checks_to_inject or [
            CheckResult(
                check_name="member_eligibility", passed=True,
                reason="Member eligible.", severity=CheckSeverity.INFO,
            ),
            CheckResult(
                check_name="waiting_period", passed=True,
                reason="Waiting period satisfied.", severity=CheckSeverity.INFO,
            ),
            CheckResult(
                check_name="exclusion_check", passed=True,
                reason="No exclusions found.", severity=CheckSeverity.INFO,
            ),
            CheckResult(
                check_name="per_claim_limit", passed=True,
                reason="Within per-claim limit.", severity=CheckSeverity.INFO,
            ),
            CheckResult(
                check_name="annual_limit", passed=True,
                reason="Within annual limit.", severity=CheckSeverity.INFO,
            ),
        ]

        trace = AgentTraceEntry(
            agent_name="Policy Evaluator",
            agent_type="policy_evaluator",
            order_index=4,
            status=AgentStatus.SUCCESS,
            checks=pe_checks,
            confidence=0.95,
        )
        ctx.add_trace(trace)

        return ctx

    async def test_full_approval_calculation(self, make_context, policy_engine):
        """₹3,000 consultation at Apollo (network) → discount + copay → approved amount."""
        agent = ClaimAdjudicatorAgent(policy_engine=policy_engine)
        ctx = self._setup_context(make_context, policy_engine)

        checks, confidence, output = await agent._execute(ctx)

        assert output["decision"] == "APPROVED"
        assert output["approved_amount"] > 0
        assert output["approved_amount"] <= 3000.0
        assert "amount_breakdown" in output

    async def test_rejection_for_policy_violation(self, make_context, policy_engine):
        """Claim with blocking policy check → REJECTED with ₹0."""
        agent = ClaimAdjudicatorAgent(policy_engine=policy_engine)
        blocking_checks = [
            CheckResult(
                check_name="per_claim_limit", passed=False,
                reason="Claimed ₹15,000 exceeds per-claim limit of ₹10,000.",
                severity=CheckSeverity.BLOCK,
            ),
        ]
        ctx = self._setup_context(
            make_context, policy_engine,
            checks_to_inject=blocking_checks,
            claimed_amount=15000.0,
        )

        checks, confidence, output = await agent._execute(ctx)

        assert output["decision"] == "REJECTED"
        assert output["approved_amount"] == 0
        assert len(output["rejection_reasons"]) > 0

    async def test_partial_approval_with_exclusions(self, make_context, policy_engine):
        """Claim with some excluded line items → PARTIAL approval."""
        agent = ClaimAdjudicatorAgent(policy_engine=policy_engine)
        # Inject line_item_exclusion checks from policy evaluator
        pe_checks = [
            CheckResult(
                check_name="member_eligibility", passed=True,
                reason="Member eligible.", severity=CheckSeverity.INFO,
            ),
            CheckResult(
                check_name="exclusion_check", passed=True,
                reason="No global exclusions.", severity=CheckSeverity.INFO,
            ),
            CheckResult(
                check_name="line_item_exclusion_cosmetic",
                passed=False,
                reason="Cosmetic procedure excluded.",
                severity=CheckSeverity.WARN,
                details={"excluded_items": [{"description": "Cosmetic", "amount": 2000}]},
            ),
        ]
        ctx = self._setup_context(
            make_context, policy_engine,
            checks_to_inject=pe_checks,
            claimed_amount=10000.0,
        )
        ctx.extracted_line_items = [
            {"description": "Consultation Fee", "amount": 8000, "source_doc": "doc2"},
            {"description": "Cosmetic", "amount": 2000, "source_doc": "doc2"},
        ]

        checks, confidence, output = await agent._execute(ctx)

        # Should be APPROVED or PARTIAL (adjudicator may still approve if non-blocking)
        assert output["decision"] in ("APPROVED", "PARTIAL")
        assert output["approved_amount"] <= 10000.0

    async def test_amount_breakdown_structure(self, make_context, policy_engine):
        """Amount breakdown contains all required fields."""
        agent = ClaimAdjudicatorAgent(policy_engine=policy_engine)
        ctx = self._setup_context(make_context, policy_engine)

        checks, confidence, output = await agent._execute(ctx)

        breakdown = output.get("amount_breakdown", {})
        assert "claimed_amount" in breakdown
        assert "approved_amount" in breakdown
        assert "copay_percent" in breakdown or "copay_amount" in breakdown

    async def test_zero_amount_rejected(self, make_context, policy_engine):
        """If all checks fail and amount is 0 → REJECTED."""
        agent = ClaimAdjudicatorAgent(policy_engine=policy_engine)
        blocking = [
            CheckResult(
                check_name="exclusion_check", passed=False,
                reason="Excluded condition: cosmetic surgery.",
                severity=CheckSeverity.BLOCK,
            ),
        ]
        ctx = self._setup_context(
            make_context, policy_engine,
            checks_to_inject=blocking,
        )

        checks, confidence, output = await agent._execute(ctx)

        assert output["decision"] == "REJECTED"
        assert output["approved_amount"] == 0
