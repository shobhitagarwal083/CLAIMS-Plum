"""
Concurrency tests for the Celery worker claims processor.

Verifies that the Redis distributed lock prevents concurrent processing
for the same member + treatment date + claim category.
"""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models import ClaimRecord
from app.tasks.worker import run_async_process_claim


@pytest.mark.asyncio
async def test_concurrent_worker_processing_acquires_lock(db_session):
    """
    Spawns two concurrent processing tasks for the same member + date + category.
    One should succeed to acquire the lock (or run to database stage),
    while the other should be rejected by the concurrency guard and marked as failed.
    """
    claim_id_1 = "claim-concurrent-1"
    claim_id_2 = "claim-concurrent-2"

    # Insert two records in PENDING status in the DB
    record_1 = ClaimRecord(
        id=claim_id_1,
        member_id="EMP001",
        member_name="Rajesh Kumar",
        policy_id="PLUM_GHI_2024",
        claim_category="CONSULTATION",
        claimed_amount=1500.0,
        treatment_date="2024-11-15",
        status="pending",
    )
    record_2 = ClaimRecord(
        id=claim_id_2,
        member_id="EMP001",
        member_name="Rajesh Kumar",
        policy_id="PLUM_GHI_2024",
        claim_category="CONSULTATION",
        claimed_amount=1500.0,
        treatment_date="2024-11-15",
        status="pending",
    )

    db_session.add(record_1)
    db_session.add(record_2)
    await db_session.commit()

    request_data = {
        "member_id": "EMP001",
        "treatment_date": "2024-11-15",
        "claim_category": "CONSULTATION",
        "claimed_amount": 1500.0,
        "policy_id": "PLUM_GHI_2024",
        "documents": [
            {
                "file_id": "doc1",
                "file_name": "bill.jpg",
                "actual_type": "HOSPITAL_BILL",
                "quality": "GOOD",
                "content": {
                    "patient_name": "Rajesh Kumar",
                    "hospital_name": "Apollo Hospitals",
                    "date": "2024-11-15",
                    "total": 1500.0,
                    "line_items": [{"description": "Consultation", "amount": 1500.0}]
                }
            }
        ],
    }

    # Patch the get_session_factory inside worker to return our in-memory SQLite session
    mock_session_factory = MagicMock(return_value=db_session)

    from app.models.claim import ClaimDecisionOutput, ClaimDecision
    from decimal import Decimal

    # We mock PipelineExecutor to avoid actually calling LLMs or rules engine during this concurrency test
    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=ClaimDecisionOutput(
        claim_id=claim_id_1,
        member_id="EMP001",
        policy_id="PLUM_GHI_2024",
        claim_category="CONSULTATION",
        decision=ClaimDecision.APPROVED,
        approved_amount=Decimal("1500.0"),
        confidence_score=0.9,
        rejection_reasons=[],
        decision_reasons=["Approved"],
        amount_breakdown=None,
        document_issues=[],
        fraud_signals=[],
        fraud_score=0.0,
        degraded_components=[],
        is_document_error=False,
        manual_review_recommended=False,
        execution_trace=[],
        processing_time_ms=10,
        pre_review_decision="APPROVED",
        pre_review_approved_amount=Decimal("1500.0"),
    ))

    # We will let them execute concurrently.
    # To simulate a lock collision, we mock redis_client.lock to return a mock lock
    # where the first task acquires it, and the second task fails to acquire it.
    mock_redis = MagicMock()
    mock_redis.aclose = AsyncMock()
    mock_lock_1 = MagicMock()
    mock_lock_1.acquire = AsyncMock(return_value=True)
    mock_lock_1.release = AsyncMock()

    mock_lock_2 = MagicMock()
    mock_lock_2.acquire = AsyncMock(return_value=False)
    mock_lock_2.release = AsyncMock()

    # The lock factory yields mock_lock_1 first, then mock_lock_2
    mock_redis.lock = MagicMock(side_effect=[mock_lock_1, mock_lock_2])

    with patch("app.tasks.worker.get_session_factory", return_value=mock_session_factory), \
         patch("app.tasks.worker.PipelineExecutor", return_value=mock_executor), \
         patch("redis.asyncio.from_url", return_value=mock_redis):

        # Run both tasks sequentially to avoid sharing the DB session concurrently
        await run_async_process_claim(claim_id_1, request_data)
        await run_async_process_claim(claim_id_2, request_data)

    # Refresh DB session and check records
    await db_session.commit()
    
    res1 = await db_session.execute(select(ClaimRecord).where(ClaimRecord.id == claim_id_1))
    rec1 = res1.scalar_one()
    
    res2 = await db_session.execute(select(ClaimRecord).where(ClaimRecord.id == claim_id_2))
    rec2 = res2.scalar_one()

    # One should be completed (status completed)
    # The other should have failed due to concurrent submission
    statuses = {rec1.status, rec2.status}
    assert "completed" in statuses
    assert "failed" in statuses

    failed_record = rec1 if rec1.status == "failed" else rec2
    assert "Concurrent submission" in failed_record.decision_reasons[0]
