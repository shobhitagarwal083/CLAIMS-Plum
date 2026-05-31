"""
Unit tests for Agent 3: Document Parser.

Tests structured extraction from pre-provided content (test mode),
AI-based extraction (mocked), and failure handling.
"""

import pytest

from app.agents.document_parser import DocumentParserAgent
from app.models.claim import (
    ClassifiedDocument,
    DocumentInput,
    DocumentQuality,
    DocumentType,
)


@pytest.mark.asyncio
class TestDocumentParser:
    """Tests for the DocumentParserAgent."""

    async def test_parses_preextracted_content(self, make_context):
        """Documents with pre-provided content are parsed without AI."""
        agent = DocumentParserAgent(ai_client=None)
        ctx = make_context()
        # Classify docs first (simulating Agent 1 output)
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="doc1", classified_type=DocumentType.PRESCRIPTION,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
            ClassifiedDocument(
                file_id="doc2", classified_type=DocumentType.HOSPITAL_BILL,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
        ]

        checks, confidence, output = await agent._execute(ctx)

        assert len(ctx.parsed_documents) == 2
        assert confidence > 0.9
        assert output["documents_parsed"] == 2
        # Extracted patient names should be populated
        assert len(ctx.extracted_patient_names) > 0
        assert "Ananya Sharma" in ctx.extracted_patient_names

    async def test_extracts_diagnosis(self, make_context):
        """Diagnosis field is extracted from prescription content."""
        agent = DocumentParserAgent(ai_client=None)
        ctx = make_context()
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="doc1", classified_type=DocumentType.PRESCRIPTION,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
        ]

        await agent._execute(ctx)

        assert ctx.extracted_diagnosis == "Viral Fever"

    async def test_extracts_line_items_from_bill(self, make_context):
        """Line items are extracted from hospital bills."""
        agent = DocumentParserAgent(ai_client=None)
        ctx = make_context()
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="doc2", classified_type=DocumentType.HOSPITAL_BILL,
                quality=DocumentQuality.GOOD, confidence=0.99,
            ),
        ]

        await agent._execute(ctx)

        assert len(ctx.extracted_line_items) > 0
        assert ctx.extracted_line_items[0]["description"] == "Consultation Fee"

    async def test_no_data_returns_empty_extraction(self, make_context):
        """Documents without content or file data return empty extraction."""
        agent = DocumentParserAgent(ai_client=None)
        ctx = make_context(documents=[
            DocumentInput(file_id="empty_doc", file_name="empty.jpg"),
        ])
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="empty_doc", classified_type=DocumentType.UNKNOWN,
                quality=DocumentQuality.GOOD, confidence=0.3,
            ),
        ]

        checks, confidence, output = await agent._execute(ctx)

        assert len(ctx.parsed_documents) == 1
        assert ctx.parsed_documents[0].extracted_data == {}
        assert ctx.parsed_documents[0].extraction_confidence == 0.0

    async def test_ai_extraction_success(self, make_context, mock_ai_client):
        """AI-based extraction populates parsed documents from mocked LLM response."""
        mock_ai_client.complete_json.return_value = {
            "patient_name": "Ananya Sharma",
            "doctor_name": "Dr. Mehta",
            "diagnosis": "Viral Fever",
            "date": "2025-03-15",
        }
        agent = DocumentParserAgent(ai_client=mock_ai_client)
        ctx = make_context(documents=[
            DocumentInput(
                file_id="ai_doc",
                file_name="rx.jpg",
                base64_data="fakebase64",
                mime_type="image/jpeg",
            ),
        ])
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="ai_doc", classified_type=DocumentType.PRESCRIPTION,
                quality=DocumentQuality.GOOD, confidence=0.9,
            ),
        ]

        checks, confidence, output = await agent._execute(ctx)

        assert len(ctx.parsed_documents) == 1
        assert ctx.parsed_documents[0].extracted_data["patient_name"] == "Ananya Sharma"
        assert "Ananya Sharma" in ctx.extracted_patient_names

    async def test_ai_extraction_failure_degrades(self, make_context, mock_ai_client):
        """When AI extraction fails, agent returns empty data with low confidence."""
        mock_ai_client.complete_json.side_effect = Exception("LLM timeout")
        agent = DocumentParserAgent(ai_client=mock_ai_client)
        ctx = make_context(documents=[
            DocumentInput(
                file_id="fail_doc",
                file_name="fail.jpg",
                base64_data="fakebase64",
            ),
        ])
        ctx.classified_documents = [
            ClassifiedDocument(
                file_id="fail_doc", classified_type=DocumentType.HOSPITAL_BILL,
                quality=DocumentQuality.GOOD, confidence=0.9,
            ),
        ]

        checks, confidence, output = await agent._execute(ctx)

        assert len(ctx.parsed_documents) == 1
        assert ctx.parsed_documents[0].extraction_confidence == 0.1
        assert len(ctx.parsed_documents[0].quality_flags) > 0
