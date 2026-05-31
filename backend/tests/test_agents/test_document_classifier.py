"""
Unit tests for Agent 1: Document Classifier.

Tests classification in test mode (pre-typed), AI mode (mocked), and failure handling.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from app.agents.document_classifier import DocumentClassifierAgent
from app.models.claim import (
    DocumentInput,
    DocumentQuality,
    DocumentType,
)


@pytest.mark.asyncio
class TestDocumentClassifier:
    """Tests for the DocumentClassifierAgent."""

    async def test_classifies_pretyped_documents(self, make_context):
        """Documents with actual_type set are classified without AI."""
        agent = DocumentClassifierAgent(ai_client=None)
        ctx = make_context()

        checks, confidence, output = await agent._execute(ctx)

        assert len(ctx.classified_documents) == 2
        assert ctx.classified_documents[0].classified_type == DocumentType.PRESCRIPTION
        assert ctx.classified_documents[1].classified_type == DocumentType.HOSPITAL_BILL
        assert all(d.quality == DocumentQuality.GOOD for d in ctx.classified_documents)
        assert confidence > 0.9
        assert output["documents_classified"] == 2

    async def test_classifies_unreadable_quality(self, make_context):
        """Documents with UNREADABLE quality are flagged."""
        agent = DocumentClassifierAgent(ai_client=None)
        ctx = make_context(documents=[
            DocumentInput(
                file_id="bad_doc",
                file_name="blurry.jpg",
                actual_type="HOSPITAL_BILL",
                quality="UNREADABLE",
            ),
        ])

        checks, confidence, output = await agent._execute(ctx)

        assert len(ctx.classified_documents) == 1
        assert ctx.classified_documents[0].quality == DocumentQuality.UNREADABLE
        assert ctx.classified_documents[0].quality_score == 0.0
        # Check that the check for this doc is marked as failed
        assert any(not c.passed for c in checks)

    async def test_classifies_poor_quality(self, make_context):
        """Documents with POOR quality get reduced quality_score."""
        agent = DocumentClassifierAgent(ai_client=None)
        ctx = make_context(documents=[
            DocumentInput(
                file_id="poor_doc",
                file_name="dim.jpg",
                actual_type="PRESCRIPTION",
                quality="POOR",
            ),
        ])

        checks, confidence, output = await agent._execute(ctx)

        assert ctx.classified_documents[0].quality == DocumentQuality.POOR
        assert ctx.classified_documents[0].quality_score == 0.4

    async def test_classifies_unknown_type_without_data(self, make_context):
        """Documents without type or data default to UNKNOWN."""
        agent = DocumentClassifierAgent(ai_client=None)
        ctx = make_context(documents=[
            DocumentInput(file_id="mystery", file_name="unknown.jpg"),
        ])

        checks, confidence, output = await agent._execute(ctx)

        assert ctx.classified_documents[0].classified_type == DocumentType.UNKNOWN
        assert confidence < 0.5

    async def test_ai_classification_success(self, make_context, mock_ai_client):
        """AI-based classification returns correct type from mocked LLM response."""
        mock_ai_client.complete_json.return_value = {
            "document_type": "LAB_REPORT",
            "quality": "GOOD",
            "quality_score": 0.95,
            "confidence": 0.88,
            "reason": "Lab report with test results",
        }
        agent = DocumentClassifierAgent(ai_client=mock_ai_client)
        ctx = make_context(documents=[
            DocumentInput(
                file_id="lab1",
                file_name="lab.jpg",
                base64_data="fakebase64data",
                mime_type="image/jpeg",
            ),
        ])

        checks, confidence, output = await agent._execute(ctx)

        assert ctx.classified_documents[0].classified_type == DocumentType.LAB_REPORT
        assert ctx.classified_documents[0].confidence == 0.88
        mock_ai_client.complete_json.assert_called_once()

    async def test_ai_classification_failure_degrades_gracefully(self, make_context, mock_ai_client):
        """When AI classification fails, the agent returns UNKNOWN with low confidence."""
        mock_ai_client.complete_json.side_effect = Exception("API timeout")
        agent = DocumentClassifierAgent(ai_client=mock_ai_client)
        ctx = make_context(documents=[
            DocumentInput(
                file_id="fail_doc",
                file_name="timeout.jpg",
                base64_data="fakebase64data",
            ),
        ])

        checks, confidence, output = await agent._execute(ctx)

        assert ctx.classified_documents[0].classified_type == DocumentType.UNKNOWN
        assert ctx.classified_documents[0].confidence == 0.2
        assert "UNKNOWN" in output["types_found"]

    async def test_multiple_documents_classified(self, make_context):
        """Multiple documents are all classified independently."""
        agent = DocumentClassifierAgent(ai_client=None)
        ctx = make_context(documents=[
            DocumentInput(file_id="d1", actual_type="PRESCRIPTION", quality="GOOD"),
            DocumentInput(file_id="d2", actual_type="HOSPITAL_BILL", quality="GOOD"),
            DocumentInput(file_id="d3", actual_type="LAB_REPORT", quality="POOR"),
        ])

        checks, confidence, output = await agent._execute(ctx)

        assert output["documents_classified"] == 3
        assert set(output["types_found"]) == {"PRESCRIPTION", "HOSPITAL_BILL", "LAB_REPORT"}
        assert len(checks) == 3
