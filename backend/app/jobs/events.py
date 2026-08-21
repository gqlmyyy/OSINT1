"""Event bus feeding the WebSocket layer. In-memory by default, Redis pub/sub when configured."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)
CHANNEL_PREFIX = "gi:events:"


def channel_for(investigation_id: uuid.UUID | str) -> str:
    return f"{CHANNEL_PREFIX}{investigation_id}"


class MemoryEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(channel, ())):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.setdefault(channel, set()).add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.get(channel, set()).discard(queue)

    def subscriber_count(self, channel: str) -> int:
        return len(self._subscribers.get(channel, ()))


class RedisEventBus:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(url, decode_responses=True)

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        await self._redis.publish(channel, json.dumps(event, default=str))

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    yield json.loads(message["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()


EventBus = MemoryEventBus | RedisEventBus
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        settings = get_settings()
        _bus = RedisEventBus(settings.redis_url) if settings.redis_url else MemoryEventBus()
    return _bus


def set_event_bus(bus: EventBus | None) -> None:
    global _bus
    _bus = bus


class InvestigationEmitter:
    """Convenience wrapper so orchestration code reads as events, not plumbing."""

    def __init__(self, investigation_id: uuid.UUID, bus: EventBus | None = None) -> None:
        self.investigation_id = investigation_id
        self.channel = channel_for(investigation_id)
        self.bus = bus or get_event_bus()

    async def emit(self, event: str, **payload: Any) -> None:
        try:
            await self.bus.publish(
                self.channel,
                {"event": event, "investigation_id": str(self.investigation_id), **payload},
            )
        except Exception:  # a dead subscriber must never fail a scan
            logger.warning("failed to publish event %s", event, exc_info=True)
