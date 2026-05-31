"""
AI Exception Hierarchy.

Clean exception types for the AI layer so callers can handle
specific failure modes without parsing error strings.
"""

from __future__ import annotations


class AIError(Exception):
    """Base exception for all AI-related errors."""

    def __init__(self, message: str, provider: str = "unknown", retryable: bool = False):
        self.provider = provider
        self.retryable = retryable
        super().__init__(message)


class AuthenticationError(AIError):
    """API key is invalid or missing."""

    def __init__(self, provider: str, message: str = ""):
        super().__init__(
            message or f"Authentication failed for provider '{provider}'.",
            provider=provider,
            retryable=False,
        )


class RateLimitError(AIError):
    """Provider rate limit hit — safe to retry after backoff."""

    def __init__(self, provider: str, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__(
            f"Rate limit exceeded for provider '{provider}'.",
            provider=provider,
            retryable=True,
        )


class TimeoutError(AIError):
    """Request timed out — safe to retry."""

    def __init__(self, provider: str, timeout_seconds: float):
        super().__init__(
            f"Request to '{provider}' timed out after {timeout_seconds}s.",
            provider=provider,
            retryable=True,
        )


class InvalidRequestError(AIError):
    """Request payload was malformed — do NOT retry."""

    def __init__(self, provider: str, detail: str = ""):
        super().__init__(
            f"Invalid request to '{provider}': {detail}",
            provider=provider,
            retryable=False,
        )


class RetryExhaustedError(AIError):
    """All retry attempts failed."""

    def __init__(self, attempts: int, last_error: Exception | None = None):
        self.attempts = attempts
        self.last_error = last_error
        msg = f"All {attempts} retry attempts exhausted."
        if last_error:
            msg += f" Last error: {last_error}"
        super().__init__(msg, retryable=False)


class ContentFilterError(AIError):
    """Response was blocked by content filter."""

    def __init__(self, provider: str):
        super().__init__(
            f"Response blocked by content filter on '{provider}'.",
            provider=provider,
            retryable=False,
        )
