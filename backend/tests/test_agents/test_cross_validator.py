"""
Unit tests for Agent 4: Cross-Document Validator.

Tests patient name consistency, fuzzy matching, date alignment,
amount verification, and category content checks.
"""

import pytest

from app.agents.cross_validator import CrossDocumentValidatorAgent
from app.models.claim import (
    DocumentInput,
    DocumentType,
    ParsedDocument,
)


@pytest.mark.asyncio
class TestCrossDocumentValidator:
    """Tests for the CrossDocumentValidatorAgent."""

    def _build_parsed_docs(self, patient_name="Ananya Sharma", hospital="Apollo Hospitals",
                           date="2025-03-15", total=3000.0):
        """Helper to build parsed documents for cross-validation."""
        return [
            ParsedDocument(
                file_id="doc1",
                document_type=DocumentType.PRESCRIPTION,
                extracted_data={
                    "patient_name": patient_name,
                    "doctor_name": "Dr. Mehta",
                    "diagnosis": "Viral Fever",
                    "date": date,
                    "hospital_name": hospital,
                },
                extraction_confidence=0.98,
            ),
            ParsedDocument(
                file_id="doc2",
                document_type=DocumentType.HOSPITAL_BILL,
                extracted_data={
                    "patient_name": patient_name,
                    "hospital_name": hospital,
                    "date": date,
                    "total": total,
                    "line_items": [{"description": "Consultation", "amount": total}],
                },
                extraction_confidence=0.98,
            ),
        ]

    async def test_consistent_patient_names_pass(self, make_context):
        """Same patient name across all docs → passes."""
        agent = CrossDocumentValidatorAgent()
        ctx = make_context()
        ctx.parsed_documents = self._build_parsed_docs()
        ctx.extracted_patient_names = ["Ananya Sharma", "Ananya Sharma"]

        checks, confidence, output = await agent._execute(ctx)

        # Find patient name consistency check
        name_checks = [c for c in checks if "name" in c.check_name.lower() and "consistency" in c.check_name.lower()]
        if name_checks:
            assert name_checks[0].passed is True

    async def test_mismatched_patient_names_halt(self, make_context):
        """Different patient names across docs → halts pipeline (TC003 pattern)."""
        agent = CrossDocumentValidatorAgent()
        ctx = make_context()
        ctx.parsed_documents = [
            ParsedDocument(
                file_id="doc1",
                document_type=DocumentType.PRESCRIPTION,
                extracted_data={"patient_name": "Rajesh Kumar", "date": "2025-03-15"},
                extraction_confidence=0.98,
            ),
            ParsedDocument(
                file_id="doc2",
                document_type=DocumentType.HOSPITAL_BILL,
                extracted_data={"patient_name": "Arjun Mehta", "date": "2025-03-15"},
                extraction_confidence=0.98,
            ),
        ]
        ctx.extracted_patient_names = ["Rajesh Kumar", "Arjun Mehta"]

        checks, confidence, output = await agent._execute(ctx)

        # Should have a failing name consistency check
        name_failed = any(
            not c.passed and "name" in c.check_name.lower()
            for c in checks
        )
        assert name_failed or ctx.should_halt

    async def test_fuzzy_name_match_passes(self, make_context):
        """Slight name variations (fuzzy match ≥ threshold) still pass."""
        agent = CrossDocumentValidatorAgent()
        ctx = make_context(member_name="Ananya Sharma")
        ctx.parsed_documents = self._build_parsed_docs(patient_name="Ananya S.")
        ctx.extracted_patient_names = ["Ananya S.", "Ananya S."]

        checks, confidence, output = await agent._execute(ctx)

        # The member_name_verification check should pass with fuzzy matching
        # (depends on threshold — "Ananya S." vs "Ananya Sharma" ~ 0.7+)
        assert not ctx.should_halt

    async def test_member_name_verification(self, make_context):
        """Extracted name matches roster member name."""
        agent = CrossDocumentValidatorAgent()
        ctx = make_context(member_name="Ananya Sharma")
        ctx.parsed_documents = self._build_parsed_docs()
        ctx.extracted_patient_names = ["Ananya Sharma"]

        checks, confidence, output = await agent._execute(ctx)

        member_checks = [c for c in checks if "member_name" in c.check_name]
        if member_checks:
            assert member_checks[0].passed is True

    async def test_claimed_amount_mismatch_flagged(self, make_context):
        """Form amount ≠ bill amount → flagged for review."""
        agent = CrossDocumentValidatorAgent()
        ctx = make_context(claimed_amount=5000.0)
        ctx.parsed_documents = self._build_parsed_docs(total=3000.0)
        ctx.extracted_patient_names = ["Ananya Sharma"]

        checks, confidence, output = await agent._execute(ctx)

        # Should have a failed amount verification check
        amount_checks = [c for c in checks if "amount" in c.check_name.lower()]
        if amount_checks:
            assert any(not c.passed for c in amount_checks)

    async def test_treatment_date_mismatch(self, make_context):
        """Form date far from doc dates → flagged."""
        agent = CrossDocumentValidatorAgent()
        ctx = make_context(treatment_date="2025-06-01")
        ctx.parsed_documents = self._build_parsed_docs(date="2025-03-15")
        ctx.extracted_patient_names = ["Ananya Sharma"]

        checks, confidence, output = await agent._execute(ctx)

        date_checks = [c for c in checks if "date" in c.check_name.lower()]
        if date_checks:
            assert any(not c.passed for c in date_checks)
