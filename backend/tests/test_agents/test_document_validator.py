"""
Unit tests for Agent 2: Document Validator.

Tests that the correct documents are required per claim category,
that missing documents halt the pipeline with specific messages,
and that unreadable documents are caught.
"""

import pytest

from app.agents.document_validator import DocumentValidatorAgent
from app.models.claim import (
    ClassifiedDocument,
    DocumentInput,
    DocumentQuality,
    DocumentType,
)


@pytest.mark.asyncio
class TestDocumentValidator:
    """Tests for the DocumentValidatorAgent."""

    async def test_passes_with_correct_consultation_docs(self, make_context, policy_engine):
        """Consultation claim with prescription + bill passes validation."""
        agent = DocumentValidatorAgent(policy_engine=policy_engine)
        ctx = make_context(claim_category="CONSULTATION")
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="d1", classified_type=DocumentType.PRESCRIPTION,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
            ClassifiedDocument(
                file_id="d2", classified_type=DocumentType.HOSPITAL_BILL,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
        ]

        checks, confidence, output = await agent._execute(ctx)

        assert output["validation_passed"] is True
        assert not ctx.should_halt
        assert confidence > 0.9

    async def test_fails_on_missing_required_document(self, make_context, policy_engine):
        """Consultation claim with only prescription (no bill) halts pipeline."""
        agent = DocumentValidatorAgent(policy_engine=policy_engine)
        ctx = make_context(claim_category="CONSULTATION")
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="d1", classified_type=DocumentType.PRESCRIPTION,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
        ]

        checks, confidence, output = await agent._execute(ctx)

        assert output["validation_passed"] is False
        assert ctx.should_halt is True
        assert ctx.is_document_error is True
        assert "HOSPITAL_BILL" in str(output.get("missing_types", []))
        # Error message should be specific
        assert "missing" in ctx.halt_reason.lower() or "Missing" in ctx.halt_reason

    async def test_fails_on_unreadable_document(self, make_context, policy_engine):
        """Unreadable document halts the pipeline before checking types."""
        agent = DocumentValidatorAgent(policy_engine=policy_engine)
        ctx = make_context()
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="d1", classified_type=DocumentType.PRESCRIPTION,
                quality=DocumentQuality.UNREADABLE, confidence=0.3,
                file_name="blurry_rx.jpg",
            ),
        ]

        checks, confidence, output = await agent._execute(ctx)

        assert output["validation_passed"] is False
        assert output["issue"] == "unreadable_document"
        assert ctx.should_halt is True
        assert ctx.is_document_error is True
        assert "re-upload" in ctx.halt_reason.lower()

    async def test_pharmacy_requires_prescription(self, make_context, policy_engine):
        """Pharmacy claim without prescription halts with specific message."""
        agent = DocumentValidatorAgent(policy_engine=policy_engine)
        ctx = make_context(claim_category="PHARMACY")
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="d1", classified_type=DocumentType.PHARMACY_BILL,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
        ]

        checks, confidence, output = await agent._execute(ctx)

        assert output["validation_passed"] is False
        assert ctx.should_halt is True
        # Should mention missing PRESCRIPTION
        assert "PRESCRIPTION" in str(output.get("missing_types", []))

    async def test_dental_without_dental_report_passes_validation(self, make_context, policy_engine):
        """Dental claim without dental report passes validation since it is optional in policy_terms.json."""
        agent = DocumentValidatorAgent(policy_engine=policy_engine)
        ctx = make_context(claim_category="DENTAL")
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="d1", classified_type=DocumentType.HOSPITAL_BILL,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
        ]

        checks, confidence, output = await agent._execute(ctx)

        assert output["validation_passed"] is True
        assert ctx.should_halt is False

    async def test_poor_quality_reduces_confidence(self, make_context, policy_engine):
        """Poor quality docs pass validation but reduce confidence."""
        agent = DocumentValidatorAgent(policy_engine=policy_engine)
        ctx = make_context(claim_category="CONSULTATION")
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="d1", classified_type=DocumentType.PRESCRIPTION,
                quality=DocumentQuality.POOR, confidence=0.6,
            ),
            ClassifiedDocument(
                file_id="d2", classified_type=DocumentType.HOSPITAL_BILL,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
        ]

        checks, confidence, output = await agent._execute(ctx)

        assert output["validation_passed"] is True
        assert confidence < 1.0  # Reduced due to POOR quality
        # Should have a warning check
        warn_checks = [c for c in checks if c.check_name == "document_quality_warning"]
        assert len(warn_checks) == 1

    async def test_wrong_document_type_caught(self, make_context, policy_engine):
        """Two prescriptions instead of prescription + bill are caught (TC001 pattern)."""
        agent = DocumentValidatorAgent(policy_engine=policy_engine)
        ctx = make_context(claim_category="CONSULTATION")
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="d1", classified_type=DocumentType.PRESCRIPTION,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
            ClassifiedDocument(
                file_id="d2", classified_type=DocumentType.PRESCRIPTION,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
        ]

        checks, confidence, output = await agent._execute(ctx)

        assert output["validation_passed"] is False
        assert ctx.should_halt is True
        assert "HOSPITAL_BILL" in str(output.get("missing_types", []))
