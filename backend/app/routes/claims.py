"""
Claims API Routes.

Primary endpoints for claim submission and retrieval.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
from app.db.models import ClaimRecord
from app.models.claim import ClaimDecisionOutput, ClaimSubmissionRequest
from app.services.claim_service import ClaimService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/claims", tags=["Claims"])


def get_claim_service() -> ClaimService:
    """Get the singleton ClaimService from app state."""
    from app.main import get_app_service
    return get_app_service()


def _record_to_output(claim: ClaimRecord) -> ClaimDecisionOutput:
    """Convert a ClaimRecord ORM object to the API response model."""
    from app.models.claim import ClaimDecision
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


@router.post(
    "",
    response_model=ClaimDecisionOutput,
    status_code=202,
    summary="Submit a new claim",
    description="Submit a health insurance claim for automated processing.",
)
async def submit_claim(
    request: ClaimSubmissionRequest,
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
    db: AsyncSession = Depends(get_db_session),
) -> ClaimDecisionOutput:
    """
    Submit a claim for asynchronous background processing.
    
    Creates a database record in PENDING state and enqueues a Celery task.
    """
    import uuid
    from app.tasks.worker import process_claim_task
    import redis.asyncio as aioredis
    from app.config import get_settings

    try:
        service = get_claim_service()
        claim_id = str(uuid.uuid4())

        # ── Idempotency Key Check ────────────────────────
        if x_idempotency_key:
            settings = get_settings()
            redis_client = aioredis.from_url(settings.redis_url)
            try:
                # Try to set the key in Redis (NX = True, EX = 24 hours)
                acquired = await redis_client.set(
                    f"idempotency:{x_idempotency_key}",
                    claim_id,
                    nx=True,
                    ex=86400,
                )
                if not acquired:
                    # Key already exists! Fetch the existing claim ID
                    val = await redis_client.get(f"idempotency:{x_idempotency_key}")
                    if val:
                        existing_claim_id = val.decode("utf-8") if isinstance(val, bytes) else val
                        logger.info("Idempotency key hit for %s -> returning existing claim %s", x_idempotency_key, existing_claim_id)
                        # Fetch the existing claim from DB
                        result = await db.execute(select(ClaimRecord).where(ClaimRecord.id == existing_claim_id))
                        existing_record = result.scalar_one_or_none()
                        if existing_record:
                            return _record_to_output(existing_record)
            except Exception as redis_err:
                logger.error("Failed to check/set idempotency key in Redis: %s", redis_err)
            finally:
                await redis_client.aclose()

        # Resolve member name from roster
        member = service._pipeline._policy.get_member(request.member_id)
        member_name = member.get("name") if member else None

        # 1. Create a database record in PENDING state
        record = ClaimRecord(
            id=claim_id,
            member_id=request.member_id,
            member_name=member_name,
            policy_id=request.policy_id,
            claim_category=request.claim_category,
            claimed_amount=request.claimed_amount,
            treatment_date=request.treatment_date,
            hospital_name=request.hospital_name,
            status="pending",
        )
        db.add(record)
        await db.commit()

        # Save documents' base64_data to disk to optimize Celery payload size
        from pathlib import Path
        import base64
        from app.config import get_settings

        settings = get_settings()
        upload_dir = Path(settings.upload_dir) / claim_id
        upload_dir.mkdir(parents=True, exist_ok=True)

        payload = request.model_dump(mode="json")
        for doc in payload.get("documents", []):
            base64_data = doc.get("base64_data")
            if base64_data:
                try:
                    file_id = doc.get("file_id") or str(uuid.uuid4())[:8]
                    file_name = doc.get("file_name") or "file"
                    # Sanitized/safe filename format: {file_id}_{file_name}
                    safe_name = f"{file_id}_{Path(file_name).name}"
                    file_path = upload_dir / safe_name
                    
                    # Strip mime prefix if present in base64
                    if "," in base64_data:
                        base64_data = base64_data.split(",", 1)[1]
                    file_bytes = base64.b64decode(base64_data)
                    file_path.write_bytes(file_bytes)
                    
                    doc["file_path"] = str(file_path.resolve())
                    doc["base64_data"] = None
                except Exception as save_err:
                    logger.error("Failed to save document to disk: %s", save_err)

        # 2. Dispatch background task
        process_claim_task.delay(claim_id, payload)

        # 3. Return initial pending shell
        return ClaimDecisionOutput(
            claim_id=claim_id,
            member_id=request.member_id,
            member_name=member_name,
            policy_id=request.policy_id,
            claim_category=request.claim_category,
            status="pending",
            decision=None,
            approved_amount=0.0,
            confidence_score=0.0,
        )
    except Exception as exc:
        logger.exception("Failed to submit claim: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Claim submission failed: {str(exc)}",
        )


@router.get(
    "",
    summary="List all claims",
    description="Retrieve all processed claims.",
)
async def list_claims(
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List all claims with their decisions."""
    result = await db.execute(
        select(ClaimRecord).order_by(ClaimRecord.created_at.desc())
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
            "approved_amount": c.approved_amount,
            "confidence_score": c.confidence_score,
            "status": c.status,
            "is_document_error": c.is_document_error,
            "processing_time_ms": c.processing_time_ms,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "review_action": c.review_action,
            "pre_review_decision": c.pre_review_decision,
            "pre_review_approved_amount": c.pre_review_approved_amount,
        }
        for c in claims
    ]


@router.get(
    "/{claim_id}",
    response_model=ClaimDecisionOutput,
    summary="Get claim details",
    description="Retrieve full claim details including execution trace.",
)
async def get_claim(
    claim_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> ClaimDecisionOutput:
    """Get a specific claim with its full decision output and trace."""
    result = await db.execute(
        select(ClaimRecord).where(ClaimRecord.id == claim_id)
    )
    claim = result.scalar_one_or_none()

    if not claim:
        raise HTTPException(status_code=404, detail=f"Claim '{claim_id}' not found.")

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
