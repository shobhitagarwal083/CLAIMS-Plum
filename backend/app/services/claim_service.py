"""
Claim Service — Business Logic Layer.

Sits between the API routes and the pipeline executor.
Handles:
- Orchestrating the pipeline execution
- Persisting results to the database
- Translating between API models and pipeline models
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClaimRecord
from app.models.claim import ClaimDecisionOutput, ClaimSubmissionRequest
from app.pipeline.executor import PipelineExecutor

logger = logging.getLogger(__name__)


class ClaimService:
    """
    Business logic layer for claim processing.
    
    Stateless — all state lives in the pipeline's ClaimContext.
    """

    def __init__(self, pipeline: PipelineExecutor):
        self._pipeline = pipeline

    async def process_claim(
        self,
        request: ClaimSubmissionRequest,
        db: Optional[AsyncSession] = None,
    ) -> ClaimDecisionOutput:
        """
        Process a claim submission through the full pipeline.
        
        1. Execute the pipeline
        2. Persist the result (if db session provided)
        3. Return the decision output
        """
        from sqlalchemy import select
        from app.models.claim import ClaimHistoryEntry

        if db:
            try:
                # Fetch past claims for the same member from the database
                past_claims_query = await db.execute(
                    select(ClaimRecord).where(
                        ClaimRecord.member_id == request.member_id
                    )
                )
                past_records = past_claims_query.scalars().all()
                
                db_history = [
                    ClaimHistoryEntry(
                        claim_id=c.id,
                        date=c.treatment_date,
                        amount=c.claimed_amount,
                        provider=c.hospital_name or "unknown",
                        status=c.status,
                        decision=c.decision,
                        claim_category=c.claim_category
                    )
                    for c in past_records
                ]
                
                # Combine database history with request payload history
                combined_history = {h.claim_id: h for h in db_history}
                for h in (request.claims_history or []):
                    combined_history[h.claim_id] = h
                
                request.claims_history = list(combined_history.values())
            except Exception as exc:
                logger.error("Failed to fetch past claims history from DB: %s", exc)

        # Execute pipeline
        result = await self._pipeline.execute(request)

        # Persist to database
        if db:
            await self._persist_result(db, request, result)

        return result

    async def _persist_result(
        self,
        db: AsyncSession,
        request: ClaimSubmissionRequest,
        result: ClaimDecisionOutput,
    ) -> None:
        """Save the claim decision to the database."""
        try:
            record = ClaimRecord(
                id=result.claim_id,
                member_id=result.member_id,
                member_name=result.member_name,
                policy_id=result.policy_id,
                claim_category=result.claim_category,
                claimed_amount=result.amount_breakdown.claimed_amount if result.amount_breakdown else 0,
                treatment_date=request.treatment_date,
                hospital_name=request.hospital_name,
                status="completed",
                decision=result.decision.value if result.decision else None,
                approved_amount=result.approved_amount,
                confidence_score=result.confidence_score,
                rejection_reasons=result.rejection_reasons,
                decision_reasons=result.decision_reasons,
                amount_breakdown=result.amount_breakdown.model_dump() if result.amount_breakdown else None,
                document_issues=result.document_issues,
                fraud_signals=result.fraud_signals,
                fraud_score=result.fraud_score,
                degraded_components=result.degraded_components,
                is_document_error=result.is_document_error,
                manual_review_recommended=result.manual_review_recommended,
                execution_trace=result.execution_trace,
                processing_time_ms=result.processing_time_ms,
            )
            db.add(record)
            await db.flush()  # Flush to get any DB errors early
            logger.info("Claim %s persisted to database.", result.claim_id)
        except Exception as exc:
            logger.error("Failed to persist claim %s: %s", result.claim_id, exc)
            # Don't fail the API response because of DB issues
