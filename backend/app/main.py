"""FastAPI application factory."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.routes import ai, analytics, auth, demo, entities, health, investigations, sources, ws
from app.core.config import get_settings
from app.core.db import create_all, dispose_engine, get_sessionmaker
from app.core.logging import configure_logging
from app.jobs.queue import get_queue
from app.providers.registry import get_registry
from app.security.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from app.services.source_registry import SourceRegistryService

logger = logging.getLogger(__name__)

DESCRIPTION = """
Evidence-first, graph-centric OSINT platform for **public** information.

Every result carries a source, a timestamp, a confidence and an evidence URL, and is
classified as `observed`, `inferred`, `correlated` or `unverified`. The platform performs
no authentication bypass, no CAPTCHA solving, no credential testing and no access to
private profiles.
"""


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()

    if settings.database_url.startswith("sqlite"):
        # Single-file deployments and the demo image bootstrap their own schema;
        # PostgreSQL deployments run Alembic migrations at container start.
        await create_all()

    registry = get_registry()
    logger.info(
        "loaded %d provider(s): %s", len(registry), ", ".join(registry.names()) or "(none)"
    )
    for error in registry.errors:
        logger.error("plugin %s failed to load: %s", error.plugin, error.message.splitlines()[0])

    with contextlib.suppress(Exception):
        async with get_sessionmaker()() as session:
            await SourceRegistryService(session, registry).sync()
            await session.commit()

    try:
        yield
    finally:
        await get_queue().close()
        await dispose_engine()


def create_app(**overrides: Any) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
        **overrides,
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens only; no ambient authority to steal
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    install_error_handlers(app)

    app.include_router(health.router)
    prefix = settings.api_prefix
    for module in (auth, investigations, entities, analytics, sources, ai, demo, ws):
        app.include_router(module.router, prefix=prefix)

    return app


app = create_app()
