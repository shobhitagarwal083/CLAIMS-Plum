"""
Agent 2: Document Validator.

Validates that the correct documents have been uploaded for the claim category.
Compares classified document types against requirements from policy_terms.json.

This is the "early detection" agent — the assignment is emphatic that:
- The system must STOP before making any claim decision
- Error messages must be SPECIFIC (not generic)
- The message must name the uploaded type AND the required type

Handles:
- TC001: Wrong document uploaded (2 prescriptions instead of prescription + bill)
- TC002: Unreadable document (blurry pharmacy bill)
- TC003 is handled by Agent 4 (cross-validation), not here
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from app.models.claim import ClassifiedDocument, DocumentQuality, DocumentType
from app.models.trace import CheckResult, CheckSeverity
from app.pipeline.claim_context import ClaimContext
from app.policy import PolicyRulesEngine

from .base import BaseAgent

logger = logging.getLogger(__name__)


class DocumentValidatorAgent(BaseAgent):
    """
    Validates uploaded documents against policy requirements.

    Input (from context):
        - context.classified_documents (from Agent 1)
        - context.claim_category

    Output (written to context):
        - context.should_halt = True if validation fails
        - context.halt_reason = specific error message

    Checks produced:
        - required_documents: Are all required types present?
        - document_quality: Are all documents readable?
    """

    def __init__(self, policy_engine: PolicyRulesEngine):
        self._policy = policy_engine

    @property
    def agent_name(self) -> str:
        return "Document Validator"

    @property
    def agent_type(self) -> str:
        return "document_validator"

    async def _execute(
        self,
        context: ClaimContext,
    ) -> tuple[list[CheckResult], float, dict[str, Any]]:
        checks: list[CheckResult] = []
        confidence = 1.0

        classified = context.classified_documents
        category = context.claim_category

        # ── Check 1: Document Quality ────────────────────────────
        unreadable_docs = [
            d for d in classified
            if d.quality == DocumentQuality.UNREADABLE
        ]

        if unreadable_docs:
            doc_names = [
                f"'{d.file_name or d.file_id}' ({d.classified_type.value})"
                for d in unreadable_docs
            ]
            reason = (
                f"The following document(s) cannot be read: {', '.join(doc_names)}. "
                f"Please re-upload a clearer photo or scan of "
                f"{'these documents' if len(unreadable_docs) > 1 else 'this document'}. "
                f"Ensure the image is well-lit, not blurry, and all text is visible."
            )
            checks.append(CheckResult(
                check_name="document_quality",
                passed=False,
                reason=reason,
                severity=CheckSeverity.BLOCK,
                details={
                    "unreadable_documents": [
                        {"file_id": d.file_id, "file_name": d.file_name, "type": d.classified_type.value}
                        for d in unreadable_docs
                    ],
                },
            ))

            # Halt pipeline — but don't reject, ask for re-upload
            context.halt(reason, is_doc_error=True)

            output = {
                "validation_passed": False,
                "issue": "unreadable_document",
                "unreadable_count": len(unreadable_docs),
            }
            return checks, 0.0, output

        checks.append(CheckResult(
            check_name="document_quality",
            passed=True,
            reason="All uploaded documents are readable.",
            severity=CheckSeverity.INFO,
        ))

        # ── Check 2: Required Document Types ─────────────────────
        required_types = self._policy.get_required_documents(category)
        if not required_types:
            checks.append(CheckResult(
                check_name="required_documents",
                passed=True,
                reason=f"No specific document requirements found for category '{category}'.",
                severity=CheckSeverity.INFO,
            ))
            return checks, confidence, {"validation_passed": True}

        # Count what we have (excluding UNKNOWN)
        type_counter = Counter(
            d.classified_type.value for d in classified
            if d.classified_type != DocumentType.UNKNOWN
        )

        # Check each required type
        missing_types: list[str] = []
        for req_type in required_types:
            if type_counter.get(req_type, 0) == 0:
                missing_types.append(req_type)

        if missing_types:
            # Build specific error message — the assignment requires this to be
            # detailed enough that the member knows EXACTLY what to do
            uploaded_summary = self._build_uploaded_summary(classified)
            required_summary = ", ".join(
                self._format_doc_type(t) for t in required_types
            )
            missing_summary = ", ".join(
                self._format_doc_type(t) for t in missing_types
            )

            reason = (
                f"Your {category} claim requires the following documents: {required_summary}. "
                f"You uploaded: {uploaded_summary}. "
                f"Missing: {missing_summary}. "
                f"Please upload the missing document(s) and resubmit."
            )

            checks.append(CheckResult(
                check_name="required_documents",
                passed=False,
                reason=reason,
                severity=CheckSeverity.BLOCK,
                details={
                    "required_types": required_types,
                    "uploaded_types": dict(type_counter),
                    "missing_types": missing_types,
                    "category": category,
                },
            ))

            context.halt(reason, is_doc_error=True)

            output = {
                "validation_passed": False,
                "issue": "missing_documents",
                "missing_types": missing_types,
                "uploaded_types": dict(type_counter),
            }
            return checks, 0.0, output

        checks.append(CheckResult(
            check_name="required_documents",
            passed=True,
            reason=(
                f"All required documents for {category} claim are present: "
                f"{', '.join(self._format_doc_type(t) for t in required_types)}."
            ),
            severity=CheckSeverity.INFO,
            details={
                "required_types": required_types,
                "uploaded_types": dict(type_counter),
            },
        ))

        # ── Check 3: Document quality warnings (POOR but readable) ─
        poor_docs = [d for d in classified if d.quality == DocumentQuality.POOR]
        if poor_docs:
            confidence *= 0.85  # Reduce confidence for poor quality
            checks.append(CheckResult(
                check_name="document_quality_warning",
                passed=True,
                reason=(
                    f"{len(poor_docs)} document(s) have poor quality but are readable. "
                    f"Extraction accuracy may be reduced."
                ),
                severity=CheckSeverity.WARN,
                details={
                    "poor_quality_docs": [d.file_id for d in poor_docs],
                },
            ))

        output = {
            "validation_passed": True,
            "required_types": required_types,
            "all_present": True,
        }
        return checks, round(confidence, 3), output

    def _build_uploaded_summary(self, docs: list[ClassifiedDocument]) -> str:
        """Build a human-readable summary of what was uploaded."""
        counter = Counter(d.classified_type.value for d in docs)
        parts = []
        for doc_type, count in counter.items():
            formatted = self._format_doc_type(doc_type)
            if count > 1:
                parts.append(f"{count}x {formatted}")
            else:
                parts.append(formatted)
        return ", ".join(parts) if parts else "no recognizable documents"

    @staticmethod
    def _format_doc_type(doc_type: str) -> str:
        """Format a document type enum for human display."""
        return doc_type.replace("_", " ").title()
