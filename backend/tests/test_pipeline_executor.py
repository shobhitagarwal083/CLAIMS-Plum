"""
Integration tests for the Pipeline Executor.
"""

import pytest

from app.pipeline.executor import PipelineExecutor
from app.models.claim import DocumentType
from app.models.trace import AgentStatus


@pytest.mark.asyncio
class TestPipelineExecutor:
    """Tests for the PipelineExecutor."""

    async def test_full_pipeline_happy_path(self, make_request, policy_engine, mock_ai_client):
        """A well-formed claim submission goes through the entire pipeline and gets approved."""
        executor = PipelineExecutor(policy_engine=policy_engine, ai_client=mock_ai_client)
        
        # consultation claim (EMP001) for ₹3,000, treatment date in 2024 to bypass deadline checks
        req = make_request(
            member_id="EMP001",
            claim_category="CONSULTATION",
            claimed_amount=3000.0,
            treatment_date="2024-11-15",
            hospital_name="Apollo Hospitals",
        )

        output = await executor.execute(req)

        # Rajesh Kumar (EMP001) is active, consultation limit is ₹2,000, 
        # so it should be approved but capped at ₹2,000, with copay/discount applied.
        assert output.decision in ("APPROVED", "PARTIAL")
        assert output.approved_amount > 0
        assert output.approved_amount <= 3000.0
        assert output.confidence_score > 0.5
        
        # Verify trace has all 7 agents executed
        trace_steps = output.execution_trace
        agent_types = [step["agent_type"] for step in trace_steps]
        assert "document_classifier" in agent_types
        assert "document_validator" in agent_types
        assert "document_parser" in agent_types
        assert "cross_document_validator" in agent_types
        assert "policy_evaluator" in agent_types
        assert "claim_adjudicator" in agent_types
        assert "fraud_detector" in agent_types

    async def test_pipeline_halts_on_document_error(self, make_request, policy_engine, mock_ai_client):
        """Pipeline halts immediately on document validator failure (missing required doc)."""
        executor = PipelineExecutor(policy_engine=policy_engine, ai_client=mock_ai_client)
        
        # consultation claim needs prescription + bill. We only provide prescription.
        docs = [
            {
                "file_id": "doc1",
                "file_name": "prescription.jpg",
                "actual_type": "PRESCRIPTION",
                "quality": "GOOD",
                "content": {"patient_name": "Rajesh Kumar", "date": "2024-11-15"},
            }
        ]
        req = make_request(
            member_id="EMP001",
            claim_category="CONSULTATION",
            treatment_date="2024-11-15",
            documents=docs,
        )

        output = await executor.execute(req)

        # If validator fails, it sets should_halt = True, is_document_error = True.
        # No decision is made.
        assert output.decision is None
        assert output.is_document_error is True
        assert len(output.document_issues) > 0
        
        # Trace should halt after document validator
        trace_steps = output.execution_trace
        agent_types = [step["agent_type"] for step in trace_steps]
        assert "document_classifier" in agent_types
        assert "document_validator" in agent_types
        assert "document_parser" not in agent_types  # Halted early!

    async def test_pipeline_degrades_gracefully_on_agent_failure(self, make_request, policy_engine, mock_ai_client):
        """If an agent fails, the pipeline logs the failure in the trace and continues in degraded mode."""
        executor = PipelineExecutor(policy_engine=policy_engine, ai_client=mock_ai_client)
        
        # We enable simulate_component_failure which causes Fraud Detector to fail.
        req = make_request(
            member_id="EMP001",
            claim_category="CONSULTATION",
            treatment_date="2024-11-15",
            simulate_component_failure=True,
        )

        output = await executor.execute(req)

        # Pipeline should still complete and have a decision
        assert output.decision is not None
        
        # Fraud detector step in trace should be FAILED
        fraud_step = [step for step in output.execution_trace if step["agent_type"] == "fraud_detector"][0]
        assert fraud_step["status"] == "failed"

    async def test_fraud_override_to_manual_review(self, make_request, policy_engine, mock_ai_client):
        """High fraud score overrides approval decision to MANUAL_REVIEW."""
        executor = PipelineExecutor(policy_engine=policy_engine, ai_client=mock_ai_client)
        
        # We submit a duplicate claim to trigger a high fraud score (adds 0.8 to score, threshold is 0.8).
        history = [
            {
                "claim_id": "prev_claim_id",
                "date": "2024-11-15",
                "amount": 3000.0,
                "status": "completed",
                "claim_category": "CONSULTATION",
            }
        ]
        
        req = make_request(
            member_id="EMP001",
            claim_category="CONSULTATION",
            claimed_amount=3000.0,
            treatment_date="2024-11-15",
            claims_history=history,
        )

        output = await executor.execute(req)

        # Adjudicator would approve, but fraud detector should trigger duplicate check and override to MANUAL_REVIEW.
        assert output.decision == "MANUAL_REVIEW"
        assert output.manual_review_recommended is True
        assert any("DUPLICATE_CLAIM" in sig for sig in output.fraud_signals)
