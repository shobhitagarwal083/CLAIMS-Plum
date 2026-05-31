"""
Agent 3: Document Parser (OCR + Structured Extraction).

Combines the OCR Node and Information Extractor Node patterns from SuperNodes.
For each classified document, extracts structured data according to
document-type-specific schemas.

Two modes:
- Test mode: Documents have pre-provided `content` → skip OCR, use directly
- Real mode: Uses vision LLM for OCR + structured extraction

The extracted data (patient names, diagnosis, amounts, dates, line items)
flows to downstream agents for cross-validation and policy evaluation.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai import ModelClient
from app.models.claim import (
    ClassifiedDocument,
    DocumentInput,
    DocumentType,
    ParsedDocument,
)
from app.models.trace import CheckResult, CheckSeverity
from app.pipeline.claim_context import ClaimContext

from .base import BaseAgent

logger = logging.getLogger(__name__)


# ── Extraction Schemas per Document Type ─────────────────────────────

EXTRACTION_SCHEMAS: dict[str, str] = {
    "PRESCRIPTION": """{
  "doctor_name": "string or null",
  "doctor_registration": "string or null",
  "patient_name": "string or null",
  "patient_age": "string or null",
  "date": "string (YYYY-MM-DD) or null",
  "diagnosis": "string or null",
  "medicines": ["string"],
  "tests_ordered": ["string"],
  "hospital_name": "string or null",
  "treatment": "string or null"
}""",
    "HOSPITAL_BILL": """{
  "hospital_name": "string or null",
  "patient_name": "string or null",
  "date": "string (YYYY-MM-DD) or null",
  "bill_number": "string or null",
  "line_items": [{"description": "string", "amount": number}],
  "total": number,
  "referring_doctor": "string or null"
}""",
    "LAB_REPORT": """{
  "lab_name": "string or null",
  "patient_name": "string or null",
  "referring_doctor": "string or null",
  "date": "string (YYYY-MM-DD) or null",
  "test_name": "string or null",
  "tests": [{"name": "string", "result": "string", "normal_range": "string"}]
}""",
    "PHARMACY_BILL": """{
  "pharmacy_name": "string or null",
  "patient_name": "string or null",
  "prescribing_doctor": "string or null",
  "date": "string (YYYY-MM-DD) or null",
  "medicines": [{"name": "string", "quantity": number, "amount": number}],
  "total": number
}""",
}


class DocumentParserAgent(BaseAgent):
    """
    Extracts structured data from classified documents.

    Input (from context):
        - context.classified_documents (from Agent 1)
        - context.documents (original inputs with content/file data)

    Output (written to context):
        - context.parsed_documents: list of ParsedDocument
        - context.extracted_diagnosis
        - context.extracted_treatment
        - context.extracted_line_items
        - context.extracted_patient_names
    """

    def __init__(self, ai_client: ModelClient | None = None):
        self._ai_client = ai_client

    @property
    def agent_name(self) -> str:
        return "Document Parser"

    @property
    def agent_type(self) -> str:
        return "document_parser"

    async def _execute(
        self,
        context: ClaimContext,
    ) -> tuple[list[CheckResult], float, dict[str, Any]]:
        checks: list[CheckResult] = []
        parsed_docs: list[ParsedDocument] = []
        total_confidence = 0.0

        # Build lookup from file_id → original DocumentInput
        doc_lookup = {d.file_id: d for d in context.documents}

        for classified in context.classified_documents:
            original = doc_lookup.get(classified.file_id)
            if not original:
                continue

            parsed = await self._parse_document(classified, original)
            parsed_docs.append(parsed)

            checks.append(CheckResult(
                check_name=f"parse_{classified.file_id}",
                passed=parsed.extraction_confidence > 0.3,
                reason=(
                    f"Extracted {len(parsed.extracted_data)} fields from "
                    f"{classified.classified_type.value} "
                    f"(confidence: {parsed.extraction_confidence:.2f})."
                    + (f" Quality flags: {', '.join(parsed.quality_flags)}." if parsed.quality_flags else "")
                ),
                severity=CheckSeverity.INFO if parsed.extraction_confidence > 0.3 else CheckSeverity.WARN,
                details={
                    "file_id": classified.file_id,
                    "fields_extracted": list(parsed.extracted_data.keys()),
                    "confidence": parsed.extraction_confidence,
                    "quality_flags": parsed.quality_flags,
                },
            ))
            total_confidence += parsed.extraction_confidence

        # Write results to context
        context.parsed_documents = parsed_docs

        # Extract key fields for downstream agents
        self._aggregate_extracted_data(context, parsed_docs)

        avg_confidence = total_confidence / len(parsed_docs) if parsed_docs else 0.0

        output_summary = {
            "documents_parsed": len(parsed_docs),
            "diagnosis": context.extracted_diagnosis,
            "treatment": context.extracted_treatment,
            "patient_names": context.extracted_patient_names,
            "line_items_count": len(context.extracted_line_items),
        }

        return checks, round(avg_confidence, 3), output_summary

    async def _parse_document(
        self,
        classified: ClassifiedDocument,
        original: DocumentInput,
    ) -> ParsedDocument:
        """
        Parse a single document. Test mode uses pre-provided content.
        Real mode uses vision LLM.
        """
        doc_type = classified.classified_type

        # ── Test Mode: Use pre-provided content ──────────────────
        if original.content:
            return ParsedDocument(
                file_id=classified.file_id,
                document_type=doc_type,
                extracted_data=original.content,
                field_confidences={k: 0.99 for k in original.content.keys()},
                quality_flags=[],
                extraction_confidence=0.98,
            )

        # ── Real Mode: Vision LLM Extraction ─────────────────────
        if self._ai_client and (original.base64_data or original.file_path):
            try:
                schema = EXTRACTION_SCHEMAS.get(doc_type.value, EXTRACTION_SCHEMAS["HOSPITAL_BILL"])

                prompt = (
                    f"Extract all structured data from this {doc_type.value.replace('_', ' ').lower()} document.\n\n"
                    f"Return a JSON object matching this schema:\n{schema}\n\n"
                    f"Rules:\n"
                    f"- For unclear or missing fields, use null\n"
                    f"- For dates, use YYYY-MM-DD format\n"
                    f"- For amounts, use numbers without currency symbols\n"
                    f"- Extract ALL line items if present\n"
                    f"- If handwritten text is unclear, make your best attempt and note it"
                )

                if original.base64_data:
                    response = await self._ai_client.complete_json(
                        prompt=prompt,
                        images=[{"base64": original.base64_data, "mime_type": original.mime_type or "image/jpeg"}],
                    )
                else:
                    response = await self._ai_client.complete_json(prompt=prompt)

                # Calculate field-level confidences
                field_confidences = {}
                for key, value in response.items():
                    if value is None:
                        field_confidences[key] = 0.0
                    elif isinstance(value, str) and len(value) < 2:
                        field_confidences[key] = 0.5
                    else:
                        field_confidences[key] = 0.9

                return ParsedDocument(
                    file_id=classified.file_id,
                    document_type=doc_type,
                    extracted_data=response,
                    field_confidences=field_confidences,
                    quality_flags=[],
                    extraction_confidence=0.85,
                )

            except Exception as e:
                logger.warning("LLM extraction failed for %s: %s", classified.file_id, e)
                return ParsedDocument(
                    file_id=classified.file_id,
                    document_type=doc_type,
                    extracted_data={},
                    quality_flags=[f"extraction_failed: {e}"],
                    extraction_confidence=0.1,
                )

        # ── No data available ────────────────────────────────────
        return ParsedDocument(
            file_id=classified.file_id,
            document_type=doc_type,
            extracted_data={},
            quality_flags=["no_data_available"],
            extraction_confidence=0.0,
        )

    def _aggregate_extracted_data(
        self,
        context: ClaimContext,
        parsed_docs: list[ParsedDocument],
    ) -> None:
        """
        Aggregate extracted data across documents for downstream agents.

        Pulls out key fields that other agents need:
        - Patient names (for cross-validation in Agent 4)
        - Diagnosis and treatment (for policy evaluation in Agent 5)
        - Line items (for adjudication in Agent 6)
        """
        patient_names: list[str] = []
        line_items: list[dict[str, Any]] = []
        diagnosis = None
        treatment = None

        for doc in parsed_docs:
            data = doc.extracted_data

            # Patient names
            name = data.get("patient_name")
            if name and isinstance(name, str) and name.strip():
                patient_names.append(name.strip())

            # Diagnosis (from prescriptions)
            if not diagnosis and data.get("diagnosis"):
                diagnosis = data["diagnosis"]

            # Treatment
            if not treatment and data.get("treatment"):
                treatment = data["treatment"]

            # Line items (only from bills)
            if doc.document_type in (DocumentType.HOSPITAL_BILL, DocumentType.PHARMACY_BILL):
                if "line_items" in data and isinstance(data["line_items"], list):
                    for item in data["line_items"]:
                        if isinstance(item, dict) and "description" in item:
                            val = item.get("amount")
                            try:
                                amount_val = float(val) if val is not None else 0.0
                            except (ValueError, TypeError):
                                amount_val = 0.0
                            line_items.append({
                                "description": item["description"],
                                "amount": amount_val,
                                "source_doc": doc.file_id,
                            })

        context.extracted_patient_names = patient_names
        context.extracted_diagnosis = diagnosis
        context.extracted_treatment = treatment
        context.extracted_line_items = line_items
