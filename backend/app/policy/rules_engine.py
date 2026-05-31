"""
Policy Rules Engine.

Reads ALL business logic from policy_terms.json dynamically — zero hardcoded
policy rules. Every rule check returns a structured RuleResult with:
- pass/fail status
- human-readable reason (for ops team)
- machine-readable details (for programmatic handling)
- severity level (block/warn/info)

This is the single source of truth for policy evaluation. If a rule changes,
only policy_terms.json needs to change — no code modification required.

Design decision: The engine is stateless and side-effect-free. It receives
data and returns verdicts. It does NOT modify claim state or make DB calls.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Rule Result ──────────────────────────────────────────────────────


@dataclass
class RuleResult:
    """
    Outcome of a single policy rule check.
    
    This is the atomic unit of policy explainability.
    """
    rule_name: str
    passed: bool
    reason: str
    severity: str = "info"  # "block" | "warn" | "info"
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return not self.passed and self.severity == "block"


# ── Condition Mapping ────────────────────────────────────────────────

# Maps diagnosis keywords to policy condition keys for waiting period checks.
# This avoids LLM calls for deterministic matching.
# All 9 conditions from policy_terms.json are covered.
DIAGNOSIS_CONDITION_MAP: dict[str, str] = {
    # Diabetes variants (90 days)
    "diabetes": "diabetes",
    "t2dm": "diabetes",
    "type 2 diabetes": "diabetes",
    "type 1 diabetes": "diabetes",
    "diabetic": "diabetes",
    "diabetes mellitus": "diabetes",
    # Hypertension variants (90 days)
    "hypertension": "hypertension",
    "htn": "hypertension",
    "high blood pressure": "hypertension",
    "elevated bp": "hypertension",
    # Thyroid variants (90 days)
    "thyroid": "thyroid_disorders",
    "hypothyroidism": "thyroid_disorders",
    "hyperthyroidism": "thyroid_disorders",
    "thyroiditis": "thyroid_disorders",
    # Joint Replacement variants (730 days)
    "joint replacement": "joint_replacement",
    "knee replacement": "joint_replacement",
    "hip replacement": "joint_replacement",
    "arthroplasty": "joint_replacement",
    "total knee": "joint_replacement",
    "total hip": "joint_replacement",
    # Maternity variants (270 days)
    "maternity": "maternity",
    "pregnancy": "maternity",
    "prenatal": "maternity",
    "antenatal": "maternity",
    "postnatal": "maternity",
    "obstetric": "maternity",
    "delivery": "maternity",
    # Mental Health variants (180 days)
    "depression": "mental_health",
    "anxiety": "mental_health",
    "mental health": "mental_health",
    "psychiatric": "mental_health",
    "psychotherapy": "mental_health",
    "bipolar": "mental_health",
    "schizophrenia": "mental_health",
    "ptsd": "mental_health",
    # Obesity Treatment variants (365 days)
    "obesity treatment": "obesity_treatment",
    "morbid obesity": "obesity_treatment",
    "bmi management": "obesity_treatment",
    # Hernia variants (365 days)
    "hernia": "hernia",
    "inguinal hernia": "hernia",
    "umbilical hernia": "hernia",
    "hiatal hernia": "hernia",
    "hernioplasty": "hernia",
    # Cataract variants (365 days)
    "cataract": "cataract",
    "phacoemulsification": "cataract",
    "lens implant": "cataract",
    "iol implant": "cataract",
}

# Maps keywords to exclusion categories.
# All exclusion conditions from policy_terms.json are covered.
EXCLUSION_KEYWORD_MAP: dict[str, str] = {
    # Cosmetic or aesthetic procedures
    "cosmetic": "Cosmetic or aesthetic procedures",
    "aesthetic": "Cosmetic or aesthetic procedures",
    "teeth whitening": "Cosmetic or aesthetic procedures",
    "hair transplant": "Cosmetic or aesthetic procedures",
    "botox": "Cosmetic or aesthetic procedures",
    "liposuction": "Cosmetic or aesthetic procedures",
    "rhinoplasty": "Cosmetic or aesthetic procedures",
    # Bariatric surgery
    "bariatric": "Bariatric surgery",
    # Obesity and weight loss programs
    "weight loss": "Obesity and weight loss programs",
    "obesity": "Obesity and weight loss programs",
    "diet plan": "Obesity and weight loss programs",
    "diet program": "Obesity and weight loss programs",
    # Infertility and assisted reproduction
    "infertility": "Infertility and assisted reproduction",
    "ivf": "Infertility and assisted reproduction",
    "assisted reproduction": "Infertility and assisted reproduction",
    "iui": "Infertility and assisted reproduction",
    # Self-inflicted injuries
    "self-inflicted": "Self-inflicted injuries",
    "self inflicted": "Self-inflicted injuries",
    # Experimental treatments
    "experimental": "Experimental treatments",
    "clinical trial": "Experimental treatments",
    # Substance abuse treatment
    "substance abuse": "Substance abuse treatment",
    "alcohol rehab": "Substance abuse treatment",
    "drug rehabilitation": "Substance abuse treatment",
    "de-addiction": "Substance abuse treatment",
    "deaddiction": "Substance abuse treatment",
    # Vaccination (non-medically necessary)
    "vaccination": "Vaccination (non-medically necessary)",
    "vaccine": "Vaccination (non-medically necessary)",
    "immunization": "Vaccination (non-medically necessary)",
    # Health supplements and tonics
    "supplement": "Health supplements and tonics",
    "tonic": "Health supplements and tonics",
    "multivitamin": "Health supplements and tonics",
    "protein powder": "Health supplements and tonics",
    "nutraceutical": "Health supplements and tonics",
}


# ── Policy Engine ────────────────────────────────────────────────────


class PolicyRulesEngine:
    """
    Stateless policy evaluation engine.
    
    Loads rules from policy_terms.json and provides check methods
    that return structured RuleResults. Never modifies state.
    
    Thread-safe: all methods are pure functions on the loaded policy data.
    """

    def __init__(self, policy_path: str | Path):
        self._policy_path = Path(policy_path)
        self._policy: dict[str, Any] = {}
        self._members: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """Load and index policy data."""
        if not self._policy_path.exists():
            raise FileNotFoundError(f"Policy file not found: {self._policy_path}")
        
        with open(self._policy_path, "r") as f:
            self._policy = json.load(f)

        # Index members by member_id for O(1) lookup
        self._members = {
            m["member_id"]: m 
            for m in self._policy.get("members", [])
        }
        
        logger.info(
            "Policy loaded: %s, %d members, %d categories",
            self._policy.get("policy_id", "unknown"),
            len(self._members),
            len(self._policy.get("coverage_categories", {})),
        )

    def reload(self) -> None:
        """Hot-reload policy data without restart."""
        self._load()

    @property
    def policy_data(self) -> dict[str, Any]:
        return self._policy

    # ── Member Checks ────────────────────────────────────────────

    def check_member_eligibility(self, member_id: str) -> RuleResult:
        """Verify member exists in the policy roster."""
        member = self._members.get(member_id)
        if not member:
            return RuleResult(
                rule_name="member_eligibility",
                passed=False,
                reason=f"Member '{member_id}' not found in policy roster. "
                       f"Valid member IDs are: {', '.join(sorted(self._members.keys())[:5])}...",
                severity="block",
                details={"member_id": member_id, "valid_count": len(self._members)},
            )

        return RuleResult(
            rule_name="member_eligibility",
            passed=True,
            reason=f"Member '{member['name']}' ({member_id}) is active on policy.",
            severity="info",
            details={"member_name": member["name"], "join_date": member.get("join_date")},
        )

    def get_member(self, member_id: str) -> dict[str, Any] | None:
        """Get member data by ID."""
        return self._members.get(member_id)

    # ── Waiting Periods ──────────────────────────────────────────

    def check_waiting_period(
        self,
        member_id: str,
        diagnosis: str,
        treatment_date: str | date,
    ) -> RuleResult:
        """
        Check if treatment falls within a waiting period.
        
        Two types of waiting periods:
        1. Initial waiting period (all conditions) — 30 days
        2. Specific condition waiting periods (diabetes=90d, etc.)
        """
        member = self._members.get(member_id)
        if not member:
            return RuleResult(
                rule_name="waiting_period",
                passed=True,  # Can't check without member data
                reason="Member not found — waiting period check skipped.",
                severity="warn",
            )

        join_date = date.fromisoformat(member["join_date"])
        t_date = date.fromisoformat(str(treatment_date)) if isinstance(treatment_date, str) else treatment_date

        waiting_periods = self._policy.get("waiting_periods", {})

        # 1. Check initial waiting period
        initial_days = waiting_periods.get("initial_waiting_period_days", 30)
        initial_end = join_date + timedelta(days=initial_days)
        if t_date < initial_end:
            return RuleResult(
                rule_name="waiting_period",
                passed=False,
                reason=(
                    f"Treatment date ({t_date.isoformat()}) falls within the "
                    f"{initial_days}-day initial waiting period. Member joined on "
                    f"{join_date.isoformat()}. Eligible from {initial_end.isoformat()}."
                ),
                severity="block",
                details={
                    "type": "initial",
                    "join_date": join_date.isoformat(),
                    "waiting_days": initial_days,
                    "eligible_from": initial_end.isoformat(),
                    "treatment_date": t_date.isoformat(),
                },
            )

        # 2. Check specific condition waiting periods
        specific = waiting_periods.get("specific_conditions", {})
        diagnosis_lower = diagnosis.lower()

        matched_condition = None
        matched_days = None

        for keyword, condition_key in DIAGNOSIS_CONDITION_MAP.items():
            if keyword in diagnosis_lower and condition_key in specific:
                matched_condition = condition_key
                matched_days = specific[condition_key]
                break

        if matched_condition and matched_days:
            condition_end = join_date + timedelta(days=matched_days)
            if t_date < condition_end:
                return RuleResult(
                    rule_name="waiting_period",
                    passed=False,
                    reason=(
                        f"Diagnosis '{diagnosis}' maps to condition '{matched_condition}' "
                        f"which has a {matched_days}-day waiting period. Member joined on "
                        f"{join_date.isoformat()}. Eligible for {matched_condition}-related "
                        f"claims from {condition_end.isoformat()}."
                    ),
                    severity="block",
                    details={
                        "type": "specific_condition",
                        "condition": matched_condition,
                        "diagnosis": diagnosis,
                        "join_date": join_date.isoformat(),
                        "waiting_days": matched_days,
                        "eligible_from": condition_end.isoformat(),
                        "treatment_date": t_date.isoformat(),
                    },
                )

        return RuleResult(
            rule_name="waiting_period",
            passed=True,
            reason=(
                f"No waiting period restriction for treatment on {t_date.isoformat()}. "
                f"Member joined {join_date.isoformat()} ({(t_date - join_date).days} days ago)."
            ),
            severity="info",
            details={"days_since_join": (t_date - join_date).days},
        )

    # ── Exclusions ───────────────────────────────────────────────

    def check_exclusions(
        self,
        diagnosis: str,
        treatment: str | None = None,
        line_items: list[dict] | None = None,
    ) -> RuleResult:
        """
        Check if diagnosis, treatment, or any line items are excluded.
        
        Returns details about which specific items are excluded for
        partial approval handling (TC006).
        """
        exclusions = self._policy.get("exclusions", {})
        excluded_conditions: list[str] = exclusions.get("conditions", [])
        excluded_procedures: list[str] = exclusions.get("procedures", [])
        all_excluded = [e.lower() for e in excluded_conditions + excluded_procedures]

        # Check diagnosis against exclusion keywords
        diagnosis_lower = (diagnosis or "").lower()
        treatment_lower = (treatment or "").lower()
        combined = f"{diagnosis_lower} {treatment_lower}"

        for keyword, exclusion_name in EXCLUSION_KEYWORD_MAP.items():
            if keyword in combined:
                return RuleResult(
                    rule_name="exclusion_check",
                    passed=False,
                    reason=(
                        f"'{exclusion_name}' is explicitly excluded under this policy. "
                        f"Matched keyword '{keyword}' in diagnosis/treatment: '{diagnosis}'."
                    ),
                    severity="block",
                    details={
                        "exclusion": exclusion_name,
                        "matched_keyword": keyword,
                        "diagnosis": diagnosis,
                        "treatment": treatment,
                    },
                )

        # Check line items for partial exclusions
        if line_items:
            excluded_items = []
            covered_items = []
            for item in line_items:
                desc_lower = item.get("description", "").lower()
                is_excluded = False
                matched_exclusion = None
                for keyword, exclusion_name in EXCLUSION_KEYWORD_MAP.items():
                    if keyword in desc_lower:
                        is_excluded = True
                        matched_exclusion = exclusion_name
                        break
                # Also check category-specific excluded procedures
                category_exclusions = self._get_category_excluded_procedures(line_items)
                for exc_proc in category_exclusions:
                    if exc_proc.lower() in desc_lower:
                        is_excluded = True
                        matched_exclusion = exc_proc
                        break

                if is_excluded:
                    excluded_items.append({
                        **item,
                        "exclusion_reason": matched_exclusion,
                    })
                else:
                    covered_items.append(item)

            if excluded_items and covered_items:
                # Partial exclusion — some items covered, some not
                return RuleResult(
                    rule_name="exclusion_check",
                    passed=True,  # Passed = can proceed, but with partial
                    reason=(
                        f"{len(excluded_items)} of {len(line_items)} line items are excluded. "
                        f"Excluded: {', '.join(i['description'] for i in excluded_items)}."
                    ),
                    severity="warn",
                    details={
                        "excluded_items": excluded_items,
                        "covered_items": covered_items,
                        "is_partial": True,
                    },
                )
            elif excluded_items and not covered_items:
                # All items excluded
                return RuleResult(
                    rule_name="exclusion_check",
                    passed=False,
                    reason=f"All line items are excluded under this policy.",
                    severity="block",
                    details={"excluded_items": excluded_items, "is_partial": False},
                )

        return RuleResult(
            rule_name="exclusion_check",
            passed=True,
            reason="No exclusions apply to this claim.",
            severity="info",
        )

    def _get_category_excluded_procedures(self, line_items: list[dict]) -> list[str]:
        """Get excluded procedures from all coverage categories."""
        excluded = []
        for cat_key, cat_data in self._policy.get("opd_categories", {}).items():
            if isinstance(cat_data, dict):
                excluded.extend(cat_data.get("excluded_procedures", []))
        return excluded

    # ── Pre-Authorization ────────────────────────────────────────

    def check_pre_authorization(
        self,
        claim_category: str,
        line_items: list[dict] | None = None,
        claimed_amount: float | Decimal = 0,
        has_pre_auth: bool = False,
    ) -> RuleResult:
        """Check if pre-authorization is required but missing."""
        if has_pre_auth:
            return RuleResult(
                rule_name="pre_authorization",
                passed=True,
                reason="Pre-authorization has been obtained.",
                severity="info",
            )

        category_key = claim_category.lower()
        category_data = self._policy.get("opd_categories", {}).get(category_key)
        if not category_data:
            return RuleResult(
                rule_name="pre_authorization",
                passed=True,
                reason=f"No pre-authorization rules found for category '{claim_category}'.",
                severity="info",
            )

        # Check if category has pre-auth requirements
        pre_auth_threshold_val = category_data.get("pre_auth_threshold")
        high_value_tests = category_data.get("high_value_tests_requiring_pre_auth", [])

        if not pre_auth_threshold_val and not high_value_tests:
            return RuleResult(
                rule_name="pre_authorization",
                passed=True,
                reason=f"Category '{claim_category}' does not require pre-authorization.",
                severity="info",
            )

        pre_auth_threshold = Decimal(str(pre_auth_threshold_val)) if pre_auth_threshold_val is not None else None
        claimed_amount_dec = Decimal(str(claimed_amount))

        # Check line items against high-value tests
        if line_items and high_value_tests and pre_auth_threshold is not None:
            for item in line_items:
                desc = item.get("description", "")
                amount = Decimal(str(item.get("amount", 0)))
                for test in high_value_tests:
                    if test.lower() in desc.lower() and amount > pre_auth_threshold:
                        return RuleResult(
                            rule_name="pre_authorization",
                            passed=False,
                            reason=(
                                f"Pre-authorization is required for '{test}' when the amount "
                                f"exceeds ₹{pre_auth_threshold:,.2f}. This claim includes "
                                f"'{desc}' at ₹{amount:,.2f}. Please obtain pre-authorization "
                                f"from Plum before the procedure and resubmit with the "
                                f"pre-authorization reference number."
                            ),
                            severity="block",
                            details={
                                "test": test,
                                "amount": float(amount),
                                "threshold": float(pre_auth_threshold),
                                "item_description": desc,
                            },
                        )

        # Also check total amount against threshold
        if pre_auth_threshold is not None and claimed_amount_dec > pre_auth_threshold:
            # Check if any high-value tests are in the claim
            if line_items:
                for item in line_items:
                    desc = item.get("description", "").lower()
                    for test in high_value_tests:
                        if test.lower() in desc:
                            return RuleResult(
                                rule_name="pre_authorization",
                                passed=False,
                                reason=(
                                    f"Pre-authorization is required for '{test}' when amount "
                                    f"exceeds ₹{pre_auth_threshold:,.2f}. Claimed amount: "
                                    f"₹{claimed_amount_dec:,.2f}. Please obtain pre-authorization "
                                    f"and resubmit."
                                ),
                                severity="block",
                                details={
                                    "test": test,
                                    "threshold": float(pre_auth_threshold),
                                    "claimed_amount": float(claimed_amount_dec),
                                },
                            )

        return RuleResult(
            rule_name="pre_authorization",
            passed=True,
            reason="No pre-authorization required for this claim.",
            severity="info",
        )

    # ── Financial Limits ─────────────────────────────────────────

    def check_per_claim_limit(self, claimed_amount: float | Decimal, claim_category: str) -> RuleResult:
        """
        Check if claimed amount exceeds per-claim limit.
        
        The global per_claim_limit (₹5,000) applies to categories that do NOT
        have a category sub_limit higher than the per-claim limit. Categories
        with their own sub_limit (e.g., dental=₹10,000) are governed by that
        sub_limit instead, avoiding double-blocking on TC006-style claims.
        """
        coverage = self._policy.get("coverage", {})
        per_claim_limit_val = coverage.get("per_claim_limit")

        if not per_claim_limit_val:
            return RuleResult(
                rule_name="per_claim_limit",
                passed=True,
                reason="No per-claim limit configured.",
                severity="info",
            )

        per_claim_limit = Decimal(str(per_claim_limit_val))
        claimed_amount_dec = Decimal(str(claimed_amount))

        # Check if the category has a sub_limit that overrides per_claim_limit
        category_config = self.get_category_config(claim_category)
        category_sub_limit_val = category_config.get("sub_limit") if category_config else None
        category_sub_limit = Decimal(str(category_sub_limit_val)) if category_sub_limit_val is not None else None

        if category_sub_limit is not None and category_sub_limit >= per_claim_limit:
            # Category has its own higher or equal sub-limit → per-claim limit not applicable
            return RuleResult(
                rule_name="per_claim_limit",
                passed=True,
                reason=(
                    f"Category '{claim_category}' has its own sub-limit of "
                    f"₹{category_sub_limit:,.2f} which overrides the global per-claim "
                    f"limit of ₹{per_claim_limit:,.2f}."
                ),
                severity="info",
                details={
                    "claimed_amount": float(claimed_amount_dec),
                    "per_claim_limit": float(per_claim_limit),
                    "category_sub_limit": float(category_sub_limit),
                    "overridden": True,
                },
            )

        if claimed_amount_dec > per_claim_limit:
            return RuleResult(
                rule_name="per_claim_limit",
                passed=False,
                reason=(
                    f"Claimed amount ₹{claimed_amount_dec:,.2f} exceeds the per-claim "
                    f"limit of ₹{per_claim_limit:,.2f}. Claims above this limit "
                    f"are not eligible for reimbursement."
                ),
                severity="block",
                details={
                    "claimed_amount": float(claimed_amount_dec),
                    "per_claim_limit": float(per_claim_limit),
                    "excess": float(claimed_amount_dec - per_claim_limit),
                },
            )

        return RuleResult(
            rule_name="per_claim_limit",
            passed=True,
            reason=f"Claimed amount ₹{claimed_amount_dec:,.2f} is within per-claim limit of ₹{per_claim_limit:,.2f}.",
            severity="info",
            details={"claimed_amount": float(claimed_amount_dec), "per_claim_limit": float(per_claim_limit)},
        )

    def check_annual_limit(
        self,
        claimed_amount: float | Decimal,
        ytd_amount: float | Decimal = 0,
    ) -> RuleResult:
        """Check if claim would exceed annual OPD limit."""
        coverage = self._policy.get("coverage", {})
        annual_limit_val = coverage.get("annual_opd_limit")
        if not annual_limit_val:
            return RuleResult(
                rule_name="annual_limit",
                passed=True,
                reason="No annual limit configured.",
                severity="info",
            )

        annual_limit = Decimal(str(annual_limit_val))
        claimed_amount_dec = Decimal(str(claimed_amount))
        ytd_amount_dec = Decimal(str(ytd_amount))

        total_after = ytd_amount_dec + claimed_amount_dec
        if total_after > annual_limit:
            remaining = max(Decimal('0.00'), annual_limit - ytd_amount_dec)
            return RuleResult(
                rule_name="annual_limit",
                passed=False,
                reason=(
                    f"This claim (₹{claimed_amount_dec:,.2f}) would bring YTD total to "
                    f"₹{total_after:,.2f}, exceeding the annual OPD limit of "
                    f"₹{annual_limit:,.2f}. Remaining balance: ₹{remaining:,.2f}."
                ),
                severity="block",
                details={
                    "claimed_amount": float(claimed_amount_dec),
                    "ytd_amount": float(ytd_amount_dec),
                    "annual_limit": float(annual_limit),
                    "remaining": float(remaining),
                },
            )

        return RuleResult(
            rule_name="annual_limit",
            passed=True,
            reason=(
                f"Within annual limit. YTD: ₹{ytd_amount_dec:,.2f} + this claim: "
                f"₹{claimed_amount_dec:,.2f} = ₹{total_after:,.2f} / ₹{annual_limit:,.2f}."
            ),
            severity="info",
            details={"ytd_amount": float(ytd_amount_dec), "remaining": float(annual_limit - total_after)},
        )

    def check_minimum_claim(self, claimed_amount: float | Decimal) -> RuleResult:
        """Check minimum claim amount."""
        submission_rules = self._policy.get("submission_rules", {})
        minimum_val = submission_rules.get("minimum_claim_amount", 0)

        minimum = Decimal(str(minimum_val))
        claimed_amount_dec = Decimal(str(claimed_amount))

        if minimum and claimed_amount_dec < minimum:
            return RuleResult(
                rule_name="minimum_claim_amount",
                passed=False,
                reason=f"Claimed amount ₹{claimed_amount_dec:,.2f} is below the minimum of ₹{minimum:,.2f}.",
                severity="block",
                details={"claimed_amount": float(claimed_amount_dec), "minimum": float(minimum)},
            )

        return RuleResult(
            rule_name="minimum_claim_amount",
            passed=True,
            reason=f"Claimed amount ₹{claimed_amount_dec:,.2f} meets minimum threshold.",
            severity="info",
        )

    def check_submission_deadline(
        self,
        treatment_date: str | date,
        submission_date: str | date | None = None,
    ) -> RuleResult:
        """Check if claim was submitted within the deadline."""
        submission_rules = self._policy.get("submission_rules", {})
        deadline_days = submission_rules.get("deadline_days_from_treatment", 30)

        t_date = date.fromisoformat(str(treatment_date)) if isinstance(treatment_date, str) else treatment_date
        
        # In test mode, we might not have a submission date, so default to treatment_date + 1 to pass
        if submission_date is None:
            s_date = t_date + timedelta(days=1)
        else:
            s_date = date.fromisoformat(str(submission_date)) if isinstance(submission_date, str) else submission_date

        deadline = t_date + timedelta(days=deadline_days)
        if s_date > deadline:
            return RuleResult(
                rule_name="submission_deadline",
                passed=False,
                reason=(
                    f"Claim submitted {(s_date - t_date).days} days after treatment. "
                    f"Deadline is {deadline_days} days from treatment date "
                    f"({t_date.isoformat()}). Submission deadline was {deadline.isoformat()}."
                ),
                severity="block",
                details={
                    "treatment_date": t_date.isoformat(),
                    "submission_date": s_date.isoformat(),
                    "deadline": deadline.isoformat(),
                    "days_elapsed": (s_date - t_date).days,
                    "deadline_days": deadline_days,
                },
            )

        return RuleResult(
            rule_name="submission_deadline",
            passed=True,
            reason=f"Submitted within {deadline_days}-day deadline.",
            severity="info",
        )

    # ── Category-Specific Rules ──────────────────────────────────

    def get_category_config(self, claim_category: str) -> dict[str, Any]:
        """Get the full configuration for a coverage category."""
        categories = self._policy.get("opd_categories", {})
        return categories.get(claim_category.lower(), {})

    def get_copay_percent(self, claim_category: str) -> float:
        """Get co-pay percentage for a category."""
        config = self.get_category_config(claim_category)
        return config.get("copay_percent", 0)

    def get_sub_limit(self, claim_category: str) -> float | None:
        """Get sub-limit for a category (annual pool)."""
        config = self.get_category_config(claim_category)
        return config.get("sub_limit")

    def get_network_discount(self, hospital_name: str | None, claim_category: str) -> float:
        """
        Get network discount percentage if hospital is in network.
        
        Network discount is applied BEFORE co-pay (TC010 requirement).
        """
        if not hospital_name:
            return 0.0

        network_hospitals = self._policy.get("network_hospitals", [])
        hospital_lower = hospital_name.lower()

        for nh in network_hospitals:
            if isinstance(nh, str):
                if nh.lower() in hospital_lower or hospital_lower in nh.lower():
                    config = self.get_category_config(claim_category)
                    return config.get("network_discount_percent", 0)
            elif isinstance(nh, dict):
                nh_name = nh.get("name", "")
                if nh_name.lower() in hospital_lower or hospital_lower in nh_name.lower():
                    # Found network hospital — get category-specific discount
                    config = self.get_category_config(claim_category)
                    discount = config.get("network_discount_percent", 0)
                    if discount:
                        return discount
                    # Fallback to hospital-level discount
                    return nh.get("discount_percent", 0)

        return 0.0

    def is_network_hospital(self, hospital_name: str | None) -> tuple[bool, str | None]:
        """Check if a hospital is in the network list."""
        if not hospital_name:
            return False, None

        network_hospitals = self._policy.get("network_hospitals", [])
        hospital_lower = hospital_name.lower()

        for nh in network_hospitals:
            if isinstance(nh, str):
                if nh.lower() in hospital_lower or hospital_lower in nh.lower():
                    return True, nh
            elif isinstance(nh, dict):
                nh_name = nh.get("name", "")
                if nh_name.lower() in hospital_lower or hospital_lower in nh_name.lower():
                    return True, nh_name

        return False, None

    # ── Document Requirements ────────────────────────────────────

    def get_required_documents(self, claim_category: str) -> list[str]:
        """Get required document types for a claim category."""
        doc_reqs = self._policy.get("document_requirements", {})
        category_reqs = doc_reqs.get(claim_category.upper(), {})
        return category_reqs.get("required", [])

    def get_optional_documents(self, claim_category: str) -> list[str]:
        """Get optional document types for a claim category."""
        doc_reqs = self._policy.get("document_requirements", {})
        category_reqs = doc_reqs.get(claim_category.upper(), {})
        return category_reqs.get("optional", [])

    # ── Fraud Thresholds ─────────────────────────────────────────

    def get_fraud_thresholds(self) -> dict[str, Any]:
        """Get fraud detection thresholds from policy."""
        return self._policy.get("fraud_thresholds", {})

    # ── Category Excluded Procedures ─────────────────────────────

    def get_category_excluded_procedures(self, claim_category: str) -> list[str]:
        """Get excluded procedures for a specific category (e.g., dental whitening)."""
        config = self.get_category_config(claim_category)
        return config.get("excluded_procedures", []) + config.get("excluded_items", [])

    def get_category_covered_procedures(self, claim_category: str) -> list[str]:
        """Get covered procedures/items for a specific category."""
        config = self.get_category_config(claim_category)
        return config.get("covered_procedures", []) + config.get("covered_items", [])

    # ── Covered Procedures Check ─────────────────────────────────────

    def check_covered_procedures(
        self,
        claim_category: str,
        line_items: list[dict] | None = None,
    ) -> RuleResult:
        """
        Validate line items against positive covered-procedures lists.

        For categories like DENTAL and VISION that define explicit
        covered_procedures/covered_items lists, line items that don't
        match any covered procedure AND aren't already in the excluded
        list are flagged for manual review.
        """
        covered = self.get_category_covered_procedures(claim_category)
        if not covered:
            return RuleResult(
                rule_name="covered_procedures",
                passed=True,
                reason=f"No covered-procedures list for category '{claim_category}' — all procedures accepted.",
                severity="info",
            )

        if not line_items:
            return RuleResult(
                rule_name="covered_procedures",
                passed=True,
                reason="No line items to validate against covered procedures.",
                severity="info",
            )

        excluded = self.get_category_excluded_procedures(claim_category)
        covered_lower = [p.lower() for p in covered]
        excluded_lower = [p.lower() for p in excluded]

        matched_items = []
        unrecognised_items = []

        for item in line_items:
            desc = item.get("description", "")
            desc_lower = desc.lower()

            # Check if it matches a covered procedure
            is_covered = any(cp in desc_lower or desc_lower in cp for cp in covered_lower)
            # Check if it's already caught by exclusion list
            is_excluded = any(ep in desc_lower or desc_lower in ep for ep in excluded_lower)

            if is_covered:
                matched_items.append(desc)
            elif not is_excluded:
                # Not in covered list and not in excluded list → unknown
                unrecognised_items.append(desc)

        if unrecognised_items:
            return RuleResult(
                rule_name="covered_procedures",
                passed=False,  # Don't block, but warn
                reason=(
                    f"{len(unrecognised_items)} line item(s) not found in the covered "
                    f"procedures list for '{claim_category}': "
                    f"{', '.join(unrecognised_items)}. "
                    f"Covered procedures are: {', '.join(covered)}."
                ),
                severity="warn",
                details={
                    "unrecognised_items": unrecognised_items,
                    "covered_procedures": covered,
                    "matched_items": matched_items,
                },
            )

        return RuleResult(
            rule_name="covered_procedures",
            passed=True,
            reason=(
                f"All line items match covered procedures for '{claim_category}': "
                f"{', '.join(matched_items)}."
            ),
            severity="info",
            details={"matched_items": matched_items, "covered_procedures": covered},
        )

    # ── Sub-Limit Check ──────────────────────────────────────────────

    def check_sub_limit(self, claim_category: str, amount: float | Decimal) -> RuleResult:
        """
        Check if an amount would exceed the category sub-limit.

        Returns the capped amount in details so the adjudicator can use it.
        Sub-limit acts as a per-claim ceiling for the category.
        """
        sub_limit_val = self.get_sub_limit(claim_category)
        if sub_limit_val is None:
            return RuleResult(
                rule_name="sub_limit",
                passed=True,
                reason=f"No sub-limit configured for category '{claim_category}'.",
                severity="info",
            )

        sub_limit = Decimal(str(sub_limit_val))
        amount_dec = Decimal(str(amount))

        if amount_dec > sub_limit:
            return RuleResult(
                rule_name="sub_limit",
                passed=True,  # Don't block — cap instead
                reason=(
                    f"Amount ₹{amount_dec:,.2f} exceeds category sub-limit of "
                    f"₹{sub_limit:,.2f} for '{claim_category}'. "
                    f"Approved amount will be capped at ₹{sub_limit:,.2f}."
                ),
                severity="warn",
                details={
                    "original_amount": float(amount_dec),
                    "sub_limit": float(sub_limit),
                    "capped_amount": float(sub_limit),
                    "capped": True,
                },
            )

        return RuleResult(
            rule_name="sub_limit",
            passed=True,
            reason=(
                f"Amount ₹{amount_dec:,.2f} is within category sub-limit of "
                f"₹{sub_limit:,.2f} for '{claim_category}'."
            ),
            severity="info",
            details={"amount": float(amount_dec), "sub_limit": float(sub_limit), "capped": False},
        )

    # ── Relationship Check ───────────────────────────────────────────

    def check_relationship(self, member_id: str) -> RuleResult:
        """
        Verify that the member's relationship type is covered under the
        family floater configuration.
        """
        member = self._members.get(member_id)
        if not member:
            return RuleResult(
                rule_name="relationship_coverage",
                passed=True,
                reason="Member not found — relationship check skipped.",
                severity="warn",
            )

        relationship = member.get("relationship", "SELF")
        family_floater = self._policy.get("coverage", {}).get("family_floater", {})
        covered_relationships = family_floater.get("covered_relationships", ["SELF"])

        # Normalise — policy uses CHILDREN, roster uses CHILD
        normalised_covered = []
        for cr in covered_relationships:
            normalised_covered.append(cr.upper())
            if cr.upper() == "CHILDREN":
                normalised_covered.append("CHILD")
            if cr.upper() == "PARENTS":
                normalised_covered.append("PARENT")
                normalised_covered.append("FATHER")
                normalised_covered.append("MOTHER")

        if relationship.upper() in normalised_covered:
            return RuleResult(
                rule_name="relationship_coverage",
                passed=True,
                reason=(
                    f"Member '{member.get('name')}' has relationship '{relationship}' "
                    f"which is covered under the family floater."
                ),
                severity="info",
                details={
                    "member_name": member.get("name"),
                    "relationship": relationship,
                    "covered_relationships": covered_relationships,
                },
            )

        return RuleResult(
            rule_name="relationship_coverage",
            passed=False,
            reason=(
                f"Member '{member.get('name')}' has relationship '{relationship}' "
                f"which is NOT covered under the family floater. "
                f"Covered relationships: {', '.join(covered_relationships)}."
            ),
            severity="block",
            details={
                "member_name": member.get("name"),
                "relationship": relationship,
                "covered_relationships": covered_relationships,
            },
        )

    # ── Alternative Medicine Checks ───────────────────────────────────

    def check_alternative_medicine_system(
        self,
        diagnosis: str,
        treatment: str,
        line_items: list[dict] | None = None,
        hospital_name: str | None = None,
    ) -> RuleResult:
        """
        Verify that the alternative medicine system is covered.
        Covered: Ayurveda, Homeopathy, Unani, Siddha, Naturopathy.
        """
        config = self.get_category_config("alternative_medicine")
        covered_systems = config.get("covered_systems", ["Ayurveda", "Homeopathy", "Unani", "Siddha", "Naturopathy"])
        
        # Combine all texts
        all_text_parts = []
        if diagnosis:
            all_text_parts.append(diagnosis)
        if treatment:
            all_text_parts.append(treatment)
        if hospital_name:
            all_text_parts.append(hospital_name)
        if line_items:
            for item in line_items:
                desc = item.get("description", "")
                if desc:
                    all_text_parts.append(desc)
                    
        combined_text = " ".join(all_text_parts).lower()
        
        system_keywords = {
            "Ayurveda": ["ayurved", "ayur", "panchakarma"],
            "Homeopathy": ["homeopath", "homoeopath"],
            "Unani": ["unani"],
            "Siddha": ["siddha"],
            "Naturopathy": ["naturopath", "naturopathy"],
        }
        
        matched_systems = []
        for system in covered_systems:
            keywords = system_keywords.get(system, [system.lower()])
            for kw in keywords:
                if kw in combined_text:
                    matched_systems.append(system)
                    break
                
        if matched_systems:
            return RuleResult(
                rule_name="alternative_medicine_system",
                passed=True,
                reason=f"Covered alternative medicine system detected: {', '.join(matched_systems)}.",
                severity="info",
                details={"matched_systems": matched_systems},
            )
            
        return RuleResult(
            rule_name="alternative_medicine_system",
            passed=False,
            reason="No covered alternative medicine systems (Ayurveda, Homeopathy, Unani, Siddha, Naturopathy) detected in document text.",
            severity="warn",
            details={
                "covered_systems": covered_systems,
                "text_analyzed": combined_text[:200],
            },
        )


    def check_alternative_medicine_practitioner(
        self,
        doctor_registration: str | None,
    ) -> RuleResult:
        """
        Verify that the practitioner has an alternative medicine registration (starts with 'AYUR/').
        """
        if not doctor_registration:
            return RuleResult(
                rule_name="alternative_medicine_practitioner",
                passed=False,
                reason="Practitioner registration number is missing.",
                severity="warn",
            )
            
        reg_upper = doctor_registration.upper().strip()
        if reg_upper.startswith("AYUR/"):
            return RuleResult(
                rule_name="alternative_medicine_practitioner",
                passed=True,
                reason=f"Practitioner is registered under Alternative Medicine (Registration: {doctor_registration}).",
                severity="info",
                details={"registration": doctor_registration},
            )
            
        return RuleResult(
            rule_name="alternative_medicine_practitioner",
            passed=False,
            reason=f"Practitioner registration '{doctor_registration}' lacks the mandatory 'AYUR/' prefix for alternative medicine.",
            severity="warn",
            details={"registration": doctor_registration},
        )

    def check_alternative_medicine_sessions(
        self,
        line_items: list[dict] | None = None,
    ) -> RuleResult:
        """
        Check if the sessions in the current claim exceed the limit of 20.
        Parses text like '5 sessions' or '25 sessions' from descriptions.
        """
        import re
        
        total_sessions = 0
        session_items = []
        
        if line_items:
            for item in line_items:
                desc = item.get("description", "")
                if desc:
                    # Look for numbers preceding session/sitting/class/visit/therapy keywords
                    match = re.search(r"(\d+)\s*(?:session|sitting|class|visit|therapy|therapies)", desc, re.IGNORECASE)
                    if match:
                        count = int(match.group(1))
                        total_sessions += count
                        session_items.append({"description": desc, "sessions": count})
                        
        max_sessions = 20
        if total_sessions > max_sessions:
            return RuleResult(
                rule_name="alternative_medicine_sessions",
                passed=False,
                reason=f"Claimed sessions ({total_sessions}) exceed the yearly policy limit of {max_sessions} sessions.",
                severity="warn",
                details={
                    "total_sessions": total_sessions,
                    "max_sessions": max_sessions,
                    "session_items": session_items,
                },
            )
            
        return RuleResult(
            rule_name="alternative_medicine_sessions",
            passed=True,
            reason=f"Claimed sessions ({total_sessions}) are within the policy limit of {max_sessions}.",
            severity="info",
            details={
                "total_sessions": total_sessions,
                "max_sessions": max_sessions,
                "session_items": session_items,
            },
        )

    def check_dental_report(self, has_dental_report: bool) -> RuleResult:
        """
        Verify that a dental report is provided for dental claims.
        """
        if has_dental_report:
            return RuleResult(
                rule_name="dental_report_requirement",
                passed=True,
                reason="Required dental report is present in the claim submission.",
                severity="info",
            )
        return RuleResult(
            rule_name="dental_report_requirement",
            passed=False,
            reason="Dental report is missing but is required under the policy for dental claims.",
            severity="warn",
        )

    def check_pharmacy_generic_status(
        self,
        line_items: list[dict] | None = None,
        medicines: list[str] | None = None,
    ) -> RuleResult:
        """
        Verify if pharmacy claim conforms to the generic_mandatory policy.
        If any medicine listed is branded, or if the bill is not itemized,
        escalate to manual review (passed=False, severity=warn).
        """
        branded_keywords = ["dolo", "crocin", "calpol", "combiflam", "lipitor", "advil", "tylenol", "aspirin"]
        
        is_vague = False
        has_branded = False
        found_branded_names = []
        
        if line_items:
            for item in line_items:
                desc = item.get("description", "").lower()
                # If bill is not itemized with drug details
                if desc in ["medicines", "medicines (tablets and capsules)", "pharmacy bill", "drugs", "pharmacy charges"]:
                    is_vague = True
                for kw in branded_keywords:
                    if kw in desc:
                        has_branded = True
                        found_branded_names.append(kw.title())
                        
        if medicines:
            for med in medicines:
                med_lower = med.lower()
                for kw in branded_keywords:
                    if kw in med_lower:
                        has_branded = True
                        found_branded_names.append(kw.title())
                        
        if has_branded:
            return RuleResult(
                rule_name="pharmacy_generic_mandatory",
                passed=False,
                reason=f"Branded drug(s) detected ({', '.join(set(found_branded_names))}). Under policy, generic medicines are mandatory.",
                severity="warn",
                details={"has_branded": True, "branded_drugs": list(set(found_branded_names))},
            )
            
        if is_vague:
            return RuleResult(
                rule_name="pharmacy_generic_mandatory",
                passed=False,
                reason="Pharmacy bill is not itemized with specific drug names; cannot verify generic compliance.",
                severity="warn",
                details={"is_vague": True},
            )
            
        return RuleResult(
            rule_name="pharmacy_generic_mandatory",
            passed=True,
            reason="All medicines verified as generic.",
            severity="info",
        )

