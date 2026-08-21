"""Task queue with two interchangeable backends.

``arq`` puts scans on Redis so a dedicated worker process runs them (production).
``inline`` runs them as an asyncio task inside the API process — used by the test suite
and by single-container demo deployments where Redis is not present.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.jobs.orchestrator import Orchestrator, ScanRequest

logger = logging.getLogger(__name__)


class TaskQueue(Protocol):
    async def enqueue_scan(self, request: ScanRequest) -> str: ...
    async def cancel(self, investigation_id: uuid.UUID) -> int: ...
    async def close(self) -> None: ...


class InlineQueue:
    """Runs scans in-process. Tasks are tracked so shutdown can await them."""

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, asyncio.Task[Any]] = {}

    async def enqueue_scan(self, request: ScanRequest) -> str:
        orchestrator = Orchestrator(get_sessionmaker())
        task = asyncio.create_task(self._guard(orchestrator, request))
        self._tasks[request.investigation_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(request.investigation_id, None))
        return f"inline:{request.investigation_id}"

    async def _guard(self, orchestrator: Orchestrator, request: ScanRequest) -> None:
        try:
            await orchestrator.run(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scan failed for investigation %s", request.investigation_id)

    async def cancel(self, investigation_id: uuid.UUID) -> int:
        task = self._tasks.get(investigation_id)
        if task and not task.done():
            task.cancel()
            return 1
        return 0

    async def wait(self, investigation_id: uuid.UUID) -> None:
        """Test helper: await an in-flight scan."""
        task = self._tasks.get(investigation_id)
        if task:
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()


class ArqQueue:
    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            from arq import create_pool
            from arq.connections import RedisSettings

            self._pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        return self._pool

    async def enqueue_scan(self, request: ScanRequest) -> str:
        pool = await self._get_pool()
        job = await pool.enqueue_job(
            "run_scan",
            str(request.investigation_id),
            request.providers,
            request.max_depth,
            request.recursive,
        )
        return str(job.job_id) if job else ""

    async def cancel(self, investigation_id: uuid.UUID) -> int:
        # ARQ has no selective abort; the orchestrator checks investigation status and
        # the API marks it cancelled, which stops further waves.
        return 0

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


_queue: TaskQueue | None = None


def get_queue() -> TaskQueue:
    global _queue
    if _queue is None:
        settings = get_settings()
        _queue = ArqQueue(settings.redis_url) if settings.redis_url else InlineQueue()
    return _queue


def set_queue(queue: TaskQueue | None) -> None:
    global _queue
    _queue = queue
