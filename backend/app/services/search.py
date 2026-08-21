"""Internal search (spec 15). PostgreSQL full-text when available, portable LIKE otherwise."""

from __future__ import annotations

import uuid

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, Identifier, Observation


class SearchService:
    """The seam an Elasticsearch backend would slot into — see ADR A7."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def _is_postgres(self) -> bool:
        return self.session.bind is not None and self.session.bind.dialect.name == "postgresql"

    async def search(
        self, investigation_id: uuid.UUID, query: str, limit: int = 50
    ) -> list[dict[str, object]]:
        needle = query.strip()
        if not needle:
            return []
        hits: dict[uuid.UUID, dict[str, object]] = {}

        for entity, matched_on, snippet in await self._entity_hits(investigation_id, needle, limit):
            hits.setdefault(
                entity.id,
                {
                    "entity_id": entity.id,
                    "label": entity.label,
                    "type": entity.type,
                    "confidence": entity.confidence,
                    "matched_on": matched_on,
                    "snippet": snippet,
                },
            )

        for entity, matched_on, snippet in await self._observation_hits(
            investigation_id, needle, limit
        ):
            hits.setdefault(
                entity.id,
                {
                    "entity_id": entity.id,
                    "label": entity.label,
                    "type": entity.type,
                    "confidence": entity.confidence,
                    "matched_on": matched_on,
                    "snippet": snippet,
                },
            )
        return list(hits.values())[:limit]

    async def _entity_hits(
        self, investigation_id: uuid.UUID, needle: str, limit: int
    ) -> list[tuple[Entity, str, str]]:
        pattern = f"%{needle.lower()}%"
        stmt = (
            select(Entity, Identifier.kind, Identifier.value)
            .outerjoin(Identifier, Identifier.entity_id == Entity.id)
            .where(
                Entity.investigation_id == investigation_id,
                or_(
                    func.lower(Entity.label).like(pattern),
                    func.lower(Entity.canonical_key).like(pattern),
                    func.lower(Identifier.normalized).like(pattern),
                ),
            )
            .limit(limit * 3)
        )
        results: list[tuple[Entity, str, str]] = []
        for entity, kind, value in (await self.session.execute(stmt)).all():
            if kind and needle.lower() in str(value).lower():
                results.append((entity, f"identifier:{kind}", str(value)))
            else:
                results.append((entity, "label", entity.label))
        return results

    async def _observation_hits(
        self, investigation_id: uuid.UUID, needle: str, limit: int
    ) -> list[tuple[Entity, str, str]]:
        pattern = f"%{needle.lower()}%"
        stmt = (
            select(Entity, Observation.provider, Observation.url, Observation.data)
            .join(Observation, Observation.entity_id == Entity.id)
            .where(
                Observation.investigation_id == investigation_id,
                or_(
                    func.lower(func.coalesce(Observation.url, "")).like(pattern),
                    func.lower(cast(Observation.data, String)).like(pattern),
                ),
            )
            .limit(limit)
        )
        return [
            (entity, f"observation:{provider}", url or str(data)[:200])
            for entity, provider, url, data in (await self.session.execute(stmt)).all()
        ]
