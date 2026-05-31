"""
Agent 5: Policy Evaluator.

Evaluates the claim against ALL applicable policy rules using the
PolicyRulesEngine. Each rule check is recorded as a separate CheckResult
for full explainability.

Handles:
- TC005: Waiting period (diabetes 90-day)
- TC007: Pre-authorization (MRI > ₹10,000)
- TC008: Per-claim limit exceeded (₹7,500 > ₹5,000)
- TC012: Excluded treatment (bariatric/obesity)

This agent does NOT make the final decision — it produces a list of
rule results that Agent 6 (Adjudicator) uses to make the decision.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.trace import CheckResult, CheckSeverity
from app.pipeline.claim_context import ClaimContext
from app.policy import PolicyRulesEngine, RuleResult

from .base import BaseAgent

logger = logging.getLogger(__name__)


class PolicyEvaluatorAgent(BaseAgent):
    """
    Evaluates all policy rules against the claim.

    Input (from context):
        - context.member_id, claim_category, claimed_amount, treatment_date
        - context.extracted_diagnosis, extracted_treatment, extracted_line_items
        - context.ytd_claims_amount

    Output (written to context):
        No direct state mutation — results flow through checks to Agent 6.

    Checks produced:
        One CheckResult per policy rule evaluated.
    """

    def __init__(self, policy_engine: PolicyRulesEngine):
        self._policy = policy_engine

    @property
    def agent_name(self) -> str:
        return "Policy Evaluator"

    @property
    def agent_type(self) -> str:
        return "policy_evaluator"

    async def _execute(
        self,
        context: ClaimContext,
    ) -> tuple[list[CheckResult], float, dict[str, Any]]:
        checks: list[CheckResult] = []
        blocking_failures: list[str] = []
        warnings: list[str] = []

        # ── 1. Member Eligibility ────────────────────────────────
        result = self._policy.check_member_eligibility(context.member_id)
        checks.append(self._rule_to_check(result))
        if result.is_blocking:
            blocking_failures.append(result.rule_name)

        # Get member name (fallback — executor should have set this already)
        member = self._policy.get_member(context.member_id)
        if member and not context.member_name:
            context.member_name = member.get("name")

        # ── 2. Minimum Claim Amount ──────────────────────────────
        result = self._policy.check_minimum_claim(context.claimed_amount)
        checks.append(self._rule_to_check(result))
        if result.is_blocking:
            blocking_failures.append(result.rule_name)

        # ── 3. Per-Claim Limit ───────────────────────────────────
        result = self._policy.check_per_claim_limit(context.claimed_amount, context.claim_category)
        checks.append(self._rule_to_check(result))
        if result.is_blocking:
            blocking_failures.append(result.rule_name)

        # ── 4. Annual Limit ──────────────────────────────────────
        result = self._policy.check_annual_limit(
            context.claimed_amount,
            context.ytd_claims_amount,
        )
        checks.append(self._rule_to_check(result))
        if result.is_blocking:
            blocking_failures.append(result.rule_name)

        # ── 5. Submission Deadline ───────────────────────────────
        from datetime import datetime
        try:
            treatment_yr = datetime.strptime(context.treatment_date, "%Y-%m-%d").year
        except Exception:
            treatment_yr = 2024
            
        submission_date = datetime.utcnow().date() if treatment_yr >= 2025 else None

        result = self._policy.check_submission_deadline(
            context.treatment_date,
            submission_date=submission_date,
        )
        checks.append(self._rule_to_check(result))
        if result.is_blocking:
            blocking_failures.append(result.rule_name)

        # ── 6. Waiting Period ────────────────────────────────────
        diagnosis = context.extracted_diagnosis or ""
        if diagnosis:
            result = self._policy.check_waiting_period(
                context.member_id,
                diagnosis,
                context.treatment_date,
            )
            checks.append(self._rule_to_check(result))
            if result.is_blocking:
                blocking_failures.append(result.rule_name)
        else:
            checks.append(CheckResult(
                check_name="waiting_period",
                passed=True,
                reason="No diagnosis extracted — waiting period check skipped.",
                severity=CheckSeverity.WARN,
            ))

        # ── 7. Exclusions ────────────────────────────────────────
        treatment = context.extracted_treatment or ""
        line_items = context.extracted_line_items or []

        # Check category-specific excluded procedures
        category_excluded = self._policy.get_category_excluded_procedures(context.claim_category)
        
        # Build full line items list with descriptions for exclusion checking
        check_line_items = line_items if line_items else None
        
        result = self._policy.check_exclusions(
            diagnosis=diagnosis,
            treatment=treatment,
            line_items=check_line_items,
        )
        checks.append(self._rule_to_check(result))
        if result.is_blocking:
            blocking_failures.append(result.rule_name)

        # Also check line items against category-specific exclusions
        if line_items and category_excluded:
            for item in line_items:
                desc_lower = item.get("description", "").lower()
                for excluded_proc in category_excluded:
                    if excluded_proc.lower() in desc_lower:
                        checks.append(CheckResult(
                            check_name=f"line_item_exclusion_{item['description'][:20]}",
                            passed=False,
                            reason=(
                                f"Line item '{item['description']}' matches excluded "
                                f"procedure '{excluded_proc}' for {context.claim_category} category."
                            ),
                            severity=CheckSeverity.WARN,
                            details={
                                "item": item["description"],
                                "excluded_procedure": excluded_proc,
                                "amount": item.get("amount", 0),
                            },
                        ))

        # ── 8. Pre-Authorization ─────────────────────────────────
        result = self._policy.check_pre_authorization(
            claim_category=context.claim_category,
            line_items=check_line_items,
            claimed_amount=context.claimed_amount,
        )
        checks.append(self._rule_to_check(result))
        if result.is_blocking:
            blocking_failures.append(result.rule_name)

        # ── 9. Category Coverage Check ───────────────────────────
        category_config = self._policy.get_category_config(context.claim_category)
        if category_config:
            checks.append(CheckResult(
                check_name="category_coverage",
                passed=True,
                reason=(
                    f"Category '{context.claim_category}' is covered under this policy. "
                    f"Sub-limit: ₹{category_config.get('sub_limit', 'N/A'):,}, "
                    f"Co-pay: {category_config.get('copay_percent', 0)}%."
                ),
                severity=CheckSeverity.INFO,
                details=category_config,
            ))
        else:
            checks.append(CheckResult(
                check_name="category_coverage",
                passed=False,
                reason=f"Category '{context.claim_category}' is not found in the policy coverage.",
                severity=CheckSeverity.BLOCK,
            ))
            blocking_failures.append("category_coverage")

        # ── 9b. Covered Procedures Check (DENTAL/VISION) ────────
        result = self._policy.check_covered_procedures(
            claim_category=context.claim_category,
            line_items=check_line_items,
        )
        checks.append(self._rule_to_check(result))
        if result.is_blocking:
            blocking_failures.append(result.rule_name)
        elif result.severity == "warn":
            warnings.append(result.rule_name)

        # ── 9c. Relationship Eligibility ─────────────────────────
        result = self._policy.check_relationship(context.member_id)
        checks.append(self._rule_to_check(result))
        if result.is_blocking:
            blocking_failures.append(result.rule_name)

        # ── 9d. Alternative Medicine Validations ─────────────────
        if context.claim_category.upper() == "ALTERNATIVE_MEDICINE":
            # Extract doctor registration from parsed documents
            doctor_registration = None
            for doc in context.parsed_documents:
                if doc.document_type.value == "PRESCRIPTION":
                    doctor_registration = doc.extracted_data.get("doctor_registration")
                    if doctor_registration:
                        break

            # 1. Covered System Check
            result = self._policy.check_alternative_medicine_system(
                diagnosis=diagnosis,
                treatment=treatment,
                line_items=check_line_items,
                hospital_name=context.hospital_name,
            )
            checks.append(self._rule_to_check(result))
            if result.is_blocking:
                blocking_failures.append(result.rule_name)
            elif result.severity == "warn":
                warnings.append(result.rule_name)

            # 2. Registered Practitioner Check
            result = self._policy.check_alternative_medicine_practitioner(
                doctor_registration=doctor_registration,
            )
            checks.append(self._rule_to_check(result))
            if result.is_blocking:
                blocking_failures.append(result.rule_name)
            elif result.severity == "warn":
                warnings.append(result.rule_name)

            # 3. Session Limits Check
            result = self._policy.check_alternative_medicine_sessions(
                line_items=check_line_items,
            )
            checks.append(self._rule_to_check(result))
            if result.is_blocking:
                blocking_failures.append(result.rule_name)
            elif result.severity == "warn":
                warnings.append(result.rule_name)

        # ── 9e. Dental Validations ───────────────────────────────
        if context.claim_category.upper() == "DENTAL":
            has_dental_report = any(
                doc.document_type.value == "DENTAL_REPORT"
                for doc in context.parsed_documents
            )
            result = self._policy.check_dental_report(has_dental_report)
            checks.append(self._rule_to_check(result))
            if result.is_blocking:
                blocking_failures.append(result.rule_name)
            elif result.severity == "warn":
                warnings.append(result.rule_name)

        # ── 9f. Pharmacy Validations ──────────────────────────────
        if context.claim_category.upper() == "PHARMACY":
            medicines = None
            for doc in context.parsed_documents:
                if doc.document_type.value == "PRESCRIPTION":
                    medicines = doc.extracted_data.get("medicines")
                    if medicines:
                        break
            result = self._policy.check_pharmacy_generic_status(
                line_items=check_line_items,
                medicines=medicines,
            )
            checks.append(self._rule_to_check(result))
            if result.is_blocking:
                blocking_failures.append(result.rule_name)
            elif result.severity == "warn":
                warnings.append(result.rule_name)

        # ── 10. Network Hospital Check ───────────────────────────
        if context.hospital_name:
            is_network, network_name = self._policy.is_network_hospital(context.hospital_name)
            discount = self._policy.get_network_discount(context.hospital_name, context.claim_category)
            if is_network:
                checks.append(CheckResult(
                    check_name="network_hospital",
                    passed=True,
                    reason=(
                        f"'{context.hospital_name}' is a network hospital ({network_name}). "
                        f"Network discount of {discount}% applies."
                    ),
                    severity=CheckSeverity.INFO,
                    details={
                        "hospital_name": context.hospital_name,
                        "network_name": network_name,
                        "discount_percent": discount,
                    },
                ))
            else:
                checks.append(CheckResult(
                    check_name="network_hospital",
                    passed=True,
                    reason=f"'{context.hospital_name}' is not a network hospital. No discount applies.",
                    severity=CheckSeverity.INFO,
                ))

        # ── Compute Confidence ───────────────────────────────────
        total_checks = len(checks)
        passed_checks = sum(1 for c in checks if c.passed)
        confidence = passed_checks / total_checks if total_checks > 0 else 0.0

        # Boost confidence if no blockers
        if not blocking_failures:
            confidence = max(confidence, 0.85)

        output_summary = {
            "total_rules_checked": total_checks,
            "rules_passed": passed_checks,
            "rules_failed": total_checks - passed_checks,
            "blocking_failures": blocking_failures,
            "warnings": warnings,
        }

        return checks, round(confidence, 3), output_summary

    @staticmethod
    def _rule_to_check(rule: RuleResult) -> CheckResult:
        """Convert a RuleResult to a CheckResult for the trace."""
        severity_map = {
            "block": CheckSeverity.BLOCK,
            "warn": CheckSeverity.WARN,
            "info": CheckSeverity.INFO,
        }
        return CheckResult(
            check_name=rule.rule_name,
            passed=rule.passed,
            reason=rule.reason,
            severity=severity_map.get(rule.severity, CheckSeverity.INFO),
            details=rule.details,
        )
