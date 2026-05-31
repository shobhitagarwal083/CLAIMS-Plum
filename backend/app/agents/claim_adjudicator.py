"""
Agent 6: Claim Adjudicator.

Makes the final claim decision and calculates the approved amount.
Reads checks from all previous agents (especially Agent 5 Policy Evaluator)
to determine APPROVED / PARTIAL / REJECTED / MANUAL_REVIEW.

Financial calculation order (critical for TC010):
1. Start with claimed amount (or eligible amount after exclusions)
2. Apply network discount FIRST (if network hospital)
3. Apply co-pay AFTER discount
4. Cap at sub-limit if applicable
5. Result = approved amount

Handles:
- TC004: Full approval (₹1,500 → 10% co-pay → ₹1,350)
- TC006: Partial approval (excluded items removed, ₹8,000 approved)
- TC008: Hard rejection (per-claim limit exceeded)
- TC010: Network discount + co-pay order (₹4,500 → 20% discount → 10% co-pay → ₹3,240)
- TC012: Full rejection (excluded condition)
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.models.claim import (
    AmountBreakdown,
    ClaimDecision,
    LineItemDecision,
)
from app.models.trace import AgentTraceEntry, CheckResult, CheckSeverity
from app.pipeline.claim_context import ClaimContext
from app.policy import PolicyRulesEngine

from .base import BaseAgent

logger = logging.getLogger(__name__)


class ClaimAdjudicatorAgent(BaseAgent):
    """
    Calculates approved amount and makes the final claim decision.

    Input (from context):
        - All agent traces (especially policy evaluator checks)
        - context.claimed_amount, claim_category, hospital_name
        - context.extracted_line_items

    Output (does NOT write to context — returns via checks/output_summary):
        The pipeline executor reads the output_summary to build the decision.
    """

    def __init__(self, policy_engine: PolicyRulesEngine):
        self._policy = policy_engine

    @property
    def agent_name(self) -> str:
        return "Claim Adjudicator"

    @property
    def agent_type(self) -> str:
        return "claim_adjudicator"

    async def _execute(
        self,
        context: ClaimContext,
    ) -> tuple[list[CheckResult], float, dict[str, Any]]:
        checks: list[CheckResult] = []
        claimed_amount = Decimal(str(context.claimed_amount))

        # ── Step 1: Check for blocking failures from previous agents ─
        blocking_checks = self._get_blocking_failures(context)

        if blocking_checks:
            # Determine if it's a single-reason rejection
            rejection_reasons = [c.check_name.upper() for c in blocking_checks]
            
            # Map common check names to expected rejection reason codes
            reason_map = {
                "per_claim_limit": "PER_CLAIM_EXCEEDED",
                "waiting_period": "WAITING_PERIOD",
                "exclusion_check": "EXCLUDED_CONDITION",
                "pre_authorization": "PRE_AUTH_MISSING",
                "annual_limit": "ANNUAL_LIMIT_EXCEEDED",
                "member_eligibility": "MEMBER_NOT_ELIGIBLE",
                "minimum_claim_amount": "BELOW_MINIMUM",
                "submission_deadline": "SUBMISSION_LATE",
                "category_coverage": "CATEGORY_NOT_COVERED",
            }
            
            mapped_reasons = []
            for c in blocking_checks:
                mapped = reason_map.get(c.check_name, c.check_name.upper())
                mapped_reasons.append(mapped)

            checks.append(CheckResult(
                check_name="adjudication_decision",
                passed=False,
                reason=(
                    f"Claim REJECTED due to {len(blocking_checks)} policy violation(s): "
                    + "; ".join(c.reason for c in blocking_checks)
                ),
                severity=CheckSeverity.BLOCK,
                details={
                    "decision": "REJECTED",
                    "rejection_reasons": mapped_reasons,
                    "blocking_checks": [c.check_name for c in blocking_checks],
                },
            ))

            output = {
                "decision": ClaimDecision.REJECTED.value,
                "approved_amount": Decimal('0.00'),
                "rejection_reasons": mapped_reasons,
                "decision_reasons": [c.reason for c in blocking_checks],
                "amount_breakdown": AmountBreakdown(
                    claimed_amount=claimed_amount,
                ).model_dump(),
            }
            return checks, 0.95, output  # High confidence in rejection

        # ── Step 2: Check for partial exclusions (TC006) ─────────
        from app.models.claim import DocumentType
        
        bill_totals = []
        for doc in context.parsed_documents:
            if doc.document_type in (DocumentType.HOSPITAL_BILL, DocumentType.PHARMACY_BILL):
                val = doc.extracted_data.get("total")
                if val is not None:
                    try:
                        bill_totals.append(Decimal(str(val)))
                    except (ValueError, TypeError):
                        pass

        total_bill_amount = sum(bill_totals) if bill_totals else None
        
        partial_info = self._check_partial_exclusions(context)
        
        if total_bill_amount is not None:
            eligible_amount = min(claimed_amount, total_bill_amount)
        else:
            eligible_amount = claimed_amount
            
        line_item_decisions: list[LineItemDecision] = []
        is_partial = False

        if partial_info:
            excluded_items = partial_info.get("excluded_items", [])
            covered_items = partial_info.get("covered_items", [])
            
            if excluded_items:
                is_partial = True
                excluded_amount = sum(Decimal(str(i.get("amount", 0))) for i in excluded_items)
                covered_amount = sum(Decimal(str(i.get("amount", 0))) for i in covered_items)
                eligible_amount = covered_amount

                for item in covered_items:
                    line_item_decisions.append(LineItemDecision(
                        description=item["description"],
                        amount=Decimal(str(item.get("amount", 0))),
                        approved=True,
                        reason="Covered under policy.",
                    ))
                for item in excluded_items:
                    line_item_decisions.append(LineItemDecision(
                        description=item["description"],
                        amount=Decimal(str(item.get("amount", 0))),
                        approved=False,
                        reason=f"Excluded: {item.get('exclusion_reason', 'policy exclusion')}.",
                    ))

                checks.append(CheckResult(
                    check_name="partial_exclusion",
                    passed=True,
                    reason=(
                        f"Partial approval: {len(covered_items)} items covered (₹{covered_amount:,.2f}), "
                        f"{len(excluded_items)} items excluded (₹{excluded_amount:,.2f})."
                    ),
                    severity=CheckSeverity.WARN,
                    details={
                        "covered_amount": float(covered_amount),
                        "excluded_amount": float(excluded_amount),
                        "line_items": [lid.model_dump() for lid in line_item_decisions],
                    },
                ))

        # ── Step 3: Apply Network Discount (BEFORE co-pay) ──────
        network_discount_pct = self._policy.get_network_discount(
            context.hospital_name, context.claim_category
        )
        network_discount_pct = Decimal(str(network_discount_pct))
        network_discount_amount = Decimal('0.00')
        amount_after_discount = eligible_amount

        if network_discount_pct > 0:
            network_discount_amount = (eligible_amount * network_discount_pct / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            amount_after_discount = eligible_amount - network_discount_amount

            checks.append(CheckResult(
                check_name="network_discount",
                passed=True,
                reason=(
                    f"Network hospital discount of {network_discount_pct}% applied: "
                    f"₹{eligible_amount:,.2f} - ₹{network_discount_amount:,.2f} = ₹{amount_after_discount:,.2f}."
                ),
                severity=CheckSeverity.INFO,
                details={
                    "hospital": context.hospital_name,
                    "discount_percent": float(network_discount_pct),
                    "discount_amount": float(network_discount_amount),
                    "amount_after": float(amount_after_discount),
                },
            ))

        # ── Step 4: Apply Co-pay (AFTER discount) ────────────────
        copay_pct = self._policy.get_copay_percent(context.claim_category)
        copay_pct = Decimal(str(copay_pct))
        copay_amount = Decimal('0.00')
        amount_after_copay = amount_after_discount

        if copay_pct > 0:
            copay_amount = (amount_after_discount * copay_pct / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            amount_after_copay = amount_after_discount - copay_amount

            checks.append(CheckResult(
                check_name="copay_applied",
                passed=True,
                reason=(
                    f"Co-pay of {copay_pct}% applied on ₹{amount_after_discount:,.2f}: "
                    f"₹{copay_amount:,.2f} deducted. Remaining: ₹{amount_after_copay:,.2f}."
                ),
                severity=CheckSeverity.INFO,
                details={
                    "copay_percent": float(copay_pct),
                    "copay_amount": float(copay_amount),
                    "amount_before": float(amount_after_discount),
                    "amount_after": float(amount_after_copay),
                },
            ))

        # ── Step 5: Apply Sub-limit Cap ──────────────────────────
        sub_limit = self._policy.get_sub_limit(context.claim_category)
        sub_limit_dec = Decimal(str(sub_limit)) if sub_limit is not None else None
        sub_limit_applied = False
        approved_amount = amount_after_copay

        # Enforce category sub-limit cap using the rules engine
        sub_limit_result = self._policy.check_sub_limit(
            context.claim_category, approved_amount,
        )
        if sub_limit_result.details and sub_limit_result.details.get("capped"):
            approved_amount = Decimal(str(sub_limit_result.details["capped_amount"]))
            sub_limit_applied = True
            checks.append(CheckResult(
                check_name="sub_limit_cap",
                passed=True,
                reason=(
                    f"Approved amount capped from ₹{amount_after_copay:,.2f} to "
                    f"₹{approved_amount:,.2f} due to category sub-limit of "
                    f"₹{sub_limit_dec:,.2f} for '{context.claim_category}'."
                ),
                severity=CheckSeverity.WARN,
                details={
                    "original_amount": float(amount_after_copay),
                    "sub_limit": float(sub_limit_dec) if sub_limit_dec is not None else None,
                    "capped_amount": float(approved_amount),
                    "capped": True,
                },
            ))
        else:
            checks.append(CheckResult(
                check_name="sub_limit_cap",
                passed=True,
                reason=sub_limit_result.reason,
                severity=CheckSeverity.INFO,
            ))

        # ── Step 6: Final Decision ───────────────────────────────
        approved_amount = approved_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        # Determine decision
        if is_partial:
            decision = ClaimDecision.PARTIAL
        elif approved_amount > 0:
            decision = ClaimDecision.APPROVED
        else:
            decision = ClaimDecision.REJECTED

        # Build amount breakdown
        breakdown = AmountBreakdown(
            claimed_amount=claimed_amount,
            eligible_amount=eligible_amount,
            network_discount_percent=network_discount_pct,
            network_discount_amount=network_discount_amount,
            amount_after_discount=amount_after_discount,
            copay_percent=copay_pct,
            copay_amount=copay_amount,
            sub_limit=sub_limit_dec,
            sub_limit_applied=sub_limit_applied,
            approved_amount=approved_amount,
            line_items=line_item_decisions,
        )

        # Decision check
        checks.append(CheckResult(
            check_name="final_decision",
            passed=True,
            reason=(
                f"Decision: {decision.value}. "
                f"Approved amount: ₹{approved_amount:,.2f} "
                f"(from claimed ₹{claimed_amount:,.2f})."
            ),
            severity=CheckSeverity.INFO,
            details={
                "decision": decision.value,
                "approved_amount": float(approved_amount),
                "breakdown": breakdown.model_dump(),
            },
        ))

        # Confidence based on pipeline health
        confidence = context.overall_confidence
        if decision == ClaimDecision.APPROVED:
            confidence = max(confidence, 0.85)
        elif decision == ClaimDecision.PARTIAL:
            confidence = max(confidence * 0.9, 0.75)

        output = {
            "decision": decision.value,
            "approved_amount": approved_amount,
            "rejection_reasons": [],
            "decision_reasons": [
                f"{'Network discount' if network_discount_pct else 'No discount'}, "
                f"co-pay {copay_pct}%, approved ₹{approved_amount:,.2f}"
            ],
            "amount_breakdown": breakdown.model_dump(),
            "line_items": [lid.model_dump() for lid in line_item_decisions],
        }

        return checks, round(confidence, 3), output

    def _get_blocking_failures(self, context: ClaimContext) -> list[CheckResult]:
        """Collect all blocking failures from previous agent traces."""
        blocking = []
        for trace in context.agent_traces:
            for check in trace.checks:
                if check.is_blocking:
                    blocking.append(check)
        return blocking

    def _check_partial_exclusions(self, context: ClaimContext) -> dict | None:
        """Find partial exclusion info from policy evaluator checks."""
        for trace in context.agent_traces:
            if trace.agent_type == "policy_evaluator":
                for check in trace.checks:
                    if check.check_name == "exclusion_check" and check.details.get("is_partial"):
                        return check.details
        return None
