"""Token-bucket limiters: an in-process API throttle and a shared provider limiter."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucket:
    """Per-key token bucket. Async-safe within one process."""

    def __init__(self, rate_per_minute: int, burst: int | None = None) -> None:
        self.rate = max(rate_per_minute, 1) / 60.0
        self.capacity = float(burst if burst is not None else max(rate_per_minute, 1))
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    def _refill(self, key: str, now: float) -> _Bucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self.capacity, updated_at=now)
            self._buckets[key] = bucket
            return bucket
        elapsed = now - bucket.updated_at
        bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.rate)
        bucket.updated_at = now
        return bucket

    async def try_acquire(self, key: str, cost: float = 1.0) -> bool:
        async with self._lock:
            bucket = self._refill(key, time.monotonic())
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True
            return False

    async def retry_after(self, key: str, cost: float = 1.0) -> float:
        async with self._lock:
            bucket = self._refill(key, time.monotonic())
            deficit = max(0.0, cost - bucket.tokens)
            return deficit / self.rate if self.rate else 60.0

    async def acquire(self, key: str, cost: float = 1.0) -> None:
        """Block until a token is available."""
        while not await self.try_acquire(key, cost):
            await asyncio.sleep(min(await self.retry_after(key, cost), 5.0) or 0.05)


@dataclass
class ConcurrencyGuard:
    """Per-key semaphore set, so one provider cannot starve the worker."""

    limits: dict[str, int] = field(default_factory=dict)
    _semaphores: dict[str, asyncio.Semaphore] = field(default_factory=dict)

    def semaphore(self, key: str, limit: int) -> asyncio.Semaphore:
        sem = self._semaphores.get(key)
        if sem is None or self.limits.get(key) != limit:
            sem = asyncio.Semaphore(max(1, limit))
            self._semaphores[key] = sem
            self.limits[key] = limit
        return sem
