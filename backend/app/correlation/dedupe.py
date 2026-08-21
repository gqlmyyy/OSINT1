"""Entity deduplication (spec 18).

Two layers:

* **Write-time** — ``GraphStore.upsert_entity`` keys on ``canonical_key``, so the same
  account reported by five providers is one node. This is the guarantee.
* **Audit-time** — this service finds nodes that *escaped* that guarantee (a provider
  reporting a repository under a different owner casing, two URL forms of one page) and
  can merge them, moving identifiers, observations and edges onto the survivor.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EntityType
from app.evidence.canonical import relationship_dedupe_key
from app.models import Entity, Identifier, Observation, Relationship

#: Identifier kinds that uniquely pin an entity when they match exactly.
STRONG_KINDS = frozenset({"email", "url", "domain", "repository", "avatar"})


@dataclass
class DuplicateGroup:
    reason: str
    kind: str
    value: str
    entity_ids: list[uuid.UUID]
    labels: list[str]


class DeduplicationService:
    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID) -> None:
        self.session = session
        self.investigation_id = investigation_id

    async def find_duplicates(self) -> list[DuplicateGroup]:
        entities = {
            e.id: e
            for e in (
                await self.session.execute(
                    select(Entity).where(Entity.investigation_id == self.investigation_id)
                )
            ).scalars()
        }
        identifiers = list(
            (
                await self.session.execute(
                    select(Identifier).where(
                        Identifier.investigation_id == self.investigation_id
                    )
                )
            ).scalars()
        )

        buckets: dict[tuple[str, str], set[uuid.UUID]] = defaultdict(set)
        for row in identifiers:
            if row.kind in STRONG_KINDS:
                buckets[(row.kind, row.normalized)].add(row.entity_id)

        groups: list[DuplicateGroup] = []
        for (kind, value), ids in buckets.items():
            if len(ids) < 2:
                continue
            members = [entities[i] for i in ids if i in entities]
            # Same identifier on entities of the same type is a genuine duplicate;
            # across types it is a legitimate relationship, not a duplicate.
            by_type: dict[str, list[Entity]] = defaultdict(list)
            for member in members:
                by_type[member.type].append(member)
            for entity_type, same_type in by_type.items():
                if len(same_type) < 2 or entity_type == EntityType.URL:
                    continue
                groups.append(
                    DuplicateGroup(
                        reason=f"{len(same_type)} {entity_type} entities share the same {kind}",
                        kind=kind,
                        value=value,
                        entity_ids=[e.id for e in same_type],
                        labels=[e.label for e in same_type],
                    )
                )
        return groups

    async def merge(self, survivor_id: uuid.UUID, duplicate_id: uuid.UUID) -> Entity:
        """Fold ``duplicate`` into ``survivor``, preserving all evidence."""
        if survivor_id == duplicate_id:
            raise ValueError("cannot merge an entity into itself")
        survivor = await self.session.get(Entity, survivor_id)
        duplicate = await self.session.get(Entity, duplicate_id)
        if survivor is None or duplicate is None:
            raise LookupError("entity not found")
        if survivor.investigation_id != duplicate.investigation_id:
            raise ValueError("entities belong to different investigations")

        merged = dict(duplicate.attributes)
        merged.update({k: v for k, v in survivor.attributes.items() if v not in (None, "", [], {})})
        survivor.attributes = merged
        survivor.sources = sorted({*survivor.sources, *duplicate.sources})
        survivor.confidence = max(survivor.confidence, duplicate.confidence)
        survivor.first_seen = min(survivor.first_seen, duplicate.first_seen)
        survivor.last_seen = max(survivor.last_seen, duplicate.last_seen)
        survivor.attributes["merged_from"] = [
            *survivor.attributes.get("merged_from", []),
            duplicate.canonical_key,
        ]

        await self.session.execute(
            update(Observation)
            .where(Observation.entity_id == duplicate_id)
            .values(entity_id=survivor_id)
        )
        await self._move_identifiers(survivor_id, duplicate_id)
        await self._move_edges(survivor, duplicate)

        await self.session.delete(duplicate)
        await self.session.flush()
        return survivor

    async def _move_identifiers(self, survivor_id: uuid.UUID, duplicate_id: uuid.UUID) -> None:
        existing = {
            (row.kind, row.normalized)
            for row in (
                await self.session.execute(
                    select(Identifier).where(Identifier.entity_id == survivor_id)
                )
            ).scalars()
        }
        for row in (
            await self.session.execute(
                select(Identifier).where(Identifier.entity_id == duplicate_id)
            )
        ).scalars():
            if (row.kind, row.normalized) in existing:
                await self.session.delete(row)
            else:
                row.entity_id = survivor_id
        await self.session.flush()

    async def _move_edges(self, survivor: Entity, duplicate: Entity) -> None:
        edges = list(
            (
                await self.session.execute(
                    select(Relationship).where(
                        Relationship.investigation_id == self.investigation_id
                    )
                )
            ).scalars()
        )
        seen = {
            e.dedupe_key
            for e in edges
            if duplicate.id not in (e.source_entity_id, e.target_entity_id)
        }
        for edge in edges:
            if duplicate.id not in (edge.source_entity_id, edge.target_entity_id):
                continue
            if edge.source_entity_id == duplicate.id:
                edge.source_entity_id = survivor.id
            if edge.target_entity_id == duplicate.id:
                edge.target_entity_id = survivor.id
            if edge.source_entity_id == edge.target_entity_id:
                await self.session.delete(edge)
                continue
            source_key = (
                survivor.canonical_key
                if edge.source_entity_id == survivor.id
                else await self._key(edge.source_entity_id)
            )
            target_key = (
                survivor.canonical_key
                if edge.target_entity_id == survivor.id
                else await self._key(edge.target_entity_id)
            )
            new_key = relationship_dedupe_key(source_key, edge.type, target_key)
            if new_key in seen:
                await self.session.delete(edge)
                continue
            edge.dedupe_key = new_key
            seen.add(new_key)
        await self.session.flush()

    async def _key(self, entity_id: uuid.UUID) -> str:
        entity = await self.session.get(Entity, entity_id)
        return entity.canonical_key if entity else str(entity_id)
