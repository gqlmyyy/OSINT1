"""Process-wide token buckets and semaphores keyed by provider name."""

from __future__ import annotations

from app.security.ratelimit import ConcurrencyGuard, TokenBucket

_buckets: dict[str, TokenBucket] = {}
_guard = ConcurrencyGuard()


def provider_limiters(name: str, rpm: int) -> tuple[TokenBucket, ConcurrencyGuard]:
    bucket = _buckets.get(name)
    if bucket is None or bucket.rate != max(rpm, 1) / 60.0:
        bucket = TokenBucket(rpm)
        _buckets[name] = bucket
    return bucket, _guard


def reset_limiters() -> None:
    _buckets.clear()
    _guard.limits.clear()
    _guard._semaphores.clear()  # noqa: SLF001
