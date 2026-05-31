"""
Application Configuration.

Centralized settings management using pydantic-settings. All configuration
is loaded from environment variables with sensible defaults for local development.
Production values are injected via Docker environment or .env file.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings sourced from environment variables.
    
    Hierarchy: environment variable > .env file > default value.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────
    app_name: str = "Plum Claims Processing System"
    app_version: str = "1.0.0"
    debug: bool = False
    environment: str = "development"  # development | staging | production
    log_level: str = "INFO"

    # ── Server ───────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # ── Database ─────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/plum_claims"
    )
    db_echo: bool = False  # SQL logging

    # ── Redis & Celery ───────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    @property
    def celery_broker_url(self) -> str:
        return self.redis_url

    @property
    def celery_result_backend(self) -> str:
        return self.redis_url

    # ── AI Providers ─────────────────────────────────────────────
    # Primary: Google Gemini (best vision, cheapest)
    google_api_key: Optional[str] = None
    google_model: str = "gemini-2.0-flash"
    
    # Fallback: OpenAI GPT-4o-mini
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"

    # Alternative: OpenRouter (supports many vision/text models)
    openrouter_api_key: Optional[str] = None
    openrouter_model: str = "google/gemini-2.5-flash"
    
    # AI Behavior
    ai_temperature: float = 0.1  # Low for consistency
    ai_max_retries: int = 3
    ai_timeout_seconds: float = 60.0

    # ── File Storage ─────────────────────────────────────────────
    upload_dir: str = "./uploads"
    max_file_size_mb: int = 20

    # ── S3 Object Storage ─────────────────────────────────────────
    s3_endpoint_url: Optional[str] = None  # e.g., Supabase/R2 endpoint
    s3_access_key_id: Optional[str] = None
    s3_secret_access_key: Optional[str] = None
    s3_bucket_name: Optional[str] = None
    s3_region: str = "us-east-1"

    # ── Policy ───────────────────────────────────────────────────
    policy_terms_path: str = "./policy_terms.json"
    test_cases_path: str = "./test_cases.json"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def upload_path(self) -> Path:
        path = Path(self.upload_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def active_ai_provider(self) -> str:
        """Determine which AI provider is configured and available."""
        if self.openrouter_api_key:
            return "openrouter"
        if self.google_api_key:
            return "google"
        if self.openai_api_key:
            return "openai"
        return "none"


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings singleton.
    
    Settings are loaded once and reused across the application lifetime.
    Call this instead of instantiating Settings() directly.
    """
    return Settings()
