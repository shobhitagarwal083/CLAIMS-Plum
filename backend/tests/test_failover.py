"""
Unit tests for LLM provider failover chain in ModelClient.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.ai.model_client import ModelClient, AIResponse
from app.ai.exceptions import AIError, RetryExhaustedError

@pytest.mark.asyncio
async def test_text_completion_failover_success():
    """
    Test that complete() fails over to the fallback client when the primary fails.
    """
    primary_provider = "google"
    fallback_provider = "openai"

    fallback_providers = [
        {
            "provider": fallback_provider,
            "api_key": "fake-openai-key",
            "model": "gpt-4o-mini",
        }
    ]

    client = ModelClient(
        provider=primary_provider,
        api_key="fake-google-key",
        model="gemini-2.0-flash",
        fallback_providers=fallback_providers,
        max_retries=1,
    )

    # Mock the primary call to raise a retryable error (e.g. AIError)
    # The RetryHandler will exhaust retries and raise RetryExhaustedError
    with patch.object(client, "_do_complete", side_effect=AIError("Primary failed", provider="google", retryable=True)) as mock_primary_do_complete:
        # Mock the fallback client complete method to succeed
        fallback_client = client.fallback_clients[0]
        mock_response = AIResponse(
            content="Fallback response",
            provider="openai",
            model="gpt-4o-mini",
        )
        fallback_client.complete = AsyncMock(return_value=mock_response)

        response = await client.complete("Hello world")

        assert response.content == "Fallback response"
        assert response.provider == "openai"
        fallback_client.complete.assert_called_once_with(
            "Hello world",
            system_prompt=None,
            temperature=None,
            max_tokens=4000,
            images=None,
        )

@pytest.mark.asyncio
async def test_vision_completion_failover_success():
    """
    Test that complete_with_vision() fails over to the fallback client when the primary fails.
    """
    primary_provider = "google"
    fallback_provider = "openai"

    fallback_providers = [
        {
            "provider": fallback_provider,
            "api_key": "fake-openai-key",
            "model": "gpt-4o-mini",
        }
    ]

    client = ModelClient(
        provider=primary_provider,
        api_key="fake-google-key",
        model="gemini-2.0-flash",
        fallback_providers=fallback_providers,
        max_retries=1,
    )

    # Mock primary to fail
    with patch.object(client, "_do_complete", side_effect=AIError("Primary failed", provider="google", retryable=True)):
        fallback_client = client.fallback_clients[0]
        mock_response = AIResponse(
            content="Fallback vision response",
            provider="openai",
            model="gpt-4o-mini",
        )
        fallback_client.complete_with_vision = AsyncMock(return_value=mock_response)

        response = await client.complete_with_vision(
            prompt="Describe this image",
            image_base64="ZHVtbXk=",  # base64 for dummy
        )

        assert response.content == "Fallback vision response"
        assert response.provider == "openai"
        fallback_client.complete_with_vision.assert_called_once_with(
            "Describe this image",
            image_base64="ZHVtbXk=",
            image_path=None,
            mime_type="image/jpeg",
            system_prompt=None,
            temperature=None,
            max_tokens=4000,
        )

@pytest.mark.asyncio
async def test_all_failover_clients_fail_raises_original_error():
    """
    Test that if the primary and all fallbacks fail, the original error is raised.
    """
    primary_provider = "google"
    fallback_provider = "openai"

    fallback_providers = [
        {
            "provider": fallback_provider,
            "api_key": "fake-openai-key",
            "model": "gpt-4o-mini",
        }
    ]

    client = ModelClient(
        provider=primary_provider,
        api_key="fake-google-key",
        model="gemini-2.0-flash",
        fallback_providers=fallback_providers,
        max_retries=1,
    )

    # Mock primary to fail with AIError
    with patch.object(client, "_do_complete", side_effect=AIError("Primary failed", provider="google", retryable=False)) as mock_primary:
        # Mock fallback to also fail
        fallback_client = client.fallback_clients[0]
        fallback_client.complete = AsyncMock(side_effect=AIError("Fallback failed", provider="openai"))

        with pytest.raises(AIError) as exc_info:
            await client.complete("Hello world")

        assert "Primary failed" in str(exc_info.value)
