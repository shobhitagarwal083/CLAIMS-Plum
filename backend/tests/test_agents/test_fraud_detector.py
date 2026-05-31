"""
Unit tests for Agent 7: Fraud Detector.

Tests same-day frequency checks, monthly claims counts, duplicate claim detection,
and component failure simulation.
"""

import pytest

from app.agents.fraud_detector import FraudDetectorAgent, SimulatedComponentFailure
from app.models.trace import CheckSeverity


@pytest.mark.asyncio
class TestFraudDetector:
    """Tests for the FraudDetectorAgent."""

    async def test_no_fraud_signals_happy_path(self, make_context, policy_engine):
        """Clean claim without prior history passes with low fraud score."""
        agent = FraudDetectorAgent(policy_engine=policy_engine)
        ctx = make_context(
            member_id="EMP001",
            claimed_amount=2000.0,
            treatment_date="2025-03-15",
            claims_history=[],
        )

        checks, confidence, output = await agent._execute(ctx)

        assert confidence >= 0.85
        assert output["fraud_score"] < 0.3
        assert len(output.get("signals", [])) == 0

        # Verify info checks are present
        same_day = [c for c in checks if c.check_name == "same_day_claims"]
        assert len(same_day) == 1
        assert same_day[0].passed is True

    async def test_same_day_frequency_limits(self, make_context, policy_engine):
        """Claim exceeding same-day limit flags a fraud warning."""
        agent = FraudDetectorAgent(policy_engine=policy_engine)
        
        # Policy limit is 2 same-day claims (from policy_terms.json).
        # We inject 2 existing claims on the same day in history.
        history = [
            {"claim_id": "c1", "date": "2025-03-15", "amount": 1000.0, "status": "completed", "provider": "Apollo"},
            {"claim_id": "c2", "date": "2025-03-15", "amount": 1500.0, "status": "completed", "provider": "Fortis"},
        ]
        ctx = make_context(
            member_id="EMP001",
            claimed_amount=2000.0,
            treatment_date="2025-03-15",
            claims_history=history,
        )

        checks, confidence, output = await agent._execute(ctx)

        # Should flag same_day_claims
        same_day_check = [c for c in checks if c.check_name == "same_day_claims"][0]
        assert same_day_check.passed is False
        assert same_day_check.severity == CheckSeverity.WARN
        assert output["fraud_score"] >= 0.4
        assert any("SAME_DAY_CLAIMS" in sig for sig in output.get("signals", []))

    async def test_monthly_frequency_limits(self, make_context, policy_engine):
        """Claim exceeding monthly count limit (6) flags a warning."""
        agent = FraudDetectorAgent(policy_engine=policy_engine)
        
        # Ingress 6 claims in the same month (March 2025)
        history = [
            {"claim_id": f"c{i}", "date": f"2025-03-0{i}", "amount": 1000.0, "status": "completed"}
            for i in range(1, 7)
        ]
        ctx = make_context(
            member_id="EMP001",
            claimed_amount=2000.0,
            treatment_date="2025-03-15",
            claims_history=history,
        )

        checks, confidence, output = await agent._execute(ctx)

        monthly_check = [c for c in checks if c.check_name == "monthly_frequency"][0]
        assert monthly_check.passed is False
        assert monthly_check.severity == CheckSeverity.WARN
        assert output["fraud_score"] >= 0.2

    async def test_duplicate_claim_detection(self, make_context, policy_engine):
        """Duplicate claim with same date and amount triggers duplicate warning."""
        agent = FraudDetectorAgent(policy_engine=policy_engine)
        
        history = [
            {"claim_id": "c1", "date": "2025-03-15", "amount": 3000.0, "status": "completed", "claim_category": "CONSULTATION"},
        ]
        ctx = make_context(
            member_id="EMP001",
            claim_category="CONSULTATION",
            claimed_amount=3000.0,
            treatment_date="2025-03-15",
            claims_history=history,
        )

        checks, confidence, output = await agent._execute(ctx)

        dup_check = [c for c in checks if c.check_name == "duplicate_claim"][0]
        assert dup_check.passed is False
        assert dup_check.severity == CheckSeverity.WARN
        assert output["fraud_score"] >= 0.5
        assert any("DUPLICATE_CLAIM" in sig for sig in output.get("signals", []))

    async def test_duplicate_claim_ignores_rejected_claims(self, make_context, policy_engine):
        """Past rejected claims do not trigger duplicate claim warnings."""
        agent = FraudDetectorAgent(policy_engine=policy_engine)
        
        history = [
            {"claim_id": "c1", "date": "2025-03-15", "amount": 3000.0, "status": "rejected", "claim_category": "CONSULTATION"},
        ]
        ctx = make_context(
            member_id="EMP001",
            claim_category="CONSULTATION",
            claimed_amount=3000.0,
            treatment_date="2025-03-15",
            claims_history=history,
        )

        checks, confidence, output = await agent._execute(ctx)

        dup_check = [c for c in checks if c.check_name == "duplicate_claim"][0]
        assert dup_check.passed is True

    async def test_simulate_component_failure(self, make_context, policy_engine):
        """Raises SimulatedComponentFailure if simulate_component_failure=True."""
        agent = FraudDetectorAgent(policy_engine=policy_engine)
        ctx = make_context(simulate_component_failure=True)

        with pytest.raises(SimulatedComponentFailure):
            await agent._execute(ctx)
