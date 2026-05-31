"""
Claim Data Models.

Pydantic models representing the claim lifecycle — from submission through
processing to final decision. These are the contracts between the API layer
and the processing pipeline.

Design decisions:
- Strict validation on input (catch bad data at the boundary)
- Optional fields on output (pipeline fills them progressively)
- All monetary values as float (Decimal overkill for assignment scope,
  but documented as a trade-off)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, field_serializer


# ── Enums ────────────────────────────────────────────────────────────


class ClaimCategory(str, Enum):
    """Claim categories from policy_terms.json coverage_categories."""
    CONSULTATION = "CONSULTATION"
    DIAGNOSTIC = "DIAGNOSTIC"
    PHARMACY = "PHARMACY"
    DENTAL = "DENTAL"
    VISION = "VISION"
    ALTERNATIVE_MEDICINE = "ALTERNATIVE_MEDICINE"


class ClaimStatus(str, Enum):
    """Processing lifecycle status."""
    PENDING = "pending"
    PROCESSING = "processing"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"


class ClaimDecision(str, Enum):
    """Final claim decision as required by assignment."""
    APPROVED = "APPROVED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


# ── Document Models ──────────────────────────────────────────────────


class DocumentType(str, Enum):
    """Recognized medical document types."""
    PRESCRIPTION = "PRESCRIPTION"
    HOSPITAL_BILL = "HOSPITAL_BILL"
    LAB_REPORT = "LAB_REPORT"
    PHARMACY_BILL = "PHARMACY_BILL"
    DENTAL_REPORT = "DENTAL_REPORT"
    DIAGNOSTIC_REPORT = "DIAGNOSTIC_REPORT"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    UNKNOWN = "UNKNOWN"


class DocumentQuality(str, Enum):
    """Document readability assessment."""
    GOOD = "GOOD"
    POOR = "POOR"
    UNREADABLE = "UNREADABLE"


class DocumentInput(BaseModel):
    """A document as submitted by the user or test case."""
    file_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    file_name: Optional[str] = None
    actual_type: Optional[str] = None       # Ground-truth type (for test cases)
    quality: Optional[str] = None           # Simulated quality (for test cases)
    patient_name_on_doc: Optional[str] = None  # For TC003 cross-validation test
    content: Optional[dict[str, Any]] = None   # Pre-extracted content (test mode)
    file_path: Optional[str] = None         # Path to uploaded file (real mode)
    base64_data: Optional[str] = None       # Base64 encoded file data
    mime_type: Optional[str] = None


class ClassifiedDocument(BaseModel):
    """A document after classification by Agent 1."""
    file_id: str
    file_name: Optional[str] = None
    classified_type: DocumentType
    quality: DocumentQuality = DocumentQuality.GOOD
    quality_score: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    classification_reason: str = ""


class ParsedDocument(BaseModel):
    """A document after OCR + structured extraction by Agent 3."""
    file_id: str
    document_type: DocumentType
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    field_confidences: dict[str, float] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    raw_text: Optional[str] = None
    extraction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


# ── Claim Submission ─────────────────────────────────────────────────


class ClaimHistoryEntry(BaseModel):
    """Past claim for fraud detection context."""
    claim_id: str
    date: str
    amount: Decimal
    provider: Optional[str] = None
    status: Optional[str] = None
    decision: Optional[str] = None
    claim_category: Optional[str] = None


class ClaimSubmissionRequest(BaseModel):
    """
    Inbound claim submission — this is what the API receives.
    
    Supports two modes:
    - Real mode: documents contain file_path/base64_data for OCR
    - Test mode: documents contain pre-extracted `content` for eval
    """
    member_id: str = Field(..., min_length=1, description="Member ID from policy roster")
    policy_id: str = Field(default="PLUM_GHI_2024", description="Policy identifier")
    claim_category: ClaimCategory
    treatment_date: str = Field(..., description="Treatment date in YYYY-MM-DD format")
    claimed_amount: Decimal = Field(..., gt=0, description="Amount claimed in INR")
    hospital_name: Optional[str] = None
    documents: list[DocumentInput] = Field(..., min_length=1)
    
    # Optional context for fraud detection and limit checks
    ytd_claims_amount: Optional[Decimal] = None
    claims_history: Optional[list[ClaimHistoryEntry]] = None
    
    # Test harness flags
    simulate_component_failure: bool = False

    @field_validator("treatment_date")
    @classmethod
    def validate_treatment_date(cls, v: str) -> str:
        """Ensure treatment_date is a valid ISO date string."""
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"Invalid date format: '{v}'. Expected YYYY-MM-DD.")
        return v


# ── Review Action Request ────────────────────────────────────────────


class ReviewActionRequest(BaseModel):
    """Human reviewer's action on a MANUAL_REVIEW claim."""
    action: Literal["approve", "deny"] = Field(
        ..., description="Whether to approve or deny the claim"
    )
    reviewed_by: str = Field(
        ..., min_length=1, description="Name or ID of the reviewer"
    )
    notes: str = Field(
        ..., min_length=1, description="Mandatory reviewer notes explaining the decision"
    )
    approved_amount: Optional[Decimal] = Field(
        default=None, ge=0.0, description="Override approved amount if approving"
    )


