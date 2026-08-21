"""ARQ worker entrypoint: ``arq app.jobs.worker.WorkerSettings``."""

from __future__ import annotations

import logging
import uuid
from typing import Any, ClassVar

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
    logger.info(
        "worker started with %d provider(s): %s", len(registry), ", ".join(registry.names())
    )


async def shutdown(_ctx: dict[str, Any]) -> None:
    await dispose_engine()


def redis_settings_from_env() -> RedisSettings:
    """Build arq's Redis configuration from ``REDIS_URL``.

    Failing loudly is deliberate: a worker with no broker cannot do anything, and the
    alternative — arq's default ``RedisSettings()`` — silently points at localhost.
    """
    url = get_settings().redis_url
    if not url:
        raise RuntimeError(
            "REDIS_URL must be set to run the ARQ worker. The API can fall back to the "
            "in-process queue; the worker has no such fallback."
        )
    return RedisSettings.from_dsn(url)


class WorkerSettings:
    """Settings arq reads to construct its Worker.

    Every value here must be a plain entry in this class's own ``__dict__``:
    ``arq.worker.get_kwargs()`` does ``settings_cls.__dict__`` and keeps only the keys
    that match ``Worker``'s signature. Anything resolved dynamically — a metaclass
    property, ``__getattr__``, a descriptor on a base class — answers correctly under
    normal attribute access but is *invisible* to that lookup, so arq drops it and
    constructs ``Worker()`` with the default ``RedisSettings()``: localhost:6379.
    That failure is silent, which is what made it hard to see.
    """

    redis_settings: ClassVar[RedisSettings] = redis_settings_from_env()
    functions: ClassVar[list[Any]] = [run_scan]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    job_timeout = 60 * 30
    keep_result = 3600
    # arq records liveness into Redis on this interval and `arq ... --check` reads it
    # back. The default is an hour, which is far too coarse for a container probe: a
    # wedged worker would keep reporting healthy long after it stopped working.
    health_check_interval = 30
