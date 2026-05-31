"""
Unified AI Model Client.

Multi-provider client supporting Google Gemini and OpenAI with automatic
failover, retry logic, and structured output handling.

Ported from the SuperNodes ai_common/model_client.py pattern but simplified
for the claims processing use case. Supports both text and vision (multi-modal)
requests needed for document OCR.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .exceptions import AIError, AuthenticationError, InvalidRequestError
from .retry_handler import RetryHandler

logger = logging.getLogger(__name__)


# ── Response Model ───────────────────────────────────────────────────


@dataclass
class AIResponse:
    """Normalized response from any AI provider."""

    content: str
    provider: str
    model: str
    usage: dict[str, int] = field(default_factory=lambda: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    })
    latency_ms: float = 0.0


# ── Provider Enum ────────────────────────────────────────────────────


class AIProvider(str, Enum):
    GOOGLE = "google"
    OPENAI = "openai"
    OPENROUTER = "openrouter"


# ── Model Client ─────────────────────────────────────────────────────


class ModelClient:
    """
    Unified AI client with multi-provider support, retry logic,
    and vision (multi-modal) capabilities.

    Usage:
        client = ModelClient(provider="google", api_key="...")
        
        # Text completion
        response = await client.complete("Classify this document...")
        
        # Vision (document OCR)
        response = await client.complete_with_vision(
            prompt="Extract all fields...",
            image_base64="...",
            mime_type="image/jpeg",
        )
        
        # Structured JSON output
        data = await client.complete_json(
            prompt="Extract structured data...",
            schema={"patient_name": "string", ...},
        )
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str | None = None,
        fallback_providers: list[dict] | None = None,
        temperature: float = 0.1,
        max_retries: int = 3,
        timeout: float = 60.0,
    ):
        self.provider = AIProvider(provider.lower())
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout
        self._retry = RetryHandler(max_retries=max_retries)

        # Set default model per provider
        if model:
            self.model = model
        elif self.provider == AIProvider.GOOGLE:
            self.model = "gemini-2.0-flash"
        elif self.provider == AIProvider.OPENROUTER:
            self.model = "google/gemini-2.0-flash"
        else:
            self.model = "gpt-4o-mini"

        # Validate key is present
        if not api_key:
            raise AuthenticationError(provider=self.provider.value)

        # Initialize fallback clients
        self.fallback_providers = fallback_providers or []
        self.fallback_clients = []
        for fb in self.fallback_providers:
            fb_provider = fb.get("provider")
            fb_key = fb.get("api_key")
            if not fb_provider or not fb_key:
                continue
            try:
                client = ModelClient(
                    provider=fb_provider,
                    api_key=fb_key,
                    model=fb.get("model"),
                    fallback_providers=None,  # Avoid recursion
                    temperature=temperature,
                    max_retries=max_retries,
                    timeout=timeout,
                )
                self.fallback_clients.append(client)
            except Exception as e:
                logger.error(
                    "Failed to initialize fallback client for %s: %s",
                    fb_provider,
                    e,
                )

        logger.info(
            "ModelClient initialized: provider=%s, model=%s, fallbacks=%s",
            self.provider.value,
            self.model,
            [c.provider.value for c in self.fallback_clients],
        )

    # ── Public API ───────────────────────────────────────────────

    async def complete(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 4000,
        images: list[dict] | None = None,
    ) -> AIResponse:
        """Text completion with retry and failover."""
        try:
            return await self._retry.execute(
                self._do_complete,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens,
                images=images,
            )
        except Exception as primary_exc:
            if not self.fallback_clients:
                raise primary_exc

            logger.warning(
                "Primary provider %s failed with %s. Trying fallback chain...",
                self.provider.value,
                primary_exc,
            )
            for client in self.fallback_clients:
                try:
                    logger.info(
                        "Attempting fallback to provider %s (model %s)",
                        client.provider.value,
                        client.model,
                    )
                    return await client.complete(
                        prompt,
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        images=images,
                    )
                except Exception as fb_exc:
                    logger.warning(
                        "Fallback provider %s failed: %s",
                        client.provider.value,
                        fb_exc,
                    )
                    continue
            raise primary_exc

    async def complete_with_vision(
        self,
        prompt: str,
        *,
        image_base64: str | None = None,
        image_path: str | None = None,
        mime_type: str = "image/jpeg",
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 4000,
    ) -> AIResponse:
        """
        Vision (multi-modal) completion for document OCR with failover.
        
        Accepts either base64-encoded image data or a file path.
        """
        # Resolve image data
        b64_data = image_base64
        if not b64_data and image_path:
            path = Path(image_path)
            if not path.exists():
                raise InvalidRequestError(
                    self.provider.value,
                    f"Image file not found: {image_path}",
                )
            b64_data = base64.b64encode(path.read_bytes()).decode("utf-8")
            # Infer MIME type from extension
            ext = path.suffix.lower()
            mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf", ".webp": "image/webp"}
            mime_type = mime_map.get(ext, mime_type)

        if not b64_data:
            raise InvalidRequestError(
                self.provider.value,
                "Either image_base64 or image_path must be provided for vision.",
            )

        images = [{"base64": b64_data, "mime_type": mime_type}]

        try:
            return await self._retry.execute(
                self._do_complete,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens,
                images=images,
            )
        except Exception as primary_exc:
            if not self.fallback_clients:
                raise primary_exc

            logger.warning(
                "Primary provider %s failed with %s for vision. Trying fallback chain...",
                self.provider.value,
                primary_exc,
            )
            for client in self.fallback_clients:
                try:
                    logger.info(
                        "Attempting vision fallback to provider %s (model %s)",
                        client.provider.value,
                        client.model,
                    )
                    return await client.complete_with_vision(
                        prompt,
                        image_base64=image_base64,
                        image_path=image_path,
                        mime_type=mime_type,
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                except Exception as fb_exc:
                    logger.warning(
                        "Fallback provider %s failed for vision: %s",
                        client.provider.value,
                        fb_exc,
                    )
                    continue
            raise primary_exc

    async def complete_json(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 4000,
        images: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Complete and parse JSON response with auto-fixing.

        Appends JSON instruction to prompt, attempts to parse response,
        and if parsing fails, makes one more attempt with error feedback.
        """
        json_instruction = (
            "\n\nIMPORTANT: Return ONLY valid JSON with no additional text, "
            "no markdown formatting, no code blocks. Just the raw JSON object."
        )
        full_prompt = prompt + json_instruction

        response = await self.complete(
            full_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            images=images,
        )

        # Attempt to parse
        parsed = self._try_parse_json(response.content)
        if parsed is not None:
            return parsed

        # Auto-fix attempt: feed the error back to the model
        logger.warning("JSON parse failed on first attempt, trying auto-fix...")
        fix_prompt = (
            f"The following was supposed to be valid JSON but failed to parse:\n\n"
            f"{response.content}\n\n"
            f"Please fix it and return ONLY the valid JSON. No explanation, no markdown."
        )
        fix_response = await self.complete(
            fix_prompt,
            temperature=0.0,  # Deterministic for fixing
            max_tokens=max_tokens,
            images=images,
        )

        parsed = self._try_parse_json(fix_response.content)
        if parsed is not None:
            return parsed

        # Last resort: try to extract JSON from markdown code blocks
        raise InvalidRequestError(
            self.provider.value,
            f"Failed to parse JSON response after auto-fix. Raw: {fix_response.content[:500]}",
        )

    # ── Provider Dispatch ────────────────────────────────────────

    async def _do_complete(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int,
        images: list[dict] | None,
    ) -> AIResponse:
        """Dispatch to the correct provider implementation."""
        start = time.monotonic()

        if self.provider == AIProvider.GOOGLE:
            result = await self._complete_google(
                prompt, system_prompt, temperature, max_tokens, images
            )
        elif self.provider == AIProvider.OPENAI:
            result = await self._complete_openai(
                prompt, system_prompt, temperature, max_tokens, images
            )
        elif self.provider == AIProvider.OPENROUTER:
            result = await self._complete_openrouter(
                prompt, system_prompt, temperature, max_tokens, images
            )
        else:
            raise InvalidRequestError(self.provider.value, "Unsupported provider")

        result.latency_ms = round((time.monotonic() - start) * 1000, 2)
        return result

    # ── Google Gemini ────────────────────────────────────────────

    async def _complete_google(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int,
        images: list[dict] | None,
    ) -> AIResponse:
        """Google Gemini API call (supports vision natively)."""
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)

            # Build contents
            parts: list[Any] = []

            # Add images first if present (vision mode)
            if images:
                for img in images:
                    parts.append(
                        types.Part.from_bytes(
                            data=base64.b64decode(img["base64"]),
                            mime_type=img["mime_type"],
                        )
                    )

            # Add text prompt
            parts.append(types.Part.from_text(text=prompt))

            contents = [types.Content(role="user", parts=parts)]

            # Config
            config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            if system_prompt:
                config.system_instruction = system_prompt

            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )

            # Extract text
            text = response.text or ""

            # Extract usage
            usage = {}
            if response.usage_metadata:
                usage = {
                    "prompt_tokens": response.usage_metadata.prompt_token_count or 0,
                    "completion_tokens": response.usage_metadata.candidates_token_count or 0,
                    "total_tokens": response.usage_metadata.total_token_count or 0,
                }

            return AIResponse(
                content=text,
                provider="google",
                model=self.model,
                usage=usage,
            )

        except ImportError:
            raise AIError("google-genai package not installed", provider="google")
        except Exception as e:
            error_str = str(e).lower()
            if "api key" in error_str or "authenticate" in error_str or "401" in error_str:
                raise AuthenticationError("google")
            if "429" in error_str or "rate" in error_str:
                from .exceptions import RateLimitError
                raise RateLimitError("google")
            if "timeout" in error_str:
                from .exceptions import TimeoutError
                raise TimeoutError("google", self.timeout)
            raise AIError(f"Google API error: {e}", provider="google", retryable=True)

    # ── OpenAI ───────────────────────────────────────────────────

    async def _complete_openai(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int,
        images: list[dict] | None,
    ) -> AIResponse:
        """OpenAI API call (supports vision via GPT-4o)."""
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout)

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

            # Build user message
            if images:
                content_parts: list[dict] = [{"type": "text", "text": prompt}]
                for img in images:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img['mime_type']};base64,{img['base64']}",
                        },
                    })
                messages.append({"role": "user", "content": content_parts})
            else:
                messages.append({"role": "user", "content": prompt})

            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            choice = response.choices[0]
            text = choice.message.content or ""

            usage = {}
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens or 0,
                    "completion_tokens": response.usage.completion_tokens or 0,
                    "total_tokens": response.usage.total_tokens or 0,
                }

            return AIResponse(
                content=text,
                provider="openai",
                model=response.model or self.model,
                usage=usage,
            )

        except ImportError:
            raise AIError("openai package not installed", provider="openai")
        except Exception as e:
            error_str = str(e).lower()
            if "api key" in error_str or "authenticate" in error_str or "401" in error_str:
                raise AuthenticationError("openai")
            if "429" in error_str or "rate" in error_str:
                from .exceptions import RateLimitError
                raise RateLimitError("openai")
            if "timeout" in error_str:
                from .exceptions import TimeoutError
                raise TimeoutError("openai", self.timeout)
            raise AIError(f"OpenAI API error: {e}", provider="openai", retryable=True)

    # ── JSON Parsing ─────────────────────────────────────────────

    @staticmethod
    def _try_parse_json(text: str) -> dict[str, Any] | list | None:
        """
        Attempt to parse JSON from LLM response, handling common issues
        like markdown code blocks, trailing text, etc.
        """
        if not text:
            return None

        cleaned = text.strip()

        # Try direct parse first
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Strip markdown code blocks
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first line (```json or ```) and last line (```)
            inner_lines = []
            started = False
            for line in lines:
                if not started and line.strip().startswith("```"):
                    started = True
                    continue
                if started and line.strip() == "```":
                    break
                if started:
                    inner_lines.append(line)
            cleaned = "\n".join(inner_lines).strip()

            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

        # Try to find JSON object or array in the text
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start_idx = cleaned.find(start_char)
            end_idx = cleaned.rfind(end_char)
            if start_idx != -1 and end_idx > start_idx:
                candidate = cleaned[start_idx:end_idx + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass

        return None

    # ── OpenRouter ───────────────────────────────────────────────

    async def _complete_openrouter(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int,
        images: list[dict] | None,
    ) -> AIResponse:
        """OpenRouter API call (using OpenAI SDK compatible endpoints)."""
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=self.api_key,
                base_url="https://openrouter.ai/api/v1",
                timeout=self.timeout
            )

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

            # Build user message
            if images:
                content_parts: list[dict] = [{"type": "text", "text": prompt}]
                for img in images:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img['mime_type']};base64,{img['base64']}",
                        },
                    })
                messages.append({"role": "user", "content": content_parts})
            else:
                messages.append({"role": "user", "content": prompt})

            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_headers={
                    "HTTP-Referer": "https://github.com/Antigravity",
                    "X-Title": "Plum Claims Platform"
                }
            )

            choice = response.choices[0]
            text = choice.message.content or ""

            usage = {}
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens or 0,
                    "completion_tokens": response.usage.completion_tokens or 0,
                    "total_tokens": response.usage.total_tokens or 0,
                }

            return AIResponse(
                content=text,
                provider="openrouter",
                model=response.model or self.model,
                usage=usage,
            )

        except ImportError:
            raise AIError("openai package not installed", provider="openrouter")
        except Exception as e:
            error_str = str(e).lower()
            if "api key" in error_str or "authenticate" in error_str or "401" in error_str:
                raise AuthenticationError("openrouter")
            if "429" in error_str or "rate" in error_str:
                from .exceptions import RateLimitError
                raise RateLimitError("openrouter")
            if "timeout" in error_str:
                from .exceptions import TimeoutError
                raise TimeoutError("openrouter", self.timeout)
            raise AIError(f"OpenRouter API error: {e}", provider="openrouter", retryable=True)
