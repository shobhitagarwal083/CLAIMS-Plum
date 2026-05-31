"""
Unit and integration tests for Celery Payload Optimization (Phase 5).
"""

import os
import base64
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import get_settings
from app.tasks.worker import run_async_process_claim


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient."""
    with TestClient(app) as tc:
        yield tc


def test_submit_claim_offloads_base64(client):
    """POST /api/claims should save base64_data to disk and nullify it in task payload."""
    settings = get_settings()
    
    # Define sample image content in base64
    sample_content = b"fake-png-image-content"
    encoded_data = base64.b64encode(sample_content).decode("utf-8")
    
    payload = {
        "member_id": "MEM001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "CONSULTATION",
        "claimed_amount": 1500.0,
        "treatment_date": "2024-11-15",
        "hospital_name": "Apollo Hospitals",
        "documents": [
            {
                "file_id": "d-opt-1",
                "file_name": "bill.png",
                "base64_data": f"data:image/png;base64,{encoded_data}",
                "mime_type": "image/png"
            }
        ]
    }

    with patch("app.tasks.worker.process_claim_task.delay") as mock_delay:
        response = client.post("/api/claims", json=payload)
        assert response.status_code == 202
        
        # Verify Celery delay was called
        mock_delay.assert_called_once()
        called_args = mock_delay.call_args[0]
        claim_id = called_args[0]
        enqueued_payload = called_args[1]
        
        # Verify base64_data is removed (None) and file_path is populated
        doc = enqueued_payload["documents"][0]
        assert doc["base64_data"] is None
        assert doc["file_path"] is not None
        
        # Verify file exists on disk with correct content
        saved_path = Path(doc["file_path"])
        assert saved_path.exists()
        assert saved_path.read_bytes() == sample_content
        
        # Cleanup
        parent_dir = saved_path.parent
        if parent_dir.exists():
            shutil.rmtree(parent_dir)


@pytest.mark.asyncio
async def test_worker_reloads_base64_from_disk():
    """Worker task should read file from disk and restore base64_data before execution."""
    settings = get_settings()
    claim_id = "test-worker-reload-123"
    
    # Create file manually on disk
    upload_dir = Path(settings.upload_dir) / claim_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    sample_content = b"fake-worker-reloaded-bytes"
    file_path = upload_dir / "d-opt-2_report.png"
    file_path.write_bytes(sample_content)
    
    # Optimized request payload (as if enqueued by claims.py)
    request_data = {
        "member_id": "MEM001",
        "policy_id": "PLUM_GHI_2024",
        "claim_category": "CONSULTATION",
        "claimed_amount": 1500.0,
        "treatment_date": "2024-11-15",
        "hospital_name": "Apollo Hospitals",
        "documents": [
            {
                "file_id": "d-opt-2",
                "file_name": "report.png",
                "file_path": str(file_path.resolve()),
                "base64_data": None
            }
        ]
    }

    # Mock the database calls and pipeline execution so we only test request reconstruction
    from unittest.mock import AsyncMock
    mock_db = MagicMock()
    
    mock_execute_result = MagicMock()
    mock_record = MagicMock()
    mock_record.status = "pending"
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=mock_record)
    
    mock_scalars = MagicMock()
    mock_scalars.all = MagicMock(return_value=[])
    mock_execute_result.scalars = MagicMock(return_value=mock_scalars)
    
    mock_db.execute = AsyncMock(return_value=mock_execute_result)
    mock_db.commit = AsyncMock()
    
    # Mock get_session_factory
    mock_session_factory = MagicMock()
    # Need to handle async context manager for db session
    class AsyncContextManagerMock:
        async def __aenter__(self):
            return mock_db
        async def __aexit__(self, exc_type, exc, tb):
            pass
            
    mock_session_factory.return_value = AsyncContextManagerMock()

    # Mock pipeline executor
    mock_pipeline = MagicMock()
    mock_output = MagicMock()
    mock_output.decision = None
    mock_output.approved_amount = 0
    mock_output.confidence_score = 1.0
    mock_output.amount_breakdown = None
    mock_output.rejection_reasons = []
    mock_output.decision_reasons = []
    mock_output.document_issues = []
    mock_output.fraud_signals = []
    mock_output.fraud_score = 0.0
    mock_output.degraded_components = []
    mock_output.is_document_error = False
    mock_output.manual_review_recommended = False
    mock_output.execution_trace = []
    mock_output.processing_time_ms = 100
    mock_output.pre_review_decision = None
    mock_output.pre_review_approved_amount = None
    mock_pipeline.execute = MagicMock(return_value=mock_output)

    # Use patch to check the parameters passed to PipelineExecutor.execute
    with patch("app.tasks.worker.get_session_factory", return_value=mock_session_factory), \
         patch("app.tasks.worker.PipelineExecutor", return_value=mock_pipeline), \
         patch("app.tasks.worker.PolicyRulesEngine"), \
         patch("app.tasks.worker._init_worker_ai_client"), \
         patch("redis.asyncio.from_url") as mock_redis_from_url:
         
        # Mock Redis lock
        from unittest.mock import AsyncMock
        mock_redis = MagicMock()
        mock_redis.lock = MagicMock()
        mock_redis.lock.return_value.acquire = AsyncMock(return_value=True)
        mock_redis.lock.return_value.release = AsyncMock()
        mock_redis.aclose = AsyncMock()
        mock_redis_from_url.return_value = mock_redis
        
        await run_async_process_claim(claim_id, request_data)
        
        # Verify that execute was called with a request that has base64_data populated
        mock_pipeline.execute.assert_called_once()
        passed_request = mock_pipeline.execute.call_args[0][0]
        
        # Verify base64_data is restored
        restored_doc = passed_request.documents[0]
        assert restored_doc.base64_data is not None
        assert base64.b64decode(restored_doc.base64_data) == sample_content

    # Cleanup
    if upload_dir.exists():
        shutil.rmtree(upload_dir)
