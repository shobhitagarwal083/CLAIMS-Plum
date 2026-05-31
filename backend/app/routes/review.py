"""
Decision Gate — Human Review API Routes.

Endpoints for the human-in-the-loop review workflow.
When the pipeline flags a claim as MANUAL_REVIEW, it enters
the 'awaiting_review' state. These endpoints allow a human
reviewer to inspect the full trace and approve or deny.

Routes:
    GET  /api/reviews              — List all claims awaiting review
    GET  /api/reviews/{claim_id}   — Get full review detail for a claim
    POST /api/reviews/{claim_id}/action — Submit approve/deny action
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
from app.db.models import ClaimRecord
from app.models.claim import (
    ClaimDecisionOutput,
    ReviewActionRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reviews", tags=["Reviews"])


def _record_to_output(claim: ClaimRecord) -> ClaimDecisionOutput:
    """Convert a ClaimRecord ORM object to the API response model."""
    return ClaimDecisionOutput(
        claim_id=claim.id,
        member_id=claim.member_id,
        member_name=claim.member_name,
        policy_id=claim.policy_id,
        claim_category=claim.claim_category,
        status=claim.status,
        decision=claim.decision,
        approved_amount=claim.approved_amount,
        confidence_score=claim.confidence_score or 0.0,
        rejection_reasons=claim.rejection_reasons or [],
        decision_reasons=claim.decision_reasons or [],
        amount_breakdown=claim.amount_breakdown,
        document_issues=claim.document_issues or [],
        is_document_error=claim.is_document_error,
        fraud_signals=claim.fraud_signals or [],
        fraud_score=claim.fraud_score,
        degraded_components=claim.degraded_components or [],
        manual_review_recommended=claim.manual_review_recommended,
        review_action=claim.review_action,
        reviewed_by=claim.reviewed_by,
        reviewed_at=claim.reviewed_at,
        review_notes=claim.review_notes,
        pre_review_decision=claim.pre_review_decision,
        pre_review_approved_amount=claim.pre_review_approved_amount,
        processing_time_ms=claim.processing_time_ms,
        processed_at=claim.updated_at,
        execution_trace=claim.execution_trace or [],
    )


@router.get(
    "",
    summary="List claims awaiting human review",
    description="Returns all claims in 'awaiting_review' status for the Decision Gate queue.",
)
async def list_reviews(
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List all claims pending human review."""
    result = await db.execute(
        select(ClaimRecord)
        .where(ClaimRecord.status == "awaiting_review")
        .order_by(ClaimRecord.created_at.desc())
    )
    claims = result.scalars().all()

    return [
        {
            "claim_id": c.id,
            "member_id": c.member_id,
            "member_name": c.member_name,
            "claim_category": c.claim_category,
            "claimed_amount": c.claimed_amount,
            "decision": c.decision,
            "pre_review_decision": c.pre_review_decision,
            "pre_review_approved_amount": c.pre_review_approved_amount,
            "confidence_score": c.confidence_score,
            "fraud_score": c.fraud_score,
            "fraud_signals": c.fraud_signals or [],
            "decision_reasons": c.decision_reasons or [],
            "degraded_components": c.degraded_components or [],
            "status": c.status,
            "is_document_error": c.is_document_error,
            "processing_time_ms": c.processing_time_ms,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in claims
    ]


@router.get(
    "/{claim_id}",
    response_model=ClaimDecisionOutput,
    summary="Get review details for a claim",
    description="Get full claim details including trace for reviewer inspection.",
)
async def get_review(
    claim_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> ClaimDecisionOutput:
    """Get a specific claim for review with its full decision output and trace."""
    result = await db.execute(
        select(ClaimRecord).where(ClaimRecord.id == claim_id)
    )
    claim = result.scalar_one_or_none()

    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")

    return _record_to_output(claim)


@router.post(
    "/{claim_id}/action",
    response_model=ClaimDecisionOutput,
    summary="Submit review action",
    description=(
        "Approve or deny a claim awaiting human review. "
        "Approve restores the original adjudicator decision and amounts. "
        "Deny sets the decision to REJECTED with approved_amount=0."
    ),
)
async def submit_review_action(
    claim_id: str,
    body: ReviewActionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ClaimDecisionOutput:
    """Process a human reviewer's approve/deny action on a claim."""
    result = await db.execute(
        select(ClaimRecord).where(ClaimRecord.id == claim_id)
    )
    claim = result.scalar_one_or_none()

    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")

    if claim.status != "awaiting_review":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Claim '{claim_id}' is not awaiting review. "
                f"Current status: '{claim.status}'."
            ),
        )

    now = datetime.utcnow()

    # Record the review action
    claim.review_action = "approved" if body.action == "approve" else "denied"
    claim.reviewed_by = body.reviewed_by
    claim.reviewed_at = now
    claim.review_notes = body.notes

    if body.action == "approve":
        # Resolve approved amount
        app_amt = claim.pre_review_approved_amount or 0.0
        is_override = False
        if body.approved_amount is not None:
            if body.approved_amount > claim.claimed_amount:
                raise HTTPException(
                    status_code=400,
                    detail=f"Approved amount (₹{body.approved_amount}) cannot exceed the claimed amount (₹{claim.claimed_amount})."
                )
            app_amt = body.approved_amount
            is_override = True

        # Restore the original adjudicator decision and amounts
        claim.decision = claim.pre_review_decision or "APPROVED"
        claim.approved_amount = app_amt
        claim.status = "completed"
        # Update amount_breakdown if present
        if claim.amount_breakdown and isinstance(claim.amount_breakdown, dict):
            claim.amount_breakdown = {
                **claim.amount_breakdown,
                "approved_amount": app_amt,
            }
        # Append review note to decision reasons
        existing_reasons = claim.decision_reasons or []
        amt_str = f"with modified amount of ₹{app_amt}" if is_override else f"with calculated amount of ₹{app_amt}"
        existing_reasons.append(
            f"✅ Approved by human reviewer ({body.reviewed_by}) {amt_str}: {body.notes}"
        )
        claim.decision_reasons = existing_reasons

        logger.info(
            "Claim %s APPROVED by reviewer '%s' %s. Decision=%s",
            claim_id,
            body.reviewed_by,
            amt_str,
            claim.decision,
        )

    elif body.action == "deny":
        claim.decision = "REJECTED"
        claim.approved_amount = 0.0
        claim.status = "completed"
        # Update amount_breakdown if present
        if claim.amount_breakdown and isinstance(claim.amount_breakdown, dict):
            claim.amount_breakdown = {
                **claim.amount_breakdown,
                "approved_amount": 0.0,
            }
        # Append denial reason
        existing_reasons = claim.decision_reasons or []
        existing_reasons.append(
            f"❌ Denied by human reviewer ({body.reviewed_by}): {body.notes}"
        )
        claim.decision_reasons = existing_reasons
        # Add to rejection reasons
        existing_rejections = claim.rejection_reasons or []
        existing_rejections.append(
            f"Claim denied during human review: {body.notes}"
        )
        claim.rejection_reasons = existing_rejections

        logger.info(
            "Claim %s DENIED by reviewer '%s'. Reason: %s",
            claim_id,
            body.reviewed_by,
            body.notes,
        )

    claim.updated_at = now
    await db.commit()
    await db.refresh(claim)

    return _record_to_output(claim)
