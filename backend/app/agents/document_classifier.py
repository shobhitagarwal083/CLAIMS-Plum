"""
Agent 1: Document Classifier.

Classifies each uploaded document into one of the recognized medical
document types (PRESCRIPTION, HOSPITAL_BILL, LAB_REPORT, etc.) and
assesses document quality (GOOD, POOR, UNREADABLE).

For real documents: Uses vision LLM to analyze the document image.
For test mode: Uses the pre-provided `actual_type` and `quality` fields.

This agent handles TC001 (wrong document) and TC002 (unreadable document)
by providing classification + quality data that Agent 2 validates against.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai import ModelClient
from app.models.claim import (
    ClassifiedDocument,
    DocumentInput,
    DocumentQuality,
    DocumentType,
)
from app.models.trace import CheckResult, CheckSeverity
from app.pipeline.claim_context import ClaimContext

from .base import BaseAgent

logger = logging.getLogger(__name__)


# Prompt for vision-based document classification
CLASSIFICATION_PROMPT = """Analyze this medical document image and classify it.

Return a JSON object with these fields:
{
  "document_type": "<one of: PRESCRIPTION, HOSPITAL_BILL, LAB_REPORT, PHARMACY_BILL, DENTAL_REPORT, DIAGNOSTIC_REPORT, DISCHARGE_SUMMARY, UNKNOWN>",
  "quality": "<one of: GOOD, POOR, UNREADABLE>",
  "quality_score": <float 0.0 to 1.0 — how readable the document is>,
  "confidence": <float 0.0 to 1.0 — how confident you are in the classification>,
  "patient_name": "<patient name if visible, or null>",
  "reason": "<brief reason for your classification>"
}

Classification rules:
- PRESCRIPTION: Doctor's handwritten or printed Rx with medicines, dosage, diagnosis
- HOSPITAL_BILL: Bill/invoice from hospital or clinic with line items and amounts
- LAB_REPORT: Laboratory test results with test names, values, reference ranges
- PHARMACY_BILL: Pharmacy/chemist bill listing medicines purchased with prices
- DENTAL_REPORT: Dental examination or treatment report
- DIAGNOSTIC_REPORT: Imaging/radiology reports (X-ray, MRI, CT)
- DISCHARGE_SUMMARY: Hospital discharge summary document
- UNKNOWN: Cannot determine document type

