"""
Unit tests for the PolicyRulesEngine.
"""

from datetime import date
import pytest

from app.policy.rules_engine import PolicyRulesEngine


class TestPolicyRulesEngine:
    """Tests for PolicyRulesEngine."""

    def test_load_policy_terms(self, policy_engine):
        """Verify that policy terms and coverage limits are loaded correctly."""
        assert policy_engine.policy_data is not None
        assert policy_engine.policy_data.get("policy_id") == "PLUM_GHI_2024"
        
        # Test categories are loaded
        categories = policy_engine.policy_data.get("opd_categories", {})
        assert "consultation" in categories
        assert "dental" in categories
        assert "pharmacy" in categories

    def test_get_member_existing(self, policy_engine):
        """Verify that existing member Rajesh Kumar is returned with correct details."""
        member = policy_engine.get_member("EMP001")
        assert member is not None
        assert member["name"] == "Rajesh Kumar"
        assert member["join_date"] == "2024-04-01"

    def test_get_member_unknown(self, policy_engine):
        """Verify that querying an unknown member ID returns None."""
        member = policy_engine.get_member("EMP999")
        assert member is None

    def test_check_member_eligibility(self, policy_engine):
        """Verify eligibility rule results for active and non-existent members."""
        # Existing member passes
        res_active = policy_engine.check_member_eligibility("EMP001")
        assert res_active.passed is True
        assert res_active.rule_name == "member_eligibility"
        assert res_active.severity == "info"

        # Non-existent member fails
        res_unknown = policy_engine.check_member_eligibility("EMP999")
        assert res_unknown.passed is False
        assert res_unknown.severity == "block"

    def test_check_waiting_period_initial(self, policy_engine):
        """Verify initial waiting period check (30 days from join date)."""
        # Rajesh Kumar joined 2024-04-01. Treatment 2024-04-10 (9 days after join) should fail.
        res = policy_engine.check_waiting_period("EMP001", "Viral Fever", "2024-04-10")
        assert res.passed is False
        assert "initial waiting period" in res.reason

        # Treatment 2024-05-15 (44 days after join) should pass.
        res_pass = policy_engine.check_waiting_period("EMP001", "Viral Fever", "2024-05-15")
        assert res_pass.passed is True

    def test_check_waiting_period_specific_condition(self, policy_engine):
        """Verify specific waiting period for conditions like diabetes (90 days)."""
        # Rajesh Kumar joined 2024-04-01. Treatment 2024-06-15 (75 days after join) for Diabetes should fail.
        res = policy_engine.check_waiting_period("EMP001", "Type 2 Diabetes Mellitus", "2024-06-15")
        assert res.passed is False
        assert "specific condition waiting period" in res.reason.lower() or "diabetes" in res.reason.lower()

        # Treatment 2024-08-01 (122 days after join) for Diabetes should pass.
        res_pass = policy_engine.check_waiting_period("EMP001", "Type 2 Diabetes Mellitus", "2024-08-01")
        assert res_pass.passed is True

    def test_check_exclusions(self, policy_engine):
        """Verify that exclusions (e.g. cosmetic surgery, bariatric) fail exclusion checks."""
        # Cosmetic surgery is in exclusion list
        res_cosmetic = policy_engine.check_exclusions(diagnosis="Cosmetic Surgery")
        assert res_cosmetic.passed is False
        assert res_cosmetic.severity == "block"

        # Viral Fever is not excluded
        res_viral = policy_engine.check_exclusions(diagnosis="Viral Fever")
        assert res_viral.passed is True

    def test_check_pre_authorization_rules(self, policy_engine):
        """Verify pre-authorization requirements for diagnostic and other categories."""
        # Consultation does not require pre-auth
        res_consult = policy_engine.check_pre_authorization(claim_category="CONSULTATION", claimed_amount=5000)
        assert res_consult.passed is True

        # Diagnostic high-value test (MRI) > 10,000 threshold requires pre-auth
        res_mri_fail = policy_engine.check_pre_authorization(
            claim_category="DIAGNOSTIC",
            claimed_amount=15000,
            line_items=[{"description": "Brain MRI", "amount": 12000}]
        )
        assert res_mri_fail.passed is False
        assert res_mri_fail.severity == "block"

        # Diagnostic high-value test but amount under threshold passes
        res_mri_pass = policy_engine.check_pre_authorization(
            claim_category="DIAGNOSTIC",
            claimed_amount=8000,
            line_items=[{"description": "Brain MRI", "amount": 8000}]
        )
        assert res_mri_pass.passed is True
