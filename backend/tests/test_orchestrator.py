"""Orchestration: recursion, budgets, loop guards, failure isolation, live events."""

from __future__ import annotations

from app.core.enums import InvestigationStatus, JobStatus, ProviderType, RunStatus, TargetType
from app.jobs.budget import Budget
from app.jobs.events import MemoryEventBus, set_event_bus
from app.jobs.orchestrator import Orchestrator, ScanRequest
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.registry import ProviderRegistry
from app.providers.runner import ProviderRunner
from app.providers.types import (
    EdgeHint,
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)
from app.schemas.investigation import TargetIn
from app.services.investigation import InvestigationService

FAST = ProviderRateLimit(rpm=6000, concurrency=8, timeout_seconds=5, max_retries=0)


class ChainProvider(OSINTProvider):
    """username -> account (+website) -> domain -> ip: a full recursive chain."""

    name = "chain"
    provider_type = ProviderType.USERNAME

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, int]] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME, TargetType.DOMAIN],
            emits=["social_account", "website", "ip"],
            rate_limit=FAST,
            reliability=0.9,
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name)

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        self.seen.append((target.type, target.normalized, target.depth))
        if target.type is TargetType.USERNAME:
            return [
                self.observation(
                    kind="social_account", value=target.normalized,
                    url=f"https://github.com/{target.normalized}",
                    data={"platform": "GitHub", "username": target.normalized,
                          "website": "example.com"},
                    edges=[EdgeHint(type="LINKS_TO", target_kind="website",
                                    target_value="example.com",
                                    why="Profile declares this website.")],
                    derived_targets=[("domain", "example.com")],
                )
            ]
        return [
            self.observation(
                kind="ip", value="93.184.216.34",
                data={"record": "A"},
                derived_targets=[("domain", "example.com")],  # deliberate loop attempt
            )
        ]


class FailingProvider(OSINTProvider):
    name = "failing"
    provider_type = ProviderType.SOCIAL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME], emits=["social_account"], rate_limit=FAST
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name)

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        raise RuntimeError("provider is down")


class UnavailableProvider(OSINTProvider):
    name = "unavailable"
    provider_type = ProviderType.USERNAME

    def __init__(self) -> None:
        self.searched = False

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME], emits=["social_account"], rate_limit=FAST
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.unavailable(self.name, "binary not installed")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        self.searched = True
        return []


async def _prepare(session, investigation, values: list[str]) -> None:
    await InvestigationService(session).add_targets(
        investigation.id, [TargetIn(value=v) for v in values]
    )
    await session.commit()


def _orchestrator(session, *providers: OSINTProvider) -> Orchestrator:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    maker = async_sessionmaker(session.bind, expire_on_commit=False)
    return Orchestrator(maker, registry=registry, runner=ProviderRunner(use_cache=False))


async def test_recursive_discovery_walks_the_chain(session, investigation) -> None:
    provider = ChainProvider()
    await _prepare(session, investigation, ["example_user"])
    report = await _orchestrator(session, provider).run(
        ScanRequest(investigation_id=investigation.id, recursive=True)
    )
    assert report.jobs_run >= 2
    depths = {depth for _, _, depth in provider.seen}
    assert depths >= {0, 1}, "discovery must follow derived identifiers to the next depth"

    from sqlalchemy import select

    from app.models import Entity

    labels = {
        e.type
        for e in (
            await session.execute(select(Entity).where(Entity.investigation_id == investigation.id))
        ).scalars()
    }
    assert {"username", "social_account", "website", "ip"} <= labels


async def test_recursion_loop_guard_stops_cycles(session, investigation) -> None:
    """The chain provider re-derives a domain it already visited; it must run once."""
    provider = ChainProvider()
    await _prepare(session, investigation, ["example_user"])
    await _orchestrator(session, provider).run(
        ScanRequest(investigation_id=investigation.id, recursive=True)
    )
    domain_visits = [s for s in provider.seen if s[0] == TargetType.DOMAIN]
    assert len(domain_visits) == 1, f"the loop guard failed: {provider.seen}"


async def test_non_recursive_scan_stays_at_depth_zero(session, investigation) -> None:
    provider = ChainProvider()
    await _prepare(session, investigation, ["example_user"])
    await _orchestrator(session, provider).run(
        ScanRequest(investigation_id=investigation.id, recursive=False)
    )
    assert {depth for _, _, depth in provider.seen} == {0}