Quality rules:
- GOOD: Text is clearly readable, all fields are visible
- POOR: Some text is hard to read but key fields are identifiable
- UNREADABLE: Document is too blurry, dark, or damaged to extract meaningful data
"""


class DocumentClassifierAgent(BaseAgent):
    """
    Classifies uploaded documents by type and assesses quality.

    Input (from context):
        - context.documents: list of DocumentInput

    Output (written to context):
        - context.classified_documents: list of ClassifiedDocument

    Checks produced:
        - document_classified: One per document with type and quality
    """

    def __init__(self, ai_client: ModelClient | None = None):
        self._ai_client = ai_client

    @property
    def agent_name(self) -> str:
        return "Document Classifier"

    @property
    def agent_type(self) -> str:
        return "document_classifier"

    async def _execute(
        self,
        context: ClaimContext,
    ) -> tuple[list[CheckResult], float, dict[str, Any]]:
        checks: list[CheckResult] = []
        classified: list[ClassifiedDocument] = []
        total_confidence = 0.0

        for doc in context.documents:
            result = await self._classify_document(doc)
            classified.append(result)

            # Record check
            quality_ok = result.quality != DocumentQuality.UNREADABLE
            check = CheckResult(
                check_name=f"classify_{doc.file_id}",
                passed=quality_ok,
                reason=(
                    f"Document '{doc.file_name or doc.file_id}' classified as "
                    f"{result.classified_type.value} with quality {result.quality.value} "
                    f"(confidence: {result.confidence:.2f}). "
                    f"{result.classification_reason}"
                ),
                severity=CheckSeverity.BLOCK if not quality_ok else CheckSeverity.INFO,
                details={
                    "file_id": doc.file_id,
                    "classified_type": result.classified_type.value,
                    "quality": result.quality.value,
                    "quality_score": result.quality_score,
                    "confidence": result.confidence,
                },
            )
            checks.append(check)
            total_confidence += result.confidence

        # Write results to context
        context.classified_documents = classified

        # Average confidence across all documents
        avg_confidence = total_confidence / len(classified) if classified else 0.0

        output_summary = {
            "documents_classified": len(classified),
            "types_found": [d.classified_type.value for d in classified],
            "qualities": [d.quality.value for d in classified],
        }

        return checks, round(avg_confidence, 3), output_summary

    async def _classify_document(self, doc: DocumentInput) -> ClassifiedDocument:
        """
        Classify a single document.

        Test mode: Uses pre-provided actual_type and quality fields.
        Real mode: Uses vision LLM to classify from image data.
        """
        # ── Test Mode: Use pre-provided metadata ─────────────────
        if doc.actual_type:
            doc_type = self._resolve_doc_type(doc.actual_type)
            quality = self._resolve_quality(doc.quality)
            quality_score = 1.0 if quality == DocumentQuality.GOOD else (
                0.4 if quality == DocumentQuality.POOR else 0.0
            )

            return ClassifiedDocument(
                file_id=doc.file_id,
                file_name=doc.file_name,
                classified_type=doc_type,
                quality=quality,
                quality_score=quality_score,
                confidence=0.99,  # High confidence for test data
                classification_reason=f"Type provided directly as '{doc.actual_type}'.",
            )

        # ── Real Mode: Vision LLM Classification ────────────────
        if self._ai_client and (doc.base64_data or doc.file_path):
            try:
                response = await self._ai_client.complete_json(
                    prompt=CLASSIFICATION_PROMPT,
                    images=[{"base64": doc.base64_data, "mime_type": doc.mime_type or "image/jpeg"}]
                    if doc.base64_data else None,
                )

                doc_type = self._resolve_doc_type(response.get("document_type", "UNKNOWN"))
                quality = self._resolve_quality(response.get("quality", "GOOD"))
                quality_score = float(response.get("quality_score", 0.5))
                confidence = float(response.get("confidence", 0.5))

                # Extract patient name if available
                patient_name = response.get("patient_name")

                return ClassifiedDocument(
                    file_id=doc.file_id,
                    file_name=doc.file_name,
                    classified_type=doc_type,
                    quality=quality,
                    quality_score=quality_score,
                    confidence=confidence,
                    classification_reason=response.get("reason", "Classified by vision AI."),
                )

            except Exception as e:
                logger.warning("Vision classification failed for %s: %s", doc.file_id, e)
                # Fallback: UNKNOWN type with degraded confidence
                return ClassifiedDocument(
                    file_id=doc.file_id,
                    file_name=doc.file_name,
                    classified_type=DocumentType.UNKNOWN,
                    quality=DocumentQuality.POOR,
                    quality_score=0.3,
                    confidence=0.2,
                    classification_reason=f"AI classification failed: {e}. Marked as UNKNOWN.",
                )

        # ── No data available ────────────────────────────────────
        return ClassifiedDocument(
            file_id=doc.file_id,
            file_name=doc.file_name,
            classified_type=DocumentType.UNKNOWN,
            quality=DocumentQuality.POOR,
            quality_score=0.5,
            confidence=0.3,
            classification_reason="No image data or type metadata available.",
        )

    @staticmethod
    def _resolve_doc_type(type_str: str | None) -> DocumentType:
        """Safely resolve a string to DocumentType enum."""
        if not type_str:
            return DocumentType.UNKNOWN
        try:
            return DocumentType(type_str.upper())
        except ValueError:
            return DocumentType.UNKNOWN

    @staticmethod
    def _resolve_quality(quality_str: str | None) -> DocumentQuality:
        """Safely resolve a string to DocumentQuality enum."""
        if not quality_str:
            return DocumentQuality.GOOD
        try:
            return DocumentQuality(quality_str.upper())
        except ValueError:
            return DocumentQuality.GOOD