# ── Claim Decision Output ───────────────────────────────────────────


class LineItemDecision(BaseModel):
    """Per-line-item decision for partial approvals (TC006)."""
    description: str
    amount: Decimal
    approved: bool
    reason: Optional[str] = None

    @field_serializer("amount", when_used="json")
    def serialize_amount(self, v: Decimal) -> float:
        return float(v)


class AmountBreakdown(BaseModel):
    """Financial calculation breakdown for full explainability (TC010)."""
    claimed_amount: Decimal
    eligible_amount: Decimal = Decimal('0.00')          # After filtering excluded items
    network_discount_percent: Decimal = Decimal('0.00')
    network_discount_amount: Decimal = Decimal('0.00')
    amount_after_discount: Decimal = Decimal('0.00')
    copay_percent: Decimal = Decimal('0.00')
    copay_amount: Decimal = Decimal('0.00')
    sub_limit: Optional[Decimal] = None
    sub_limit_applied: bool = False
    approved_amount: Decimal = Decimal('0.00')
    line_items: list[LineItemDecision] = Field(default_factory=list)

    @field_serializer(
        "claimed_amount",
        "eligible_amount",
        "network_discount_percent",
        "network_discount_amount",
        "amount_after_discount",
        "copay_percent",
        "copay_amount",
        "sub_limit",
        "approved_amount",
        when_used="json"
    )
    def serialize_decimal(self, v: Decimal | None) -> float | None:
        if v is not None:
            return float(v)
        return None


class ClaimDecisionOutput(BaseModel):
    """
    The final output for a processed claim.
    
    This is the primary deliverable — it must contain enough information
    for an ops team member to understand exactly what happened and why.
    """
    claim_id: str
    member_id: str
    member_name: Optional[str] = None
    policy_id: str
    claim_category: str
    status: str = "pending"
    
    # Decision
    decision: Optional[ClaimDecision] = None
    approved_amount: Optional[Decimal] = None
    confidence_score: float = 0.0
    
    # Explainability
    amount_breakdown: Optional[AmountBreakdown] = None
    rejection_reasons: list[str] = Field(default_factory=list)
    decision_reasons: list[str] = Field(default_factory=list)
    
    # Document verification results
    document_issues: list[str] = Field(default_factory=list)
    is_document_error: bool = False  # True = stopped early due to doc issues
    
    # Fraud signals
    fraud_signals: list[str] = Field(default_factory=list)
    fraud_score: Optional[float] = None
    
    # Degradation tracking
    degraded_components: list[str] = Field(default_factory=list)
    manual_review_recommended: bool = False
    
    # Decision Gate — Human Review fields
    review_action: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None
    pre_review_decision: Optional[str] = None
    pre_review_approved_amount: Optional[Decimal] = None
    
    # Metadata
    processing_time_ms: int = 0
    processed_at: Optional[datetime] = None
    
    # Full execution trace (observability)
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)

    @field_serializer("approved_amount", "pre_review_approved_amount", when_used="json")
    def serialize_decision_amounts(self, v: Decimal | None) -> float | None:
        if v is not None:
            return float(v)
        return None
