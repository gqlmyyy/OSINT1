from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.config import get_settings
from app.providers.registry import get_registry

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(session: SessionDep) -> dict[str, Any]:
    settings = get_settings()
    try:
        await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # pragma: no cover - only on a broken deployment
        database = f"error: {type(exc).__name__}"
    registry = get_registry()
    return {
        "status": "ok" if database == "ok" else "degraded",
        "app": settings.app_name,
        "env": settings.env,
        "database": database,
        "queue": "redis" if settings.redis_url else "inline",
        "providers": len(registry),
        "provider_names": registry.names(),
        "plugin_errors": [e.plugin for e in registry.errors],
        "ai_enabled": settings.ai_enabled,
    }
