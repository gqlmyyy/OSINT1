"""Per-entity and per-investigation timelines (spec 16)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Assertion
from app.models import Entity, IdentityCandidate, Observation, Relationship


class TimelineService:
    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID) -> None:
        self.session = session
        self.investigation_id = investigation_id

    async def build(self, entity_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        entities = {
            e.id: e
            for e in (
                await self.session.execute(
                    select(Entity).where(Entity.investigation_id == self.investigation_id)
                )
            ).scalars()
        }

        obs_stmt = select(Observation).where(
            Observation.investigation_id == self.investigation_id
        )
        if entity_id:
            obs_stmt = obs_stmt.where(Observation.entity_id == entity_id)
        observations = list((await self.session.execute(obs_stmt)).scalars())

        events: list[dict[str, Any]] = [
            {
                "at": o.observed_at,
                "kind": "observation",
                "entity_id": o.entity_id,
                "label": entities[o.entity_id].label if o.entity_id in entities else o.kind,
                "description": (
                    f"{o.kind.replace('_', ' ')} observed via {o.provider}"
                    if o.assertion == Assertion.OBSERVED
                    else f"{o.kind.replace('_', ' ')} reported by {o.provider} ({o.assertion})"
                ),
                "provider": o.provider,
                "confidence": o.confidence,
                "url": o.url,
            }
            for o in observations
        ]

        rel_stmt = select(Relationship).where(
            Relationship.investigation_id == self.investigation_id
        )
        relationships = list((await self.session.execute(rel_stmt)).scalars())
        for rel in relationships:
            if entity_id and entity_id not in (rel.source_entity_id, rel.target_entity_id):
                continue
            source = entities.get(rel.source_entity_id)
            target = entities.get(rel.target_entity_id)
            if source is None or target is None:
                continue
            events.append(
                {
                    "at": rel.created_at,
                    "kind": "relationship",
                    "entity_id": rel.source_entity_id,
                    "label": f"{source.label} → {target.label}",
                    "description": f"{rel.type} ({rel.assertion}) — {rel.evidence.get('why', '')}",
                    "provider": rel.provider,
                    "confidence": rel.confidence,
                    "url": rel.evidence.get("url"),
                }
            )

        candidates = list(
            (
                await self.session.execute(
                    select(IdentityCandidate).where(
                        IdentityCandidate.investigation_id == self.investigation_id
                    )
                )
            ).scalars()
        )
        for candidate in candidates:
            if entity_id and entity_id not in (candidate.entity_a_id, candidate.entity_b_id):
                continue
            a = entities.get(candidate.entity_a_id)
            b = entities.get(candidate.entity_b_id)
            if a is None or b is None:
                continue
            events.append(
                {
                    "at": candidate.created_at,
                    "kind": "correlation",
                    "entity_id": candidate.entity_a_id,
                    "label": f"{a.label} ↔ {b.label}",
                    "description": candidate.explanation,
                    "provider": "correlation",
                    "confidence": candidate.score,
                    "url": None,
                }
            )

        return sorted(events, key=lambda e: e["at"])
