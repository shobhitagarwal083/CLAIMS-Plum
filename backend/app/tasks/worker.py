import asyncio
import logging
from typing import Any, Optional
from sqlalchemy import select

from app.tasks.celery_app import celery_app
from app.config import get_settings
from app.db.database import get_session_factory
from app.db.models import ClaimRecord
from app.policy.rules_engine import PolicyRulesEngine
from app.pipeline.executor import PipelineExecutor
from app.models.claim import ClaimSubmissionRequest
from app.ai import ModelClient

logger = logging.getLogger(__name__)

def _init_worker_ai_client(settings) -> Optional[ModelClient]:
    """Initialize AI client for worker process with failover support."""
    try:
        providers = []
        if settings.google_api_key:
            providers.append({
                "provider": "google",
                "api_key": settings.google_api_key,
                "model": settings.google_model,
            })
        if settings.openai_api_key:
            providers.append({
                "provider": "openai",
                "api_key": settings.openai_api_key,
                "model": settings.openai_model,
            })
        if settings.openrouter_api_key:
            providers.append({
                "provider": "openrouter",
                "api_key": settings.openrouter_api_key,
                "model": settings.openrouter_model,
            })

        if not providers:
            logger.warning(
                "⚠ No AI API key configured in worker. Running in test-only mode."
            )
            return None

        primary = providers[0]
        fallbacks = providers[1:]

        client = ModelClient(
            provider=primary["provider"],
            api_key=primary["api_key"],
            model=primary.get("model"),
            fallback_providers=fallbacks,
            temperature=settings.ai_temperature,
            max_retries=settings.ai_max_retries,
            timeout=settings.ai_timeout_seconds,
        )
        logger.info(
            "✓ Worker AI Client initialized: primary=%s, fallbacks=%s",
            client.provider.value,
            [c.provider.value for c in client.fallback_clients],
        )
        return client

    except Exception as exc:
        logger.error("Failed to initialize worker AI client: %s", exc)
        return None


