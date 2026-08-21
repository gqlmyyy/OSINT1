"""Provider response cache (spec 21): key = provider + target kind + value."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Protocol

from app.core.config import get_settings


def cache_key(provider: str, target_type: str, value: str) -> str:
    digest = hashlib.sha256(f"{provider}|{target_type}|{value}".encode()).hexdigest()[:32]
    return f"gi:cache:{provider}:{digest}"


class CacheBackend(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def clear(self) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    async def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int) -> None:
        self._data[key] = (time.monotonic() + ttl, value)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def clear(self) -> None:
        self._data.clear()


class RedisCache:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> Any | None:
        raw = await self._redis.get(key)
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: Any, ttl: int) -> None:
        await self._redis.set(key, json.dumps(value, default=str), ex=ttl)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def clear(self) -> None:
        async for key in self._redis.scan_iter("gi:cache:*"):
            await self._redis.delete(key)


_cache: CacheBackend | None = None


def get_cache() -> CacheBackend:
    global _cache
    if _cache is None:
        settings = get_settings()
        _cache = RedisCache(settings.redis_url) if settings.redis_url else MemoryCache()
    return _cache


def set_cache(backend: CacheBackend | None) -> None:
    """Test / bootstrap hook."""
    global _cache
    _cache = backend
