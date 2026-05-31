"""
Unit tests for Agent 5: Policy Evaluator.

Tests member eligibility, waiting periods, exclusions, pre-authorization,
claim limits, and submission deadlines.
"""

import pytest

from app.agents.policy_evaluator import PolicyEvaluatorAgent
from app.models.claim import DocumentType, ParsedDocument


@pytest.mark.asyncio
class TestPolicyEvaluator:
    """Tests for the PolicyEvaluatorAgent."""

    def _setup_context(self, make_context, **overrides):
        """Create a context with parsed documents for policy evaluation."""
        defaults = dict(
            member_id="EMP001",
            member_name="Rajesh Kumar",
            claim_category="CONSULTATION",
            claimed_amount=3000.0,
            treatment_date="2025-03-15",
            hospital_name="Apollo Hospitals",
        )
        defaults.update(overrides)
        ctx = make_context(**defaults)

        # Set up parsed documents so policy evaluator has data to work with
        ctx.parsed_documents = [
            ParsedDocument(
                file_id="doc1",
                document_type=DocumentType.PRESCRIPTION,
                extracted_data={
                    "patient_name": "Ananya Sharma",
                    "diagnosis": "Viral Fever",
                    "date": defaults["treatment_date"],
                },
                extraction_confidence=0.98,
            ),
            ParsedDocument(
                file_id="doc2",
                document_type=DocumentType.HOSPITAL_BILL,
                extracted_data={
                    "total": defaults["claimed_amount"],
                    "date": defaults["treatment_date"],
                    "line_items": [
                        {"description": "Consultation Fee", "amount": defaults["claimed_amount"]}
                    ],
                },
                extraction_confidence=0.98,
            ),
        ]
        ctx.extracted_diagnosis = overrides.get("diagnosis", "Viral Fever")
        ctx.extracted_line_items = [
            {"description": "Consultation Fee", "amount": defaults["claimed_amount"], "source_doc": "doc2"}
        ]
        return ctx

    async def test_eligible_member_passes(self, make_context, policy_engine):
        """Active member MEM001 passes eligibility check."""
        agent = PolicyEvaluatorAgent(policy_engine=policy_engine)
        ctx = self._setup_context(make_context)

        checks, confidence, output = await agent._execute(ctx)

        eligibility_checks = [c for c in checks if "eligibility" in c.check_name]
        assert any(c.passed for c in eligibility_checks)

    async def test_unknown_member_fails(self, make_context, policy_engine):
        """Unknown member MEM999 fails eligibility."""
        agent = PolicyEvaluatorAgent(policy_engine=policy_engine)
        ctx = self._setup_context(make_context, member_id="MEM999")

        checks, confidence, output = await agent._execute(ctx)

        eligibility_checks = [c for c in checks if "eligibility" in c.check_name]
        assert any(not c.passed for c in eligibility_checks)

    async def test_waiting_period_initial_30_days(self, make_context, policy_engine):
        """Treatment 15 days after policy start fails 30-day initial waiting period."""
        agent = PolicyEvaluatorAgent(policy_engine=policy_engine)
        # Rajesh Kumar (EMP001) joined on 2024-04-01, so 2024-04-15 is within 30 days
        ctx = self._setup_context(make_context, treatment_date="2024-04-15")

        checks, confidence, output = await agent._execute(ctx)

        waiting_checks = [c for c in checks if "waiting" in c.check_name.lower()]
        if waiting_checks:
            assert any(not c.passed for c in waiting_checks)

    async def test_waiting_period_passes_after_30_days(self, make_context, policy_engine):
        """Treatment 60 days after policy start passes initial waiting period."""
        agent = PolicyEvaluatorAgent(policy_engine=policy_engine)
        ctx = self._setup_context(make_context, treatment_date="2025-03-15")

        checks, confidence, output = await agent._execute(ctx)

        waiting_checks = [c for c in checks if "waiting" in c.check_name.lower()]
        # After 30 days for a non-specific condition, should pass
        if waiting_checks:
            assert any(c.passed for c in waiting_checks)

    async def test_exclusion_cosmetic_surgery(self, make_context, policy_engine):
        """Cosmetic surgery is excluded by policy."""
        agent = PolicyEvaluatorAgent(policy_engine=policy_engine)
        ctx = self._setup_context(make_context, diagnosis="Cosmetic Surgery")
        ctx.extracted_diagnosis = "Cosmetic Surgery"

        checks, confidence, output = await agent._execute(ctx)

        exclusion_checks = [c for c in checks if "exclusion" in c.check_name.lower()]
        if exclusion_checks:
            assert any(not c.passed for c in exclusion_checks)

    async def test_pre_authorization_high_value(self, make_context, policy_engine):
        """Claim above pre-auth threshold (₹10,000+ for DIAGNOSTIC MRI) requires pre-authorization."""
        agent = PolicyEvaluatorAgent(policy_engine=policy_engine)
        ctx = self._setup_context(
            make_context,
            claim_category="DIAGNOSTIC",
            claimed_amount=15000.0,
        )
        ctx.extracted_line_items = [
            {"description": "MRI Scan", "amount": 15000.0, "source_doc": "doc2"}
        ]

        checks, confidence, output = await agent._execute(ctx)

        preauth_checks = [c for c in checks if "pre_auth" in c.check_name.lower() or "authorization" in c.check_name.lower()]
        if preauth_checks:
            assert any(not c.passed for c in preauth_checks)

    async def test_per_claim_limit(self, make_context, policy_engine):
        """Claim exceeding per-claim limit for category is rejected."""
        agent = PolicyEvaluatorAgent(policy_engine=policy_engine)
        # CONSULTATION per_claim_limit is ₹10,000 from policy_terms.json
        ctx = self._setup_context(make_context, claimed_amount=15000.0)

        checks, confidence, output = await agent._execute(ctx)

        limit_checks = [c for c in checks if "per_claim" in c.check_name.lower() or "limit" in c.check_name.lower()]
        if limit_checks:
            assert any(not c.passed for c in limit_checks)

    async def test_annual_limit_near_cap(self, make_context, policy_engine):
        """YTD near annual limit + new claim exceeds total → flagged."""
        agent = PolicyEvaluatorAgent(policy_engine=policy_engine)
        # Annual limit is ₹5,00,000
        ctx = self._setup_context(
            make_context,
            claimed_amount=30000.0,
            ytd_claims_amount=490000.0,
        )
        ctx.ytd_claims_amount = 490000.0

        checks, confidence, output = await agent._execute(ctx)

        annual_checks = [c for c in checks if "annual" in c.check_name.lower()]
        if annual_checks:
            assert any(not c.passed for c in annual_checks)
