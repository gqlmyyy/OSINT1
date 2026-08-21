"""ARQ worker entrypoint: ``arq app.jobs.worker.WorkerSettings``."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.logging import configure_logging
from app.jobs.orchestrator import Orchestrator, ScanRequest
from app.providers.registry import get_registry

logger = logging.getLogger(__name__)


async def run_scan(
    _ctx: dict[str, Any],
    investigation_id: str,
    providers: list[str] | None = None,
    max_depth: int | None = None,
    recursive: bool = True,
) -> dict[str, Any]:
    orchestrator = Orchestrator(get_sessionmaker())
    report = await orchestrator.run(
        ScanRequest(
            investigation_id=uuid.UUID(investigation_id),
            providers=providers,
            max_depth=max_depth,
            recursive=recursive,
        )
    )
    return {
        "investigation_id": str(report.investigation_id),
        "jobs_run": report.jobs_run,
        "observations": report.observations,
        "entities_created": report.entities_created,
        "relationships_created": report.relationships_created,
        "matches": report.matches,
        "errors": report.errors,
    }


async def startup(_ctx: dict[str, Any]) -> None:
    configure_logging()
    registry = get_registry()
    logger.info("worker started with %d provider(s): %s", len(registry), ", ".join(registry.names()))


async def shutdown(_ctx: dict[str, Any]) -> None:
    await dispose_engine()


class _WorkerMeta(type):
    """``redis_settings`` is read off the class by arq, so resolve it lazily here:
    importing this module must not require Redis to be configured."""

    @property
    def redis_settings(cls) -> RedisSettings:
        url = get_settings().redis_url
        if not url:
            raise RuntimeError("REDIS_URL must be set to run the ARQ worker")
        return RedisSettings.from_dsn(url)


class WorkerSettings(metaclass=_WorkerMeta):
    functions = [run_scan]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    job_timeout = 60 * 30
    keep_result = 3600
