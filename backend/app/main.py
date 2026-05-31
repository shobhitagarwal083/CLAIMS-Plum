"""
Plum Claims Processing System — Main Application.

FastAPI application with:
- CORS configuration for frontend
- Lifespan management (DB init/shutdown)
- Pipeline initialization (AI client, policy engine)
- Route registration
- Structured logging

Production patterns:
- Singleton pattern for pipeline executor and claim service
- Graceful startup/shutdown via lifespan context manager
- AI provider auto-detection from environment
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai import ModelClient
from app.config import get_settings
from app.db import close_db, init_db
from app.pipeline.executor import PipelineExecutor
from app.policy import PolicyRulesEngine
from app.routes.claims import router as claims_router
from app.routes.eval import router as eval_router
from app.routes.health import health_router, policy_router
from app.routes.review import router as review_router
from app.services.claim_service import ClaimService

# ── Logging Setup ────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)-30s │ %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)

# ── Singleton Services ───────────────────────────────────────────────

_claim_service: Optional[ClaimService] = None


def get_app_service() -> ClaimService:
    """Get the singleton ClaimService. Called by route handlers."""
    if _claim_service is None:
        raise RuntimeError("ClaimService not initialized. App not started?")
    return _claim_service


# ── Lifespan ─────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown handlers."""
    global _claim_service

    settings = get_settings()
    logger.info("═══════════════════════════════════════════════════════")
    logger.info("  %s v%s", settings.app_name, settings.app_version)
    logger.info("  Environment: %s", settings.environment)
    logger.info("  AI Provider: %s", settings.active_ai_provider)
    logger.info("═══════════════════════════════════════════════════════")

    # Initialize database
    await init_db()

    # Initialize AI client (optional — works without it for test mode)
    ai_client = _init_ai_client(settings)

    # Initialize policy engine
    policy_engine = PolicyRulesEngine(settings.policy_terms_path)

    # Initialize pipeline
    pipeline = PipelineExecutor(
        policy_engine=policy_engine,
        ai_client=ai_client,
    )

    # Initialize service
    _claim_service = ClaimService(pipeline=pipeline)

    logger.info("✓ All services initialized. Ready to process claims.")

    yield

    # Shutdown
    await close_db()
    _claim_service = None
    logger.info("✓ Shutdown complete.")


def _init_ai_client(settings) -> Optional[ModelClient]:
    """Initialize AI client from environment configuration with failover support."""
    try:
        providers = []
        if settings.google_api_key:
            providers.append({
                "provider": "google",
                "api_key": settings.google_api_key,
                "model": settings.google_model,
            })
        if settings.openai_api_key:
            providers.append({
                "provider": "openai",
                "api_key": settings.openai_api_key,
                "model": settings.openai_model,
            })
        if settings.openrouter_api_key:
            providers.append({
                "provider": "openrouter",
                "api_key": settings.openrouter_api_key,
                "model": settings.openrouter_model,
            })

        if not providers:
            logger.warning(
                "⚠ No AI API key configured. Running in test-only mode "
                "(documents with pre-provided content will work, real OCR will not)."
            )
            return None

        primary = providers[0]
        fallbacks = providers[1:]

        client = ModelClient(
            provider=primary["provider"],
            api_key=primary["api_key"],
            model=primary.get("model"),
            fallback_providers=fallbacks,
            temperature=settings.ai_temperature,
            max_retries=settings.ai_max_retries,
            timeout=settings.ai_timeout_seconds,
        )
        logger.info(
            "✓ AI Client initialized: primary=%s, fallbacks=%s",
            client.provider.value,
            [c.provider.value for c in client.fallback_clients],
        )
        return client

    except Exception as exc:
        logger.error("Failed to initialize AI client: %s", exc)
        return None


# ── App Creation ─────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "AI-powered health insurance claims processing system. "
            "Multi-agent pipeline with full explainability and observability."
        ),
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    app.include_router(health_router)
    app.include_router(policy_router)
    app.include_router(claims_router)
    app.include_router(review_router)
    app.include_router(eval_router)

    return app


# Create the app instance
app = create_app()