async def run_async_process_claim(claim_id: str, request_data: dict[str, Any]):
    """Bridge async pipeline executor inside synchronous Celery worker."""
    from app.db.database import close_db
    import redis.asyncio as aioredis
    
    settings = get_settings()
    redis_client = aioredis.from_url(settings.redis_url)
    lock_key = f"claim_lock:{request_data.get('member_id')}:{request_data.get('treatment_date')}:{request_data.get('claim_category')}"
    lock = redis_client.lock(lock_key, timeout=120, blocking_timeout=5)
    acquired = False

    try:
        acquired = await lock.acquire()
    except Exception as lock_err:
        logger.error(f"Failed to communicate with Redis for lock {lock_key}: {lock_err}")
        # If Redis is completely down, log warning and continue without locking so system remains resilient

    if not acquired:
        logger.warning(f"Concurrency guard: Failed to acquire lock for {lock_key}. Rejecting claim {claim_id}.")
        try:
            session_factory = get_session_factory()
            async with session_factory() as db:
                result = await db.execute(select(ClaimRecord).where(ClaimRecord.id == claim_id))
                record = result.scalar_one_or_none()
                if record:
                    record.status = "failed"
                    record.decision_reasons = ["Concurrent submission detected. Another claim for this member and date is being processed."]
                    await db.commit()
        except Exception as db_exc:
            logger.error(f"Failed to write concurrency failure status for claim {claim_id}: {db_exc}")
        finally:
            await redis_client.aclose()
            return

    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            try:
                # 1. Update status to processing
                result = await db.execute(select(ClaimRecord).where(ClaimRecord.id == claim_id))
                record = result.scalar_one_or_none()
                if not record:
                    logger.error(f"Claim record {claim_id} not found in database.")
                    return
                
                record.status = "processing"
                await db.commit()

                # 2. Re-create executor context
                settings = get_settings()
                ai_client = _init_worker_ai_client(settings)
                policy_engine = PolicyRulesEngine(settings.policy_terms_path)
                pipeline = PipelineExecutor(policy_engine=policy_engine, ai_client=ai_client)

                # Fetch past claims from database for this member (excluding current claim)
                past_claims_query = await db.execute(
                    select(ClaimRecord).where(
                        ClaimRecord.member_id == request_data.get("member_id"),
                        ClaimRecord.id != claim_id
                    )
                )
                past_records = past_claims_query.scalars().all()
                
                db_history = [
                    {
                        "claim_id": c.id,
                        "date": c.treatment_date,
                        "amount": c.claimed_amount,
                        "provider": c.hospital_name or "unknown",
                        "status": c.status,
                        "decision": c.decision,
                        "claim_category": c.claim_category
                    }
                    for c in past_records
                ]
                
                # Combine database history with request payload history (deduplicated by claim_id)
                combined_history = {h["claim_id"]: h for h in db_history}
                for h in (request_data.get("claims_history") or []):
                    combined_history[h["claim_id"]] = h
                
                request_data["claims_history"] = list(combined_history.values())

                # Restore base64_data from file_path (local or remote object storage URL)
                import base64
                from app.utils.storage import download_file_bytes
                for doc in request_data.get("documents", []):
                    if doc.get("file_path") and not doc.get("base64_data"):
                        try:
                            file_bytes = await download_file_bytes(doc["file_path"])
                            doc["base64_data"] = base64.b64encode(file_bytes).decode("utf-8")
                        except Exception as load_err:
                            logger.error("Failed to load document from path %s: %s", doc.get("file_path"), load_err)

                # 3. Parse request and execute pipeline
                request = ClaimSubmissionRequest(**request_data)
                output = await pipeline.execute(request)

                # 4. Save results back
                is_manual_review = (
                    output.decision is not None
                    and output.decision.value == "MANUAL_REVIEW"
                )
                output_json = output.model_dump(mode='json')
                
                record.status = "awaiting_review" if is_manual_review else "completed"
                record.decision = output.decision.value if output.decision else None
                record.approved_amount = output.approved_amount
                record.confidence_score = output.confidence_score
                record.rejection_reasons = output.rejection_reasons
                record.decision_reasons = output.decision_reasons
                record.amount_breakdown = output_json.get('amount_breakdown')
                record.document_issues = output.document_issues
                record.fraud_signals = output.fraud_signals
                record.fraud_score = output.fraud_score
                record.degraded_components = output.degraded_components
                record.is_document_error = output.is_document_error
                record.manual_review_recommended = output.manual_review_recommended
                record.execution_trace = output_json.get('execution_trace', [])
                record.processing_time_ms = output.processing_time_ms
                # Decision Gate fields
                record.pre_review_decision = output.pre_review_decision
                record.pre_review_approved_amount = output.pre_review_approved_amount
                await db.commit()
                if is_manual_review:
                    logger.info(
                        f"Claim {claim_id} flagged for MANUAL_REVIEW → status=awaiting_review."
                    )
                else:
                    logger.info(f"Successfully processed claim {claim_id} in worker background.")

            except Exception as exc:
                logger.exception(f"Failed to process claim {claim_id} in worker: {exc}")
                # Mark task as failed in database
                try:
                    # Refresh session and fetch again to ensure transaction is healthy
                    result = await db.execute(select(ClaimRecord).where(ClaimRecord.id == claim_id))
                    record = result.scalar_one_or_none()
                    if record:
                        record.status = "failed"
                        record.decision_reasons = [f"Processing failed: {str(exc)}"]
                        await db.commit()
                except Exception as db_exc:
                    logger.error(f"Failed to write error status for claim {claim_id} to database: {db_exc}")
    finally:
        if acquired:
            try:
                await lock.release()
            except Exception as release_err:
                logger.warning(f"Failed to release Redis lock {lock_key}: {release_err}")
        await redis_client.aclose()
        await close_db()

@celery_app.task(name="app.tasks.worker.process_claim_task")
def process_claim_task(claim_id: str, request_data: dict[str, Any]):
    asyncio.run(run_async_process_claim(claim_id, request_data))
