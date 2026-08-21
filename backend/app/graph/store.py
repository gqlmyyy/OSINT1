"""Graph persistence with deduplication and the evidence-required invariant."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Assertion, EntityType, RelationshipType
from app.evidence.canonical import relationship_dedupe_key
from app.models import Entity, Identifier, Relationship
from app.models.base import utcnow


class EvidenceRequired(ValueError):
    """Raised when a relationship is created without a backing observation (spec 39)."""


@dataclass
class UpsertResult:
    entity: Entity
    created: bool


@dataclass
class EdgeResult:
    relationship: Relationship
    created: bool


class GraphStore:
    """All entity/relationship writes go through here so dedup can never be bypassed."""

    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID) -> None:
        self.session = session
        self.investigation_id = investigation_id
        self._entity_cache: dict[str, Entity] = {}

    # -- entities --------------------------------------------------------------

    async def get_by_key(self, canonical_key: str) -> Entity | None:
        cached = self._entity_cache.get(canonical_key)
        if cached is not None:
            return cached
        stmt = select(Entity).where(
            Entity.investigation_id == self.investigation_id,
            Entity.canonical_key == canonical_key,
        )
        entity = (await self.session.execute(stmt)).scalar_one_or_none()
        if entity is not None:
            self._entity_cache[canonical_key] = entity
        return entity

    async def upsert_entity(
        self,
        *,
        entity_type: EntityType | str,
        canonical_key: str,
        label: str,
        confidence: float,
        attributes: dict[str, Any] | None = None,
        source: str | None = None,
        observed_at: datetime | None = None,
        depth: int = 0,
    ) -> UpsertResult:
        """Create or merge. A repeat sighting never produces a second node (spec 18)."""
        attributes = attributes or {}
        seen_at = observed_at or utcnow()
        existing = await self.get_by_key(canonical_key)

        if existing is not None:
            merged = dict(existing.attributes)
            for key, value in attributes.items():
                if value in (None, "", [], {}):
                    continue
                if key not in merged or merged[key] in (None, "", [], {}):
                    merged[key] = value
            existing.attributes = merged
            if source and source not in existing.sources:
                existing.sources = [*existing.sources, source]
            # Independent corroboration raises confidence without ever reaching certainty.
            existing.confidence = round(
                min(0.99, max(existing.confidence, confidence, 1 - (1 - existing.confidence) * (1 - confidence))),
                4,
            )
            if seen_at < _aware(existing.first_seen):
                existing.first_seen = seen_at
            if seen_at > _aware(existing.last_seen):
                existing.last_seen = seen_at
            existing.depth = min(existing.depth, depth)
            await self.session.flush()
            return UpsertResult(existing, created=False)

        entity = Entity(
            id=uuid.uuid4(),
            investigation_id=self.investigation_id,
            type=str(entity_type),
            label=label[:512],
            canonical_key=canonical_key[:512],
            confidence=round(max(0.0, min(0.99, confidence)), 4),
            attributes=attributes,
            sources=[source] if source else [],
            first_seen=seen_at,
            last_seen=seen_at,
            depth=depth,
        )
        self.session.add(entity)
        await self.session.flush()
        self._entity_cache[canonical_key] = entity
        return UpsertResult(entity, created=True)

    async def add_identifier(self, entity: Entity, kind: str, value: str, normalized: str) -> None:
        stmt = select(Identifier).where(
            Identifier.entity_id == entity.id,
            Identifier.kind == kind,
            Identifier.normalized == normalized,
        )
        if (await self.session.execute(stmt)).scalar_one_or_none() is not None:
            return
        self.session.add(
            Identifier(
                id=uuid.uuid4(),
                entity_id=entity.id,
                investigation_id=self.investigation_id,
                kind=kind,
                value=value[:512],
                normalized=normalized[:512],
            )
        )
        await self.session.flush()

    # -- relationships ---------------------------------------------------------

    async def add_relationship(
        self,
        *,
        source: Entity,
        target: Entity,
        rel_type: RelationshipType | str,
        confidence: float,
        provider: str,
        evidence: dict[str, Any],
        assertion: Assertion | str = Assertion.OBSERVED,
    ) -> EdgeResult:
        if not evidence or not (evidence.get("observation_id") or evidence.get("candidate_id")):
            raise EvidenceRequired(
                "a relationship requires evidence referencing an observation or a correlation "
                f"candidate (type={rel_type}, provider={provider})"
            )
        if source.id == target.id:
            raise ValueError("self-loops are not meaningful in this graph")

        dedupe = relationship_dedupe_key(source.canonical_key, str(rel_type), target.canonical_key)
        stmt = select(Relationship).where(
            Relationship.investigation_id == self.investigation_id,
            Relationship.dedupe_key == dedupe,
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            existing.confidence = round(
                min(0.99, 1 - (1 - existing.confidence) * (1 - confidence)), 4
            )
            corroborations = list(existing.evidence.get("corroborations", []))
            if evidence not in corroborations and evidence != {
                k: v for k, v in existing.evidence.items() if k != "corroborations"
            }:
                corroborations.append(evidence)
            existing.evidence = {**existing.evidence, "corroborations": corroborations[:20]}
            await self.session.flush()
            return EdgeResult(existing, created=False)

        relationship = Relationship(
            id=uuid.uuid4(),
            investigation_id=self.investigation_id,
            source_entity_id=source.id,
            target_entity_id=target.id,
            type=str(rel_type),
            confidence=round(max(0.0, min(0.99, confidence)), 4),
            assertion=str(assertion),
            provider=provider,
            dedupe_key=dedupe,
            evidence=evidence,
        )
        self.session.add(relationship)
        await self.session.flush()
        return EdgeResult(relationship, created=True)

    # -- reads -----------------------------------------------------------------

    async def entities(self) -> list[Entity]:
        stmt = select(Entity).where(Entity.investigation_id == self.investigation_id)
        return list((await self.session.execute(stmt)).scalars())

    async def relationships(self) -> list[Relationship]:
        stmt = select(Relationship).where(Relationship.investigation_id == self.investigation_id)
        return list((await self.session.execute(stmt)).scalars())

    async def entity_count(self) -> int:
        from sqlalchemy import func

        stmt = select(func.count(Entity.id)).where(Entity.investigation_id == self.investigation_id)
        return int((await self.session.execute(stmt)).scalar_one())


def _aware(value: datetime) -> datetime:
    from datetime import timezone

    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
