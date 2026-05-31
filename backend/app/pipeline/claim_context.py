"""
Claim Context — In-Memory Pipeline State.

Mirrors the ExecutionContext pattern from the SuperNodes platform.
Holds all state for a single claim processing run:
- Input data
- Agent results (ordered)
- Overall confidence tracking
- Degradation tracking

The context is the single source of truth during pipeline execution.
It is created at the start, mutated by each agent, and serialized
to the execution trace at the end.

Design: Mutable during processing, frozen after completion.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

from app.models.claim import (
    ClaimCategory,
    ClaimSubmissionRequest,
    ClassifiedDocument,
    DocumentInput,
    ParsedDocument,
)
from app.models.trace import AgentTraceEntry


@dataclass
class ClaimContext:
    """
    In-memory execution state for a claim processing run.

    Created by the executor at pipeline start, passed through each agent,
    and serialized to the decision output at the end.
    """

    # ── Identity ─────────────────────────────────────────────────
    claim_id: str = field(default_factory=lambda: str(uuid4()))
    
    # ── Input Data ───────────────────────────────────────────────
    member_id: str = ""
    member_name: Optional[str] = None
    policy_id: str = ""
    claim_category: str = ""
    claimed_amount: Decimal = field(default_factory=lambda: Decimal('0.00'))
    treatment_date: str = ""
    hospital_name: Optional[str] = None
    documents: list[DocumentInput] = field(default_factory=list)
    ytd_claims_amount: Decimal = field(default_factory=lambda: Decimal('0.00'))
    claims_history: list[dict[str, Any]] = field(default_factory=list)
    simulate_component_failure: bool = False

    # ── Processing State ─────────────────────────────────────────
    classified_documents: list[ClassifiedDocument] = field(default_factory=list)
    parsed_documents: list[ParsedDocument] = field(default_factory=list)
    extracted_diagnosis: Optional[str] = None
    extracted_treatment: Optional[str] = None
    extracted_line_items: list[dict[str, Any]] = field(default_factory=list)
    extracted_patient_names: list[str] = field(default_factory=list)

    # ── Confidence & Degradation ─────────────────────────────────
    overall_confidence: float = 1.0
    degraded_components: list[str] = field(default_factory=list)
    confidence_reductions: list[dict[str, Any]] = field(default_factory=list)

    # ── Execution Trace ──────────────────────────────────────────
    agent_traces: list[AgentTraceEntry] = field(default_factory=list)
    _agent_order: int = field(default=0, repr=False)

    # ── Timing ───────────────────────────────────────────────────
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    _start_time: float = field(default_factory=time.monotonic, repr=False)

    # ── Pipeline Control ─────────────────────────────────────────
    should_halt: bool = False
    halt_reason: Optional[str] = None
    is_document_error: bool = False

    @classmethod
    def from_request(cls, request: ClaimSubmissionRequest) -> ClaimContext:
        """Create context from an API request."""
        return cls(
            member_id=request.member_id,
            policy_id=request.policy_id,
            claim_category=request.claim_category.value,
            claimed_amount=request.claimed_amount,
            treatment_date=request.treatment_date,
            hospital_name=request.hospital_name,
            documents=request.documents,
            ytd_claims_amount=request.ytd_claims_amount or Decimal('0.00'),
            claims_history=[h.model_dump() for h in (request.claims_history or [])],
            simulate_component_failure=request.simulate_component_failure,
        )

    def next_order_index(self) -> int:
        """Get and increment the agent execution order index."""
        idx = self._agent_order
        self._agent_order += 1
        return idx

    def add_trace(self, trace: AgentTraceEntry) -> None:
        """Record an agent's execution trace."""
        self.agent_traces.append(trace)

    def reduce_confidence(self, factor: float, reason: str) -> None:
        """
        Reduce overall confidence by a factor (0-1).
        
        factor=0.8 means confidence becomes 80% of current value.
        Records the reduction for the explainability trace.
        """
        old = self.overall_confidence
        self.overall_confidence = round(self.overall_confidence * factor, 4)
        self.confidence_reductions.append({
            "factor": factor,
            "reason": reason,
            "confidence_before": old,
            "confidence_after": self.overall_confidence,
        })

    def mark_degraded(self, component: str, reason: str) -> None:
        """Mark a component as degraded (failed but pipeline continued)."""
        self.degraded_components.append(component)
        self.reduce_confidence(0.6, f"Component '{component}' failed: {reason}")

    def halt(self, reason: str, is_doc_error: bool = False) -> None:
        """Halt the pipeline (e.g., missing documents)."""
        self.should_halt = True
        self.halt_reason = reason
        self.is_document_error = is_doc_error

    def finish(self) -> None:
        """Mark processing as complete and record total time."""
        self.finished_at = datetime.utcnow()

    @property
    def processing_time_ms(self) -> int:
        """Total processing time in milliseconds."""
        return int((time.monotonic() - self._start_time) * 1000)

    @property
    def trace_summary(self) -> list[dict[str, Any]]:
        """Compact trace for the API response."""
        return [t.to_display_dict() for t in self.agent_traces]
