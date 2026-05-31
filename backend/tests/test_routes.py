"""
Integration tests for API routes.
"""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.main import app
from app.config import get_settings
from app.db.database import Base, get_db_session
from app.db.models import ClaimRecord
from app.models.claim import ClaimSubmissionRequest, DocumentInput


# ── Database Isolation for Route Tests ──────────────────────────────

TEST_DB_FILE = "test_claims.db"
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_FILE}"

@pytest.fixture(scope="module", autouse=True)
def setup_test_database():
    """Override database settings to use a test-specific file DB and clean it up after tests."""
    settings = get_settings()
    original_url = settings.database_url
    settings.database_url = TEST_DB_URL
    
    yield
    
    # Restore original settings
    settings.database_url = original_url
    
    # Remove test DB file if it exists
    db_path = Path(__file__).parent.parent / TEST_DB_FILE
    if db_path.exists():
        try:
            os.remove(db_path)
        except OSError:
            pass


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient which triggers the app lifespan (database init, service setup)."""
    with TestClient(app) as tc:
        yield tc


@pytest_asyncio.fixture
async def async_session():
    """Async session for inserting/verifying records in the test DB directly."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ── Route Tests ─────────────────────────────────────────────────────

class TestRoutes:
    """Tests for all primary API routes."""

    @patch("app.tasks.worker.process_claim_task")
    def test_submit_claim_success(self, mock_task, client):
        """POST /api/claims creates a pending record and dispatches Celery task."""
        mock_task.delay = MagicMock()

        payload = {
            "member_id": "EMP001",
            "policy_id": "PLUM_GHI_2024",
            "claim_category": "CONSULTATION",
            "claimed_amount": 1500.0,
            "treatment_date": "2024-11-15",
            "hospital_name": "Apollo Hospitals",
            "documents": [
                {
                    "file_id": "d1",
                    "file_name": "rx.jpg",
                    "actual_type": "PRESCRIPTION",
                    "quality": "GOOD",
                    "content": {
                        "patient_name": "Rajesh Kumar",
                        "date": "2024-11-15",
                    }
                }
            ]
        }

        response = client.post("/api/claims", json=payload)
        
        assert response.status_code == 202
        data = response.json()
        assert data["claim_id"] is not None
        assert data["member_id"] == "EMP001"
        assert data["status"] == "pending"
        
        # Verify background task was enqueued
        mock_task.delay.assert_called_once()

    def test_list_claims(self, client):
        """GET /api/claims returns list of claims."""
        response = client.get("/api/claims")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_get_claim_detail(self, client, async_session):
        """GET /api/claims/{id} returns 200 with claim details, or 404."""
        # 1. Create a dummy claim record directly in DB
        claim_id = "test-claim-detail-123"
        record = ClaimRecord(
            id=claim_id,
            member_id="EMP001",
            member_name="Rajesh Kumar",
            policy_id="PLUM_GHI_2024",
            claim_category="CONSULTATION",
            claimed_amount=1500.0,
            treatment_date="2024-11-15",
            status="completed",
            decision="APPROVED",
            approved_amount=1200.0,
            decision_reasons=["Approved"],
        )
        async_session.add(record)
        await async_session.commit()

        # 2. Query via API route
        response = client.get(f"/api/claims/{claim_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["claim_id"] == claim_id
        assert data["decision"] == "APPROVED"
        assert data["approved_amount"] == 1200.0

        # Query unknown ID returns 404
        response_404 = client.get("/api/claims/unknown-id-999")
        assert response_404.status_code == 404

    @pytest.mark.asyncio
    async def test_review_action_approve(self, client, async_session):
        """POST /api/reviews/{id}/action with approve action restores original decision."""
        claim_id = "test-review-approve-123"
        record = ClaimRecord(
            id=claim_id,
            member_id="EMP001",
            policy_id="PLUM_GHI_2024",
            claim_category="CONSULTATION",
            claimed_amount=2000.0,
            treatment_date="2024-11-15",
            status="awaiting_review",
            pre_review_decision="APPROVED",
            pre_review_approved_amount=1800.0,
        )
        async_session.add(record)
        await async_session.commit()

        action_payload = {
            "action": "approve",
            "notes": "Looks good, approved by admin",
            "reviewed_by": "reviewer_01",
        }

        response = client.post(f"/api/reviews/{claim_id}/action", json=action_payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["decision"] == "APPROVED"
        assert data["approved_amount"] == 1800.0
        assert data["review_action"] == "approved"
        assert data["reviewed_by"] == "reviewer_01"

    @pytest.mark.asyncio
    async def test_review_action_deny(self, client, async_session):
        """POST /api/reviews/{id}/action with deny action rejects claim with approved_amount=0."""
        claim_id = "test-review-deny-123"
        record = ClaimRecord(
            id=claim_id,
            member_id="EMP001",
            policy_id="PLUM_GHI_2024",
            claim_category="CONSULTATION",
            claimed_amount=2000.0,
            treatment_date="2024-11-15",
            status="awaiting_review",
            pre_review_decision="APPROVED",
            pre_review_approved_amount=1800.0,
        )
        async_session.add(record)
        await async_session.commit()

        action_payload = {
            "action": "deny",
            "notes": "Rejected: duplicate request",
            "reviewed_by": "reviewer_01",
        }

        response = client.post(f"/api/reviews/{claim_id}/action", json=action_payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["decision"] == "REJECTED"
        assert data["approved_amount"] == 0.0
        assert data["review_action"] == "denied"
        assert "Rejected:" in data["review_notes"]

    @pytest.mark.asyncio
    async def test_review_action_conflict(self, client, async_session):
        """POST /api/reviews/{id}/action on a non-awaiting_review claim returns 409 conflict."""
        claim_id = "test-review-conflict-123"
        record = ClaimRecord(
            id=claim_id,
            member_id="EMP001",
            policy_id="PLUM_GHI_2024",
            claim_category="CONSULTATION",
            claimed_amount=2000.0,
            treatment_date="2024-11-15",
            status="completed",
            decision="APPROVED",
            approved_amount=1800.0,
        )
        async_session.add(record)
        await async_session.commit()

        action_payload = {
            "action": "approve",
            "notes": "Approve completed claim",
            "reviewed_by": "reviewer_01",
        }

        response = client.post(f"/api/reviews/{claim_id}/action", json=action_payload)
        assert response.status_code == 409

    @patch("app.tasks.worker.process_claim_task")
    def test_submit_claim_idempotency(self, mock_task, client):
        """POST /api/claims with X-Idempotency-Key returns same claim_id on retry."""
        mock_task.delay = MagicMock()

        # Simple dict to simulate Redis storage
        redis_store = {}
        mock_redis = MagicMock()
        mock_redis.aclose = AsyncMock()

        async def mock_set(key, val, nx=False, ex=None):
            if nx and key in redis_store:
                return False
            redis_store[key] = val
            return True

        async def mock_get(key):
            return redis_store.get(key)

        mock_redis.set = AsyncMock(side_effect=mock_set)
        mock_redis.get = AsyncMock(side_effect=mock_get)

        headers = {"X-Idempotency-Key": "test-idem-key-999"}
        payload = {
            "member_id": "EMP001",
            "policy_id": "PLUM_GHI_2024",
            "claim_category": "CONSULTATION",
            "claimed_amount": 1500.0,
            "treatment_date": "2024-11-15",
            "hospital_name": "Apollo Hospitals",
            "documents": [
                {
                    "file_id": "d1",
                    "file_name": "rx.jpg",
                    "actual_type": "PRESCRIPTION",
                    "quality": "GOOD",
                    "content": {
                        "patient_name": "Rajesh Kumar",
                        "date": "2024-11-15",
                    }
                }
            ]
        }

        with patch("redis.asyncio.from_url", return_value=mock_redis):
            # 1. First submission
            res1 = client.post("/api/claims", json=payload, headers=headers)
            assert res1.status_code == 202
            id1 = res1.json()["claim_id"]

            # 2. Second submission with same key
            res2 = client.post("/api/claims", json=payload, headers=headers)
            assert res2.status_code == 202
            id2 = res2.json()["claim_id"]

        # Assert same claim ID returned
        assert id1 == id2
        
        # Verify background task enqueued only once
        mock_task.delay.assert_called_once()