async def test_provider_failure_is_isolated_and_recorded(session, investigation) -> None:
    good, bad = ChainProvider(), FailingProvider()
    await _prepare(session, investigation, ["example_user"])
    report = await _orchestrator(session, good, bad).run(
        ScanRequest(investigation_id=investigation.id, recursive=False)
    )
    assert report.errors, "the failure must be surfaced, not swallowed"
    assert report.entities_created > 0, "one broken provider must not lose the other's results"

    from sqlalchemy import select

    from app.models import Job, ProviderRun

    jobs = {
        j.provider: j.status
        for j in (
            await session.execute(select(Job).where(Job.investigation_id == investigation.id))
        ).scalars()
    }
    assert jobs["failing"] == JobStatus.FAILED
    assert jobs["chain"] == JobStatus.SUCCEEDED
    runs = {
        r.provider: r.status
        for r in (
            await session.execute(
                select(ProviderRun).where(ProviderRun.investigation_id == investigation.id)
            )
        ).scalars()
    }
    assert runs["failing"] == RunStatus.ERROR


async def test_unhealthy_provider_is_never_scheduled(session, investigation) -> None:
    provider = UnavailableProvider()
    await _prepare(session, investigation, ["example_user"])
    report = await _orchestrator(session, provider).run(
        ScanRequest(investigation_id=investigation.id)
    )
    assert not provider.searched
    assert report.jobs_run == 0


async def test_scan_emits_live_events(session, investigation) -> None:
    bus = MemoryEventBus()
    set_event_bus(bus)
    received: list[dict] = []

    import asyncio

    from app.jobs.events import channel_for

    async def listen() -> None:
        async for event in bus.subscribe(channel_for(investigation.id)):
            received.append(event)

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)

    await _prepare(session, investigation, ["example_user"])
    await _orchestrator(session, ChainProvider()).run(
        ScanRequest(investigation_id=investigation.id, recursive=False)
    )
    await asyncio.sleep(0.05)
    task.cancel()

    kinds = [e["event"] for e in received]
    assert "job_started" in kinds
    assert "entity_discovered" in kinds
    assert "relationship_discovered" in kinds
    assert "stage_changed" in kinds
    assert "investigation_completed" in kinds
    node = next(e for e in received if e["event"] == "entity_discovered")["node"]
    assert {"id", "type", "label", "confidence"} <= set(node)


async def test_investigation_is_marked_completed(session, investigation) -> None:
    await _prepare(session, investigation, ["example_user"])
    await _orchestrator(session, ChainProvider()).run(
        ScanRequest(investigation_id=investigation.id)
    )
    await session.refresh(investigation)
    assert investigation.status == InvestigationStatus.COMPLETED


async def test_scan_without_targets_is_a_no_op(session, investigation) -> None:
    report = await _orchestrator(session, ChainProvider()).run(
        ScanRequest(investigation_id=investigation.id)
    )
    assert report.jobs_run == 0
    await session.refresh(investigation)
    assert investigation.status == InvestigationStatus.DRAFT


# -- budget ---------------------------------------------------------------------


def test_budget_enforces_job_ceiling() -> None:
    budget = Budget(max_depth=3, max_entities=500, max_jobs=3)
    assert sum(budget.can_schedule("p", "username", f"u{i}", 0) for i in range(10)) == 3
    assert any("job limit" in reason for reason in budget.exhausted_reasons)


def test_budget_enforces_depth_ceiling() -> None:
    budget = Budget(max_depth=2, max_entities=500, max_jobs=100)
    assert budget.can_schedule("p", "username", "a", 2)
    assert not budget.can_schedule("p", "username", "b", 3)
    assert any("depth limit" in reason for reason in budget.exhausted_reasons)


def test_budget_deduplicates_identical_work() -> None:
    budget = Budget(max_depth=3, max_entities=500, max_jobs=100)
    assert budget.can_schedule("p", "username", "same", 0)
    assert not budget.can_schedule("p", "username", "same", 0)
    assert budget.jobs_used == 1


def test_budget_stops_at_the_entity_ceiling() -> None:
    budget = Budget(max_depth=3, max_entities=10, max_jobs=100)
    budget.record_entities(10)
    assert budget.entities_exhausted
    assert not budget.can_schedule("p", "username", "x", 0)


def test_budget_is_capped_by_settings() -> None:
    budget = Budget.from_settings(max_depth=99, max_entities=10**9, max_jobs=10**9)
    assert budget.max_depth <= 3
    assert budget.max_entities <= 500
    assert budget.max_jobs <= 100
