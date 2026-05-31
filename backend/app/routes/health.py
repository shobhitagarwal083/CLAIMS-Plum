"""
Health and Policy API Routes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.config import get_settings

# ── Health Check ─────────────────────────────────────────────────────

health_router = APIRouter(tags=["Health"])


@health_router.get("/health", summary="Health check")
async def health_check() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "ai_provider": settings.active_ai_provider,
    }


# ── Policy Routes ────────────────────────────────────────────────────

policy_router = APIRouter(prefix="/api/policy", tags=["Policy"])


@policy_router.get(
    "",
    summary="Get policy terms",
    description="Returns the full policy configuration used by the system.",
)
async def get_policy() -> dict[str, Any]:
    settings = get_settings()
    path = Path(settings.policy_terms_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="policy_terms.json not found")
    with open(path) as f:
        return json.load(f)


@policy_router.get(
    "/members",
    summary="List all policy members",
)
async def list_members() -> list[dict[str, Any]]:
    settings = get_settings()
    path = Path(settings.policy_terms_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="policy_terms.json not found")
    with open(path) as f:
        data = json.load(f)
    return data.get("members", [])


@policy_router.get(
    "/members/{member_id}",
    summary="Get member details",
)
async def get_member(member_id: str) -> dict[str, Any]:
    settings = get_settings()
    path = Path(settings.policy_terms_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="policy_terms.json not found")
    with open(path) as f:
        data = json.load(f)
    
    member = next(
        (m for m in data.get("members", []) if m["member_id"] == member_id),
        None,
    )
    if not member:
        raise HTTPException(status_code=404, detail=f"Member '{member_id}' not found")
    return member


@policy_router.get(
    "/categories",
    summary="List coverage categories",
)
async def list_categories() -> dict[str, Any]:
    settings = get_settings()
    path = Path(settings.policy_terms_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="policy_terms.json not found")
    with open(path) as f:
        data = json.load(f)
    return data.get("coverage_categories", {})


@policy_router.get(
    "/document-requirements",
    summary="Get document requirements per category",
)
async def get_document_requirements() -> dict[str, Any]:
    settings = get_settings()
    path = Path(settings.policy_terms_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="policy_terms.json not found")
    with open(path) as f:
        data = json.load(f)
    return data.get("document_requirements", {})
