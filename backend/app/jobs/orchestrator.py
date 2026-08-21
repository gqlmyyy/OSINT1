"""Investigation orchestration: plan -> run providers -> extract -> correlate -> emit.

Recursive discovery (spec 19) happens here: derived identifiers become new targets at
``depth + 1``, bounded by the budget and a loop guard.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.enums import (
    InvestigationStage,
    InvestigationStatus,
    JobStatus,
    RunStatus,
    TargetType,
)
from app.correlation.engine import CorrelationEngine
from app.evidence.extractor import EntityExtractor
from app.graph.projection import GraphProjection, _edge, _node
from app.jobs.budget import Budget
from app.jobs.events import InvestigationEmitter
from app.models import Entity, Investigation, Job, ProviderRun, Relationship, Target
from app.models.base import utcnow
from app.providers.registry import ProviderRegistry, RegisteredProvider, get_registry
from app.providers.runner import ProviderRunner
from app.providers.types import Target as ProviderTarget
from app.social.interactions import InteractionLedger

logger = logging.getLogger(__name__)


@dataclass
class ScanRequest:
    investigation_id: uuid.UUID
    providers: list[str] | None = None
    max_depth: int | None = None
    recursive: bool = True


@dataclass
class ScanReport:
    investigation_id: uuid.UUID
    jobs_run: int = 0
    observations: int = 0
    entities_created: int = 0
    relationships_created: int = 0
    matches: int = 0
    interactions: int = 0
    errors: list[str] = field(default_factory=list)
    budget: dict[str, object] = field(default_factory=dict)


class Orchestrator:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        registry: ProviderRegistry | None = None,
        runner: ProviderRunner | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.registry = registry or get_registry()
        self.runner = runner or ProviderRunner()

    async def run(self, request: ScanRequest) -> ScanReport:
        emitter = InvestigationEmitter(request.investigation_id)
        report = ScanReport(investigation_id=request.investigation_id)
        budget = Budget.from_settings(max_depth=request.max_depth)

        async with self.sessionmaker() as session:
            investigation = await session.get(Investigation, request.investigation_id)
            if investigation is None:
                raise LookupError("investigation not found")
            investigation.status = InvestigationStatus.RUNNING
            investigation.stage = InvestigationStage.DISCOVERY
            await session.commit()

            targets = list(
                (
                    await session.execute(
                        select(Target).where(Target.investigation_id == request.investigation_id)
                    )
                ).scalars()
            )
            budget.record_entities(
                await _count_entities(session, request.investigation_id)
            )

        await emitter.emit("stage_changed", stage=InvestigationStage.DISCOVERY)

        queue: list[ProviderTarget] = [
            ProviderTarget(
                type=TargetType(t.type), value=t.value, normalized=t.normalized, depth=0
            )
            for t in targets
        ]
        if not queue:
            await self._finish(request.investigation_id, report, budget, emitter, empty=True)
            return report

        selected = await self._select_providers(request.providers, emitter)
        if not selected:
            report.errors.append("no enabled provider matches the requested selection")
            await self._finish(request.investigation_id, report, budget, emitter, empty=True)
            return report

        seen_targets: set[tuple[str, str]] = set()
        while queue:
            wave, queue = queue, []
            planned: list[tuple[RegisteredProvider, ProviderTarget]] = []
            for target in wave:
                identity = (target.type, target.normalized)
                if identity in seen_targets and target.depth > 0:
                    continue
                seen_targets.add(identity)
                for entry in selected:
                    if target.type not in entry.capabilities.accepts:
                        continue
                    if target.depth > 0 and not entry.capabilities.recursive:
                        continue
                    if budget.can_schedule(
                        entry.name, target.type, target.normalized, target.depth
                    ):
                        planned.append((entry, target))

            if not planned:
                break

            results = await asyncio.gather(
                *(self._run_one(entry, target, request, emitter) for entry, target in planned),
                return_exceptions=True,
            )

            derived: list[ProviderTarget] = []
            for outcome in results:
                if isinstance(outcome, BaseException):
                    report.errors.append(str(outcome))
                    logger.exception("job failed", exc_info=outcome)
                    continue
                report.jobs_run += 1
                report.observations += outcome.observations
                report.entities_created += outcome.entities_created
                report.relationships_created += outcome.relationships_created
                if outcome.error:
                    report.errors.append(f"{outcome.provider}: {outcome.error}")
                derived.extend(outcome.derived)

            budget.record_entities(report.entities_created)
            if request.recursive and not budget.entities_exhausted:
                queue = [t for t in derived if t.depth <= budget.max_depth]
                if queue:
                    await emitter.emit(
                        "stage_changed",
                        stage=InvestigationStage.GRAPH_EXPANSION,
                        depth=queue[0].depth,
                    )

        # -- correlation ------------------------------------------------------
        await emitter.emit("stage_changed", stage=InvestigationStage.CORRELATION)
        async with self.sessionmaker() as session:
            investigation = await session.get(Investigation, request.investigation_id)
            if investigation is not None:
                investigation.stage = InvestigationStage.CORRELATION
            engine = CorrelationEngine()
            assessments = await engine.correlate_investigation(session, request.investigation_id)

            # Interaction accounting runs alongside correlation but never feeds it: how
            # often two accounts publicly interact says nothing about whether they are
            # the same person. See docs/09.
            interactions = await InteractionLedger(
                session, request.investigation_id
            ).materialize()
            await session.commit()
            report.matches = len(assessments)
            report.interactions = len(interactions)
            for assessment in assessments:
                await emitter.emit(
                    "match_found",
                    score=assessment.score,
                    band=str(assessment.band),
                    a={"id": str(assessment.entity_a.id), "label": assessment.entity_a.label},
                    b={"id": str(assessment.entity_b.id), "label": assessment.entity_b.label},
                    reasons=assessment.reasons,
                )

        await self._finish(request.investigation_id, report, budget, emitter)
        return report

    # -- one provider x one target --------------------------------------------

    async def _run_one(
        self,
        entry: RegisteredProvider,
        target: ProviderTarget,
        request: ScanRequest,
        emitter: InvestigationEmitter,
    ) -> _JobOutcome:
        outcome = _JobOutcome(provider=entry.name)
        started = datetime.now(tz=UTC)

        async with self.sessionmaker() as session:
            job = Job(
                id=uuid.uuid4(),
                investigation_id=request.investigation_id,
                provider=entry.name,
                target_type=str(target.type),
                target_value=target.value[:512],
                depth=target.depth,
                status=JobStatus.RUNNING,
                attempts=1,
                started_at=started,
            )
            session.add(job)
            await session.commit()
            job_id = job.id

        await emitter.emit(
            "job_started", provider=entry.name, job_id=str(job_id), target=target.value
        )

        result = await self.runner.run(entry, target)

        async with self.sessionmaker() as session:
            job_row = await session.get(Job, job_id)
            if job_row is None:  # the investigation was deleted mid-scan
                return outcome
            run_status = (
                RunStatus.ERROR
                if result.error
                else (RunStatus.OK if result.observations else RunStatus.EMPTY)
            )
            if result.error and "timeout" in result.error.lower():
                run_status = RunStatus.TIMEOUT

            if result.error:
                job_row.status = JobStatus.FAILED
                job_row.error = result.error[:2000]
                outcome.error = result.error
            else:
                extractor = EntityExtractor(session, request.investigation_id)
                extraction = await extractor.ingest(target, result.observations, job_id=job_id)
                outcome.observations = len(result.observations)
                outcome.entities_created = len(extraction.new_entities)
                outcome.relationships_created = len(extraction.new_relationships)
                outcome.derived = [
                    ProviderTarget(
                        type=t.type, value=t.value, normalized=t.normalized, depth=t.depth
                    )
                    for t in extraction.derived_targets
                ]
                job_row.status = JobStatus.SUCCEEDED
                job_row.stats = {
                    "observations": outcome.observations,
                    "entities": outcome.entities_created,
                    "relationships": outcome.relationships_created,
                    "skipped": extraction.skipped[:20],
                    "cache_hit": result.cache_hit,
                }
                await self._emit_graph_delta(emitter, extraction)

            job_row.finished_at = utcnow()
            session.add(
                ProviderRun(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    investigation_id=request.investigation_id,
                    provider=entry.name,
                    status=str(run_status),
                    started_at=started,
                    finished_at=utcnow(),
                    duration_ms=result.duration_ms,
                    observation_count=len(result.observations),
                    cache_hit=result.cache_hit,
                    error=result.error[:2000] if result.error else None,
                )
            )
            await session.commit()

        await emitter.emit(
            "job_finished",
            provider=entry.name,
            job_id=str(job_id),
            status=str(run_status),
            observations=outcome.observations,
            error=outcome.error,
        )
        if outcome.error:
            await emitter.emit("error", provider=entry.name, message=outcome.error)
        return outcome

    async def _emit_graph_delta(self, emitter: InvestigationEmitter, extraction: object) -> None:
        from collections import Counter

        degree: Counter[uuid.UUID] = Counter()
        for entity in getattr(extraction, "new_entities", []):
            await emitter.emit(
                "entity_discovered",
                entity_id=str(entity.id),
                type=entity.type,
                node=_node(entity, degree, clustered=False),
            )
        for rel in getattr(extraction, "new_relationships", []):
            if isinstance(rel, Relationship):
                await emitter.emit(
                    "relationship_discovered", relationship_id=str(rel.id), edge=_edge(rel)
                )

    # -- lifecycle -------------------------------------------------------------

    async def _finish(
        self,
        investigation_id: uuid.UUID,
        report: ScanReport,
        budget: Budget,
        emitter: InvestigationEmitter,
        *,
        empty: bool = False,
    ) -> None:
        report.budget = budget.snapshot()
        async with self.sessionmaker() as session:
            investigation = await session.get(Investigation, investigation_id)
            if investigation is not None:
                investigation.status = (
                    InvestigationStatus.COMPLETED if not empty else InvestigationStatus.DRAFT
                )
                investigation.stage = (
                    InvestigationStage.DONE if not empty else InvestigationStage.IDLE
                )
                investigation.config = {
                    **investigation.config,
                    "last_scan": {
                        "at": utcnow().isoformat(),
                        "jobs": report.jobs_run,
                        "errors": report.errors[:20],
                        "budget": report.budget,
                    },
                }
                await session.commit()
            projection = GraphProjection(session, investigation_id)
            graph = await projection.build()

        await emitter.emit("stage_changed", stage=InvestigationStage.ANALYSIS)
        await emitter.emit(
            "investigation_completed",
            stats=graph["stats"],
            jobs=report.jobs_run,
            matches=report.matches,
            errors=report.errors[:20],
            budget=report.budget,
        )

    async def _select_providers(
        self, names: list[str] | None, emitter: InvestigationEmitter
    ) -> list[RegisteredProvider]:
        enabled = self.registry.enabled()
        if names:
            wanted = {n.lower() for n in names}
            enabled = [p for p in enabled if p.name.lower() in wanted]

        # A provider that reports itself unusable gets no jobs planned at all, rather than
        # a queue of runs that can only fail (e.g. an external CLI that is not installed).
        usable: list[RegisteredProvider] = []
        for entry in enabled:
            try:
                health = await entry.provider.health_check()
            except Exception as exc:
                await emitter.emit("provider_skipped", provider=entry.name, reason=str(exc))
                continue
            if health.usable:
                usable.append(entry)
            else:
                await emitter.emit(
                    "provider_skipped", provider=entry.name, reason=health.detail
                )
        return usable


@dataclass
class _JobOutcome:
    provider: str
    observations: int = 0
    entities_created: int = 0
    relationships_created: int = 0
    derived: list[ProviderTarget] = field(default_factory=list)
    error: str | None = None


async def _count_entities(session: AsyncSession, investigation_id: uuid.UUID) -> int:
    from sqlalchemy import func

    stmt = select(func.count(Entity.id)).where(Entity.investigation_id == investigation_id)
    return int((await session.execute(stmt)).scalar_one())
