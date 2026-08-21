"""Investigation lifecycle: create, add targets, progress, delete."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import InvestigationStatus, JobStatus, TargetType
from app.evidence.normalizer import NormalizationError, normalize
from app.graph.projection import GraphProjection
from app.models import Entity, IdentityCandidate, Investigation, Job, Relationship, Target
from app.schemas.investigation import InvestigationCreate, TargetIn


class InvestigationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, payload: InvestigationCreate, owner_id: uuid.UUID) -> Investigation:
        investigation = Investigation(
            id=uuid.uuid4(),
            name=payload.name,
            description=payload.description,
            owner_id=owner_id,
            tags=list(dict.fromkeys(payload.tags)),
            status=InvestigationStatus.DRAFT,
            config={},
        )
        self.session.add(investigation)
        await self.session.flush()
        if payload.targets:
            await self.add_targets(investigation.id, payload.targets)
        await self.session.refresh(investigation, ["targets"])
        return investigation

    async def add_targets(
        self, investigation_id: uuid.UUID, targets: list[TargetIn]
    ) -> tuple[list[Target], list[str]]:
        """Idempotent: re-adding an identifier returns the existing row, never a duplicate."""
        created: list[Target] = []
        rejected: list[str] = []
        for item in targets:
            try:
                target_type, normalized = normalize(item.value, item.type)
            except NormalizationError as exc:
                rejected.append(f"{item.value}: {exc}")
                continue
            existing = (
                await self.session.execute(
                    select(Target).where(
                        Target.investigation_id == investigation_id,
                        Target.type == str(target_type),
                        Target.normalized == normalized,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                created.append(existing)
                continue
            row = Target(
                id=uuid.uuid4(),
                investigation_id=investigation_id,
                type=str(target_type),
                value=item.value.strip(),
                normalized=normalized,
            )
            self.session.add(row)
            created.append(row)
        await self.session.flush()
        return created, rejected

    async def target_types(self, investigation_id: uuid.UUID) -> set[TargetType]:
        rows = (
            await self.session.execute(
                select(Target.type).where(Target.investigation_id == investigation_id)
            )
        ).scalars()
        return {TargetType(value) for value in rows}

    async def stats(self, investigation_id: uuid.UUID) -> dict[str, Any]:
        projection = GraphProjection(self.session, investigation_id)
        entities = list(
            (
                await self.session.execute(
                    select(Entity).where(Entity.investigation_id == investigation_id)
                )
            ).scalars()
        )
        relationships = list(
            (
                await self.session.execute(
                    select(Relationship).where(Relationship.investigation_id == investigation_id)
                )
            ).scalars()
        )
        candidates = list(
            (
                await self.session.execute(
                    select(IdentityCandidate).where(
                        IdentityCandidate.investigation_id == investigation_id
                    )
                )
            ).scalars()
        )
        return projection.stats(entities, relationships, candidates)

    async def progress(self, investigation: Investigation) -> dict[str, Any]:
        rows = list(
            (
                await self.session.execute(
                    select(Job.provider, Job.status, func.count(Job.id))
                    .where(Job.investigation_id == investigation.id)
                    .group_by(Job.provider, Job.status)
                )
            ).all()
        )
        by_provider: dict[str, dict[str, int]] = {}
        for provider, status, count in rows:
            bucket = by_provider.setdefault(provider, {})
            bucket[status] = bucket.get(status, 0) + int(count)

        providers: list[dict[str, Any]] = []
        total_done = total_all = 0
        for provider, counts in sorted(by_provider.items()):
            total = sum(counts.values())
            done = counts.get(JobStatus.SUCCEEDED, 0) + counts.get(JobStatus.FAILED, 0)
            failed = counts.get(JobStatus.FAILED, 0)
            total_done += done
            total_all += total
            providers.append(
                {
                    "provider": provider,
                    "total": total,
                    "done": done,
                    "failed": failed,
                    "percent": int(done / total * 100) if total else 0,
                    "status": (
                        "failed"
                        if failed == total and total
                        else "done"
                        if done == total
                        else "running"
                    ),
                }
            )

        correlation = 100 if investigation.status == InvestigationStatus.COMPLETED else (
            50 if investigation.stage == "correlation" else 0
        )
        return {
            "investigation_id": investigation.id,
            "status": investigation.status,
            "stage": investigation.stage,
            "percent": int(total_done / total_all * 100) if total_all else 0,
            "providers": providers,
            "correlation_percent": correlation,
        }

    async def delete(self, investigation_id: uuid.UUID) -> None:
        # Explicit child deletes keep behaviour identical on SQLite, where FK cascade
        # is off unless PRAGMA foreign_keys is enabled per connection.
        for model in (Relationship, IdentityCandidate, Entity, Job, Target):
            await self.session.execute(
                delete(model).where(model.investigation_id == investigation_id)  # type: ignore[attr-defined]
            )
        await self.session.execute(
            delete(Investigation).where(Investigation.id == investigation_id)
        )
        await self.session.flush()
