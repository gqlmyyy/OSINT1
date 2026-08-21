"""Provider runtime: caching, rate limits, timeouts, failures, and malformed responses."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core.enums import HealthState, ProviderType, TargetType
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.cache import MemoryCache, set_cache
from app.providers.registry import ProviderRegistry
from app.providers.runner import ProviderRunner
from app.providers.types import (
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)
from app.security.ssrf import SSRFBlocked, SafeAsyncClient

TARGET = Target(type=TargetType.USERNAME, value="ex", normalized="ex", depth=0)


class StubProvider(OSINTProvider):
    name = "stub"
    provider_type = ProviderType.USERNAME

    def __init__(self, behaviour: str = "ok") -> None:
        self.behaviour = behaviour
        self.calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME],
            emits=["social_account"],
            rate_limit=ProviderRateLimit(
                rpm=600, concurrency=2, timeout_seconds=0.3, max_retries=1,
                backoff_base_seconds=0.01,
            ),
            reliability=0.9,
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name)

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        self.calls += 1
        if self.behaviour == "boom":
            raise RuntimeError("upstream exploded")
        if self.behaviour == "hang":
            await asyncio.sleep(5)
        if self.behaviour == "ssrf":
            raise SSRFBlocked("address not allowed: 127.0.0.1")
        if self.behaviour == "malformed":
            # A provider that returns rubbish must not corrupt the pipeline.
            return [Observation(kind="social_account", value="", data={"platform": "X"})]
        return [
            self.observation(
                kind="social_account", value="ex",
                data={"platform": "Stub", "username": "ex"},
            )
        ]


def registry_with(provider: OSINTProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(provider)
    return registry


async def test_successful_run_scores_confidence_from_reliability() -> None:
    provider = StubProvider()
    entry = registry_with(provider).get("stub")
    result = await ProviderRunner(use_cache=False).run(entry, TARGET)
    assert result.error is None
    assert len(result.observations) == 1
    # reliability 0.9 x pattern_match weight 0.6
    assert result.observations[0].confidence == pytest.approx(0.54)


async def test_second_call_is_served_from_cache() -> None:
    set_cache(MemoryCache())
    provider = StubProvider()
    entry = registry_with(provider).get("stub")
    runner = ProviderRunner()
    first = await runner.run(entry, TARGET)
    second = await runner.run(entry, TARGET)
    assert not first.cache_hit and second.cache_hit
    assert provider.calls == 1, "a cache hit must not re-hit the provider"
    assert [o.value for o in second.observations] == [o.value for o in first.observations]


async def test_provider_exception_is_retried_then_reported() -> None:
    provider = StubProvider("boom")
    entry = registry_with(provider).get("stub")
    result = await ProviderRunner(use_cache=False).run(entry, TARGET)
    assert result.error is not None and "upstream exploded" in result.error
    assert provider.calls == 2, "one retry, then give up"
    assert result.observations == []


async def test_timeout_is_bounded_and_reported() -> None:
    provider = StubProvider("hang")
    entry = registry_with(provider).get("stub")
    result = await ProviderRunner(use_cache=False).run(entry, TARGET)
    assert result.error is not None and "timeout" in result.error.lower()


async def test_egress_block_is_not_retried() -> None:
    provider = StubProvider("ssrf")
    entry = registry_with(provider).get("stub")
    result = await ProviderRunner(use_cache=False).run(entry, TARGET)
    assert result.error is not None and "egress policy" in result.error
    assert provider.calls == 1, "a policy refusal is permanent; retrying is pointless"


async def test_token_bucket_throttles_once_drained() -> None:
    """Pacing is exact rather than timing-dependent: drain the burst, then measure."""
    from app.security.ratelimit import TokenBucket

    bucket = TokenBucket(rate_per_minute=60, burst=2)
    assert await bucket.try_acquire("k") and await bucket.try_acquire("k")
    assert not await bucket.try_acquire("k"), "the burst is spent"
    wait = await bucket.retry_after("k")
    assert 0.5 <= wait <= 1.5, f"a 60/min bucket should refill in ~1s, got {wait}"


async def test_runner_uses_the_provider_configured_bucket() -> None:
    from app.providers.ratelimit import provider_limiters

    provider = StubProvider()
    entry = registry_with(provider).get("stub")
    bucket, guard = provider_limiters(entry.name, entry.capabilities.rate_limit.rpm)
    before = await bucket.retry_after(entry.name)
    await ProviderRunner(use_cache=False).run(entry, TARGET)
    assert bucket.rate == entry.capabilities.rate_limit.rpm / 60.0
    assert guard.limits[entry.name] == entry.capabilities.rate_limit.concurrency
    assert before == 0.0


async def test_concurrency_cap_is_enforced() -> None:
    """Two workers at concurrency=1 must not overlap inside the provider."""

    class Counting(StubProvider):
        overlap = 0
        active = 0

        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(
                accepts=[TargetType.USERNAME], emits=["social_account"],
                rate_limit=ProviderRateLimit(rpm=600, concurrency=1, timeout_seconds=5),
            )

        async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
            type(self).active += 1
            type(self).overlap = max(type(self).overlap, type(self).active)
            await asyncio.sleep(0.05)
            type(self).active -= 1
            return []

    provider = Counting()
    entry = registry_with(provider).get("stub")
    runner = ProviderRunner(use_cache=False)
    await asyncio.gather(*(runner.run(entry, TARGET) for _ in range(4)))
    assert Counting.overlap == 1


def test_duplicate_provider_names_are_rejected() -> None:
    registry = registry_with(StubProvider())
    with pytest.raises(ValueError, match="duplicate provider name"):
        registry.register(StubProvider())


def test_unsafe_target_values_are_refused_before_use() -> None:
    provider = StubProvider()
    for bad in ["ex; rm -rf /", "ex$(whoami)", "ex`id`", "ex|nc evil 1", "ex\nnewline", "ex&&id"]:
        with pytest.raises(ValueError, match="unsafe target value"):
            provider.safe_value(bad)
    assert provider.safe_value("example_user") == "example_user"


async def test_broken_plugin_does_not_break_discovery(tmp_path) -> None:
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "provider.py").write_text(
        "from app.providers.base import OSINTProvider\n"
        "from app.providers.types import ProviderCapabilities, ProviderHealth\n"
        "from app.core.enums import TargetType\n"
        "class P(OSINTProvider):\n"
        "    name='good'\n"
        "    def capabilities(self):\n"
        "        return ProviderCapabilities(accepts=[TargetType.USERNAME], emits=['username'])\n"
        "    async def health_check(self):\n"
        "        return ProviderHealth.ok('good')\n"
        "    async def search(self, target, ctx):\n"
        "        return []\n"
        "PROVIDERS=[P]\n"
    )
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "provider.py").write_text("raise ImportError('missing dependency')\n")

    registry = ProviderRegistry().discover(tmp_path)
    assert registry.names() == ["good"]
    assert [e.plugin for e in registry.errors] == ["broken"]
    health = {h.name: h for h in await registry.health()}
    assert health["broken"].state is HealthState.UNAVAILABLE


async def test_bundled_plugins_all_load_and_declare_capabilities() -> None:
    from app.core.config import get_settings

    registry = ProviderRegistry().discover(get_settings().plugins_path)
    assert not registry.errors, f"plugin import failures: {registry.errors}"
    assert len(registry) >= 10
    for entry in registry.all():
        caps = entry.capabilities
        assert caps.accepts, f"{entry.name} accepts nothing"
        assert caps.emits, f"{entry.name} emits nothing"
        assert 0 < caps.reliability <= 1
        assert caps.rate_limit.rpm > 0 and caps.rate_limit.concurrency > 0


async def test_username_enum_treats_ambiguous_status_as_unverified() -> None:
    """A bare 200 is a weak signal; the provider must not present it as an observed fact."""
    from app.core.config import get_settings
    from app.core.enums import Assertion

    registry = ProviderRegistry().discover(get_settings().plugins_path)
    provider = registry.get("username_enum").provider

    def handler(request: httpx.Request) -> httpx.Response:
        if "reddit.com" in str(request.url):
            return httpx.Response(200, json={"name": "ex"})
        return httpx.Response(200, text="<html>ok</html>")

    client = SafeAsyncClient(get_settings(), transport=httpx.MockTransport(handler))
    ctx = ProviderContext(client, config={"sites": ["GitHub", "Reddit"]})
    observations = await provider.search(TARGET, ctx)
    await client.aclose()

    by_platform = {o.data["platform"]: o for o in observations}
    assert by_platform["GitHub"].assertion is Assertion.UNVERIFIED
    assert by_platform["Reddit"].assertion is Assertion.OBSERVED
