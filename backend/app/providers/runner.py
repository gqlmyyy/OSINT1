"""Provider execution: cache -> rate limit -> concurrency -> timeout -> retry."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from app.core.config import Settings, get_settings
from app.providers.base import ProviderContext
from app.providers.cache import cache_key, get_cache
from app.providers.ratelimit import provider_limiters
from app.providers.registry import RegisteredProvider
from app.providers.types import Observation, ProviderResult, Target
from app.security.ssrf import SSRFBlocked, SafeAsyncClient

logger = logging.getLogger(__name__)


class ProviderTimeout(Exception):
    pass


class ProviderRunner:
    """Runs one provider against one target with every safety rail applied."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_factory: Any = None,
        use_cache: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self._http_factory = http_factory
        self.use_cache = use_cache

    def _client(self, timeout: float) -> SafeAsyncClient:
        if self._http_factory is not None:
            return self._http_factory(timeout)
        return SafeAsyncClient(self.settings, timeout=timeout)

    async def run(self, entry: RegisteredProvider, target: Target) -> ProviderResult:
        started = time.monotonic()
        caps = entry.capabilities
        limits = caps.rate_limit
        key = cache_key(entry.name, target.type, target.normalized)
        cache = get_cache()

        if self.use_cache:
            cached = await cache.get(key)
            if cached is not None:
                observations = [Observation.model_validate(item) for item in cached]
                return ProviderResult(
                    provider=entry.name,
                    observations=observations,
                    cache_hit=True,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

        bucket, guard = provider_limiters(entry.name, limits.rpm)
        semaphore = guard.semaphore(entry.name, limits.concurrency)

        last_error: str | None = None
        for attempt in range(limits.max_retries + 1):
            try:
                await bucket.acquire(entry.name)
                async with semaphore:
                    client = self._client(limits.timeout_seconds)
                    ctx = ProviderContext(client, settings=self.settings, config=entry.config)
                    try:
                        observations = await asyncio.wait_for(
                            entry.provider.search(target, ctx),
                            timeout=limits.timeout_seconds,
                        )
                    finally:
                        await client.aclose()

                for obs in observations:
                    obs.provider = obs.provider or entry.name
                    obs.source = obs.source or entry.name
                    obs.confidence = obs.scored(caps.reliability)

                if self.use_cache:
                    ttl = caps.cache_ttl_seconds or self.settings.provider_cache_ttl_seconds
                    await cache.set(key, [o.model_dump(mode="json") for o in observations], ttl)

                return ProviderResult(
                    provider=entry.name,
                    observations=observations,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            except TimeoutError:
                last_error = f"timeout after {limits.timeout_seconds}s"
            except SSRFBlocked as exc:
                # Never retried: the target is refused by policy, not transiently failing.
                return ProviderResult(
                    provider=entry.name,
                    error=f"blocked by egress policy: {exc}",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("provider %s failed on %s: %s", entry.name, target.value, exc)

            if attempt < limits.max_retries:
                delay = limits.backoff_base_seconds * (2**attempt)
                await asyncio.sleep(delay + random.uniform(0, delay / 2))  # noqa: S311

        return ProviderResult(
            provider=entry.name,
            error=last_error or "unknown error",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
