"""
Retry Handler with Exponential Backoff.

Ported from the SuperNodes production platform (ai_common/retry_handler.py).
Handles transient failures (rate limits, timeouts, network errors) with
exponential backoff + jitter to prevent thundering herd.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Callable, TypeVar

from .exceptions import (
    AIError,
    AuthenticationError,
    InvalidRequestError,
    RateLimitError,
    RetryExhaustedError,
    TimeoutError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryHandler:
    """
    Async retry handler with exponential backoff and jitter.
    
    Production patterns:
    - Exponential backoff: delay doubles each attempt
    - Jitter: randomize delay ±50% to prevent thundering herd
    - Selective retry: only retryable errors trigger retry
    - Detailed logging: every attempt logged with context
    """

    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay with exponential backoff + optional jitter."""
        delay = min(
            self.initial_delay * (self.exponential_base ** attempt),
            self.max_delay,
        )
        if self.jitter:
            delay *= 0.5 + random.random() * 0.5
        return delay

    def _is_retryable(self, exc: Exception) -> bool:
        """Determine if the exception is safe to retry."""
        # AIError subclasses carry their own retryable flag
        if isinstance(exc, AIError):
            return exc.retryable

        # Standard network errors are retryable
        if isinstance(exc, (ConnectionError, asyncio.TimeoutError, OSError)):
            return True

        # Authentication and invalid request errors are NOT retryable
        if isinstance(exc, (AuthenticationError, InvalidRequestError)):
            return False

        return False

    async def execute(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute an async function with retry logic.

        Args:
            func: Async callable to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result from func

        Raises:
            RetryExhaustedError: If all retries fail
            AIError: If a non-retryable error occurs
        """
        last_exception: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                result = await func(*args, **kwargs)
                if attempt > 0:
                    logger.info(
                        "Request succeeded on attempt %d/%d",
                        attempt + 1,
                        self.max_retries + 1,
                    )
                return result

            except Exception as exc:
                last_exception = exc

                if not self._is_retryable(exc):
                    logger.error("Non-retryable error (attempt %d): %s", attempt + 1, exc)
                    raise

                if attempt >= self.max_retries:
                    logger.error(
                        "All %d attempts failed. Last error: %s",
                        self.max_retries + 1,
                        exc,
                    )
                    raise RetryExhaustedError(
                        attempts=self.max_retries + 1,
                        last_error=exc,
                    ) from exc

                delay = self._calculate_delay(attempt)

                # Special handling for rate limits with retry-after header
                if isinstance(exc, RateLimitError) and exc.retry_after:
                    delay = max(delay, exc.retry_after)

                logger.warning(
                    "Attempt %d/%d failed: %s. Retrying in %.2fs...",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        # Should never reach here, but safety net
        raise RetryExhaustedError(
            attempts=self.max_retries + 1,
            last_error=last_exception,
        )
