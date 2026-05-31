"""
ORM Models.

SQLAlchemy async ORM models for persistent storage.
These mirror the Pydantic models but are optimized for database operations.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from decimal import Decimal
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ClaimRecord(Base):
    """Persistent record of a claim submission and its decision."""
    
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    member_id: Mapped[str] = mapped_column(String(20), index=True)
    member_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    policy_id: Mapped[str] = mapped_column(String(50))
    claim_category: Mapped[str] = mapped_column(String(30))
    claimed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    treatment_date: Mapped[str] = mapped_column(String(10))
    hospital_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    
    # Processing
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    approved_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Decision details
    rejection_reasons: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    decision_reasons: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    amount_breakdown: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    document_issues: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    fraud_signals: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    fraud_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    degraded_components: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_document_error: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_review_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Full execution trace (stored as JSON)
    execution_trace: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # Decision Gate — Human Review fields
    review_action: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # "approved" | "denied"
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pre_review_decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    pre_review_approved_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)

    # Timing
    processing_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
