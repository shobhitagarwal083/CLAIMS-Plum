"""
Agent 7: Fraud Detector.

Evaluates fraud signals based on claim patterns and policy thresholds.
Modeled after the SuperNodes fraud_detection_node.

Handles TC009: 4th same-day claim → MANUAL_REVIEW with specific signals.

Signals evaluated:
1. Same-day claims count vs threshold
2. Monthly claims frequency
3. High-value claim flag
4. Document alteration indicators

Design decision: This agent can OVERRIDE the adjudicator's decision
by escalating to MANUAL_REVIEW if fraud signals are detected. It never
downgrades a REJECTED decision — fraud checks only apply to claims
that would otherwise be approved.

For TC011: This is the agent that fails when simulate_component_failure=true.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from app.models.trace import CheckResult, CheckSeverity
from app.pipeline.claim_context import ClaimContext
from app.policy import PolicyRulesEngine

from .base import BaseAgent

logger = logging.getLogger(__name__)


class SimulatedComponentFailure(Exception):
    """Deliberately thrown when simulate_component_failure is True (TC011)."""
    pass


class FraudDetectorAgent(BaseAgent):
    """
    Evaluates fraud signals and may escalate to MANUAL_REVIEW.

    Input (from context):
        - context.claims_history (past claims for pattern detection)
        - context.claimed_amount
        - context.treatment_date
        - context.simulate_component_failure

    Checks produced:
        - same_day_claims: Counts claims on the same day
        - monthly_frequency: Monthly claim count check
        - high_value_flag: High-value claim threshold
        - fraud_score: Overall fraud risk score
    """

    def __init__(self, policy_engine: PolicyRulesEngine):
        self._policy = policy_engine

    @property
    def agent_name(self) -> str:
        return "Fraud Detector"

    @property
    def agent_type(self) -> str:
        return "fraud_detector"

    async def _execute(
        self,
        context: ClaimContext,
    ) -> tuple[list[CheckResult], float, dict[str, Any]]:
        # ── TC011: Simulated failure ─────────────────────────────
        if context.simulate_component_failure:
            raise SimulatedComponentFailure(
                "Simulated component failure for testing (TC011). "
                "The Fraud Detector was unable to complete analysis."
            )

        checks: list[CheckResult] = []
        from decimal import Decimal
        claimed_amount = Decimal(str(context.claimed_amount))
        thresholds = self._policy.get_fraud_thresholds()
        fraud_signals: list[str] = []
        fraud_score = 0.0

        # ── Signal 1: Same-Day Claims ────────────────────────────
        same_day_limit = thresholds.get("same_day_claims_limit", 2)
        same_day_count = self._count_same_day_claims(
            context.claims_history,
            context.treatment_date,
        )

        if same_day_count >= same_day_limit:
            fraud_score += 0.4
            signal = (
                f"SAME_DAY_CLAIMS: {same_day_count + 1} claims on {context.treatment_date} "
                f"(including this one). Threshold is {same_day_limit}."
            )
            fraud_signals.append(signal)

            checks.append(CheckResult(
                check_name="same_day_claims",
                passed=False,
                reason=(
                    f"Unusual activity: {same_day_count} previous claims found on the same day "
                    f"({context.treatment_date}), making this the {same_day_count + 1}th claim today. "
                    f"Policy threshold is {same_day_limit} claims per day. "
                    f"Previous claims: "
                    + ", ".join(
                        f"₹{h.get('amount', 0):,.0f} at {h.get('provider', 'unknown')}"
                        for h in context.claims_history
                        if h.get("date") == context.treatment_date
                    )
                ),
                severity=CheckSeverity.WARN,
                details={
                    "same_day_count": same_day_count + 1,
                    "threshold": same_day_limit,
                    "claims_on_day": [
                        h for h in context.claims_history
                        if h.get("date") == context.treatment_date
                    ],
                },
            ))
        else:
            checks.append(CheckResult(
                check_name="same_day_claims",
                passed=True,
                reason=(
                    f"Same-day claims within threshold: "
                    f"{same_day_count + 1}/{same_day_limit + 1} claims on {context.treatment_date}."
                ),
                severity=CheckSeverity.INFO,
            ))

        # ── Signal 2: Monthly Claims Frequency ───────────────────
        monthly_limit = thresholds.get("monthly_claims_limit", 6)

        # Filter claims history to same calendar month as treatment_date
        try:
            from datetime import datetime as _dt
            treatment_dt = _dt.strptime(context.treatment_date, "%Y-%m-%d")
            treatment_year = treatment_dt.year
            treatment_month = treatment_dt.month
            monthly_claims = []
            for h in context.claims_history:
                try:
                    h_dt = _dt.strptime(h.get("date", ""), "%Y-%m-%d")
                    if h_dt.year == treatment_year and h_dt.month == treatment_month:
                        monthly_claims.append(h)
                except (ValueError, TypeError):
                    pass  # Skip unparseable dates
            monthly_count = len(monthly_claims)
        except (ValueError, TypeError):
            # Fallback if treatment_date is unparseable
            monthly_count = len(context.claims_history)

        if monthly_count >= monthly_limit:
            fraud_score += 0.2
            signal = f"HIGH_FREQUENCY: {monthly_count + 1} claims this month (limit: {monthly_limit})."
            fraud_signals.append(signal)

            checks.append(CheckResult(
                check_name="monthly_frequency",
                passed=False,
                reason=f"High claim frequency: {monthly_count + 1} claims (threshold: {monthly_limit}).",
                severity=CheckSeverity.WARN,
                details={"monthly_count": monthly_count + 1, "threshold": monthly_limit},
            ))
        else:
            checks.append(CheckResult(
                check_name="monthly_frequency",
                passed=True,
                reason=f"Monthly frequency OK: {monthly_count + 1}/{monthly_limit} claims.",
                severity=CheckSeverity.INFO,
            ))

        # ── Signal 3: High-Value Claim ───────────────────────────
        high_value_threshold = Decimal(str(thresholds.get("high_value_claim_threshold", 25000)))
        auto_review_threshold = Decimal(str(thresholds.get("auto_manual_review_above", 25000)))

        if claimed_amount > high_value_threshold:
            fraud_score += 0.15
            signal = f"HIGH_VALUE: ₹{claimed_amount:,.0f} exceeds ₹{high_value_threshold:,.0f}."
            fraud_signals.append(signal)

            checks.append(CheckResult(
                check_name="high_value_flag",
                passed=False,
                reason=f"High-value claim: ₹{claimed_amount:,.0f} exceeds ₹{high_value_threshold:,.0f} threshold.",
                severity=CheckSeverity.WARN,
                details={
                    "claimed_amount": float(claimed_amount),
                    "threshold": float(high_value_threshold),
                },
            ))
        else:
            checks.append(CheckResult(
                check_name="high_value_flag",
                passed=True,
                reason=f"Claim amount ₹{claimed_amount:,.0f} is below high-value threshold.",
                severity=CheckSeverity.INFO,
            ))

        # Check if exceeds the direct manual review threshold
        if claimed_amount > auto_review_threshold:
            signal = f"AUTO_REVIEW_THRESHOLD: ₹{claimed_amount:,.0f} exceeds auto-manual review limit of ₹{auto_review_threshold:,.0f}."
            if signal not in fraud_signals:
                fraud_signals.append(signal)

        # ── Signal: Duplicate Claim Check ────────────────────────
        duplicate_claims = []
        for h in context.claims_history:
            if h.get("claim_id") == context.claim_id:
                continue
            h_category = h.get("claim_category")
            if (h.get("date") == context.treatment_date 
                and abs(Decimal(str(h.get("amount", 0))) - claimed_amount) < Decimal('0.01')
                and (h_category is None or h_category == context.claim_category)):
                status = h.get("status")
                decision = h.get("decision")
                # Exclude explicitly rejected or failed claims (status must be completed or awaiting_review, decision != REJECTED)
                if status in ("completed", "awaiting_review") and decision != "REJECTED":
                    duplicate_claims.append(h)

        if duplicate_claims:
            fraud_score += 0.8
            dup_id = duplicate_claims[0].get("claim_id")
            signal = f"DUPLICATE_CLAIM: Identical claim already exists (Claim ID: {dup_id}, Date: {context.treatment_date}, Amount: ₹{claimed_amount:,.2f})."
            fraud_signals.append(signal)

            checks.append(CheckResult(
                check_name="duplicate_claim",
                passed=False,
                reason=(
                    f"Duplicate claim detected: Another claim with the same treatment date ({context.treatment_date}) "
                    f"and claimed amount (₹{claimed_amount:,.2f}) exists in history "
                    f"(Claim ID: {dup_id}) and is not rejected."
                ),
                severity=CheckSeverity.WARN,
                details={
                    "duplicate_claim_id": dup_id,
                    "date": context.treatment_date,
                    "amount": float(claimed_amount),
                },
            ))
        else:
            checks.append(CheckResult(
                check_name="duplicate_claim",
                passed=True,
                reason="No duplicate claims found in history.",
                severity=CheckSeverity.INFO,
            ))

        # ── Signal 4: Document Quality Concerns ──────────────────
        poor_quality_docs = [
            d for d in context.classified_documents
            if d.quality_score < 0.5
        ]
        if poor_quality_docs:
            fraud_score += 0.1
            signal = f"DOC_QUALITY: {len(poor_quality_docs)} document(s) with low quality."
            fraud_signals.append(signal)

            checks.append(CheckResult(
                check_name="document_quality_concern",
                passed=True,  # Warn but don't block
                reason=f"{len(poor_quality_docs)} document(s) have quality concerns.",
                severity=CheckSeverity.WARN,
            ))

        # ── Overall Fraud Assessment ─────────────────────────────
        fraud_threshold = thresholds.get("fraud_score_manual_review_threshold", 0.80)
        
        # Determine recommendation
        recommend_review = (
            fraud_score >= fraud_threshold 
            or fraud_score >= 0.3 
            or claimed_amount > auto_review_threshold
        )
        
        if recommend_review:
            context.overall_confidence = round(1.0 - fraud_score, 4)

        checks.append(CheckResult(
            check_name="fraud_assessment",
            passed=not recommend_review,
            reason=(
                f"Fraud score: {fraud_score:.2f}. "
                + (
                    f"MANUAL REVIEW RECOMMENDED due to {len(fraud_signals)} signal(s): "
                    + "; ".join(fraud_signals)
                    if recommend_review
                    else "No significant fraud indicators detected."
                )
            ),
            severity=CheckSeverity.WARN if recommend_review else CheckSeverity.INFO,
            details={
                "fraud_score": round(fraud_score, 3),
                "signals": fraud_signals,
                "recommend_review": recommend_review,
                "threshold": fraud_threshold,
            },
        ))

        confidence = 1.0 - fraud_score  # Lower confidence when more fraud signals

        output_summary = {
            "fraud_score": round(fraud_score, 3),
            "signals_triggered": len(fraud_signals),
            "signals": fraud_signals,
            "recommend_review": recommend_review,
        }

        return checks, round(max(confidence, 0.1), 3), output_summary

    @staticmethod
    def _count_same_day_claims(
        claims_history: list[dict[str, Any]],
        treatment_date: str,
    ) -> int:
        """Count claims on the same day as treatment_date."""
        count = 0
        for claim in claims_history:
            if claim.get("date") == treatment_date:
                count += 1
        return count
