"""
Shared test fixtures for the Plum Claims test suite.

Provides:
- Policy engine loaded from the real policy_terms.json
- Mock AI client for isolated agent testing
- In-memory database sessions
- Pre-built ClaimContext and ClaimSubmissionRequest factories
"""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.database import Base
from app.policy.rules_engine import PolicyRulesEngine


# ── Event Loop ──────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Policy Engine ───────────────────────────────────────────────────

@pytest.fixture(scope="session")
def policy_engine():
    """Load the real policy_terms.json once per session."""
    policy_path = Path(__file__).parent.parent / "policy_terms.json"
    return PolicyRulesEngine(str(policy_path))


# ── Mock AI Client ──────────────────────────────────────────────────

@pytest.fixture
def mock_ai_client():
    """
    Returns a MagicMock that mimics ModelClient.
    Callers can configure return values per test:
        mock_ai_client.complete_json.return_value = {"document_type": "PRESCRIPTION", ...}
    """
    from app.ai.model_client import AIResponse

    client = MagicMock()
    client.complete = AsyncMock(return_value=AIResponse(
        content="test response",
        provider="test",
        model="test-model",
    ))
    client.complete_json = AsyncMock(return_value={})
    client.complete_with_vision = AsyncMock(return_value=AIResponse(
        content="test vision response",
        provider="test",
        model="test-model",
    ))
    return client


# ── Database Session (in-memory SQLite) ─────────────────────────────

@pytest_asyncio.fixture
async def db_session():
    """Async in-memory SQLite session for isolated DB tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session

    await engine.dispose()


# ── Sample Context Factory ──────────────────────────────────────────

@pytest.fixture
def make_context():
    """
    Factory fixture that returns a function to create ClaimContext instances.
    Usage:
        ctx = make_context(member_id="MEM001", claim_category="CONSULTATION", ...)
    """
    from app.pipeline.claim_context import ClaimContext
    from app.models.claim import DocumentInput, DocumentType, DocumentQuality, ClassifiedDocument

    def _factory(
        member_id: str = "MEM001",
        member_name: str | None = "Ananya Sharma",
        policy_id: str = "PLUM_GHI_2024",
        claim_category: str = "CONSULTATION",
        claimed_amount: float = 3000.0,
        treatment_date: str = "2025-03-15",
        hospital_name: str | None = "Apollo Hospitals",
        documents: list | None = None,
        classified_documents: list | None = None,
        claims_history: list | None = None,
        ytd_claims_amount: float = 0.0,
        **kwargs,
    ) -> ClaimContext:
        if documents is None:
            documents = [
                DocumentInput(
                    file_id="doc1",
                    file_name="prescription.jpg",
                    actual_type="PRESCRIPTION",
                    quality="GOOD",
                    content={
                        "patient_name": member_name or "Ananya Sharma",
                        "doctor_name": "Dr. Mehta",
                        "diagnosis": "Viral Fever",
                        "date": treatment_date,
                        "hospital_name": hospital_name or "Apollo Hospitals",
                    },
                ),
                DocumentInput(
                    file_id="doc2",
                    file_name="bill.jpg",
                    actual_type="HOSPITAL_BILL",
                    quality="GOOD",
                    content={
                        "patient_name": member_name or "Ananya Sharma",
                        "hospital_name": hospital_name or "Apollo Hospitals",
                        "date": treatment_date,
                        "total": claimed_amount,
                        "line_items": [
                            {"description": "Consultation Fee", "amount": claimed_amount}
                        ],
                    },
                ),
            ]

        ctx = ClaimContext(
            member_id=member_id,
            member_name=member_name,
            policy_id=policy_id,
            claim_category=claim_category,
            claimed_amount=claimed_amount,
            treatment_date=treatment_date,
            hospital_name=hospital_name,
            documents=documents,
            ytd_claims_amount=ytd_claims_amount,
            claims_history=claims_history or [],
        )

        if classified_documents is not None:
            ctx.classified_documents = classified_documents

        for key, val in kwargs.items():
            if hasattr(ctx, key):
                setattr(ctx, key, val)

        return ctx

    return _factory


# ── Sample Request Factory ──────────────────────────────────────────

@pytest.fixture
def make_request():
    """Factory for creating ClaimSubmissionRequest instances."""
    from app.models.claim import ClaimSubmissionRequest, DocumentInput

    def _factory(
        member_id: str = "MEM001",
        member_name: str | None = None,
        claim_category: str = "CONSULTATION",
        claimed_amount: float = 3000.0,
        treatment_date: str = "2025-03-15",
        hospital_name: str = "Apollo Hospitals",
        documents: list | None = None,
        **kwargs,
    ) -> ClaimSubmissionRequest:
        pat_name = member_name or ("Rajesh Kumar" if member_id == "EMP001" else "Ananya Sharma")
        if documents is None:
            documents = [
                DocumentInput(
                    file_id="doc1",
                    file_name="prescription.jpg",
                    actual_type="PRESCRIPTION",
                    quality="GOOD",
                    content={
                        "patient_name": pat_name,
                        "doctor_name": "Dr. Mehta",
                        "diagnosis": "Viral Fever",
                        "date": treatment_date,
                        "hospital_name": hospital_name,
                    },
                ),
                DocumentInput(
                    file_id="doc2",
                    file_name="bill.jpg",
                    actual_type="HOSPITAL_BILL",
                    quality="GOOD",
                    content={
                        "patient_name": pat_name,
                        "hospital_name": hospital_name,
                        "date": treatment_date,
                        "total": claimed_amount,
                        "line_items": [
                            {"description": "Consultation Fee", "amount": claimed_amount}
                        ],
                    },
                ),
            ]

        return ClaimSubmissionRequest(
            member_id=member_id,
            claim_category=claim_category,
            claimed_amount=claimed_amount,
            treatment_date=treatment_date,
            hospital_name=hospital_name,
            documents=documents,
            **kwargs,
        )

    return _factory
