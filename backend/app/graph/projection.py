"""Graph projection for the API/UI, with clustering for large investigations."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import CORRELATION_EDGES, MatchBand, RelationshipType
from app.models import Entity, IdentityCandidate, Relationship


@dataclass
class GraphFilters:
    min_confidence: float = 0.0
    entity_types: set[str] = field(default_factory=set)
    relationship_types: set[str] = field(default_factory=set)
    since: datetime | None = None
    until: datetime | None = None
    expand: str | None = None
    query: str | None = None
    limit: int | None = None

    def matches_entity(self, entity: Entity) -> bool:
        if entity.confidence < self.min_confidence:
            return False
        if self.entity_types and entity.type not in self.entity_types:
            return False
        if self.since and _aware(entity.last_seen) < self.since:
            return False
        if self.until and _aware(entity.first_seen) > self.until:
            return False
        if self.query:
            needle = self.query.lower()
            if needle not in entity.label.lower() and needle not in entity.canonical_key.lower():
                return False
        return True


def cluster_of(entity: Entity) -> str:
    """Which visual bucket a node belongs to (spec 30)."""
    platform = entity.attributes.get("platform")
    if entity.type == "social_account" and isinstance(platform, str) and platform:
        return platform.lower()
    return str(entity.type)


CLUSTER_LABELS = {
    "social_account": "Social",
    "domain": "Domains",
    "repository": "Repositories",
    "url": "URLs",
    "email": "Emails",
    "ip": "IP addresses",
}


class GraphProjection:
    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID) -> None:
        self.session = session
        self.investigation_id = investigation_id
        self.settings = get_settings()

    async def build(self, filters: GraphFilters | None = None) -> dict[str, Any]:
        filters = filters or GraphFilters()
        entities = [
            e
            for e in (
                await self.session.execute(
                    select(Entity).where(Entity.investigation_id == self.investigation_id)
                )
            ).scalars()
            if filters.matches_entity(e)
        ]
        relationships = list(
            (
                await self.session.execute(
                    select(Relationship).where(
                        Relationship.investigation_id == self.investigation_id
                    )
                )
            ).scalars()
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

        keep = {e.id for e in entities}
        edges = [
            r
            for r in relationships
            if r.source_entity_id in keep
            and r.target_entity_id in keep
            and r.confidence >= filters.min_confidence
            and (not filters.relationship_types or r.type in filters.relationship_types)
            and (not filters.since or _aware(r.created_at) >= filters.since)
            and (not filters.until or _aware(r.created_at) <= filters.until)
        ]

        degree: Counter[uuid.UUID] = Counter()
        for edge in edges:
            degree[edge.source_entity_id] += 1
            degree[edge.target_entity_id] += 1

        clusters = _cluster_summary(entities)
        threshold = self.settings.graph_cluster_threshold
        clustered = len(entities) > threshold and filters.expand is None

        if clustered:
            visible = [e for e in entities if e.attributes.get("is_target") or e.depth == 0]
            nodes = [_node(e, degree, clustered=False) for e in visible]
            nodes.extend(
                _cluster_node(name, size)
                for name, size in clusters.items()
                if not any(cluster_of(e) == name for e in visible)
            )
            visible_ids = {e.id for e in visible}
            edges = [
                r for r in edges if r.source_entity_id in visible_ids and r.target_entity_id in visible_ids
            ]
        else:
            if filters.expand:
                entities = [e for e in entities if cluster_of(e) == filters.expand]
                keep = {e.id for e in entities}
                edges = [
                    r for r in edges if r.source_entity_id in keep and r.target_entity_id in keep
                ]
            if filters.limit:
                entities = sorted(entities, key=lambda e: (-degree[e.id], e.label))[: filters.limit]
                keep = {e.id for e in entities}
                edges = [
                    r for r in edges if r.source_entity_id in keep and r.target_entity_id in keep
                ]
            nodes = [_node(e, degree, clustered=False) for e in entities]

        return {
            "nodes": nodes,
            "edges": [_edge(r) for r in edges],
            "clusters": [
                {"id": name, "label": CLUSTER_LABELS.get(name, name.title()), "size": size}
                for name, size in sorted(clusters.items(), key=lambda kv: -kv[1])
            ],
            "clustered": clustered,
            "stats": self.stats(entities, relationships, candidates),
        }

    def stats(
        self,
        entities: list[Entity],
        relationships: list[Relationship],
        candidates: list[IdentityCandidate],
    ) -> dict[str, Any]:
        sources: set[str] = set()
        for entity in entities:
            sources.update(entity.sources)
        sources.discard("target")
        strong = [c for c in candidates if c.band in (MatchBand.STRONG, MatchBand.CONFIRMED)]
        possible = [c for c in candidates if c.band in (MatchBand.POSSIBLE, MatchBand.PROBABLE)]
        by_type = Counter(e.type for e in entities)
        return {
            "entities": len(entities),
            "relationships": len(relationships),
            "sources": len(sources),
            "strong_correlations": len(strong),
            "possible_matches": len(possible),
            "by_type": dict(by_type),
            "by_assertion": dict(Counter(r.assertion for r in relationships)),
        }


def _node(entity: Entity, degree: Counter[uuid.UUID], *, clustered: bool) -> dict[str, Any]:
    return {
        "id": str(entity.id),
        "type": entity.type,
        "label": entity.label,
        "canonical_key": entity.canonical_key,
        "confidence": round(entity.confidence, 4),
        "cluster": cluster_of(entity),
        "degree": degree.get(entity.id, 0),
        "depth": entity.depth,
        "is_target": bool(entity.attributes.get("is_target")),
        "sources": list(entity.sources),
        "first_seen": _iso(entity.first_seen),
        "last_seen": _iso(entity.last_seen),
        "is_cluster": clustered,
        "attributes": entity.attributes,
    }


def _cluster_node(name: str, size: int) -> dict[str, Any]:
    return {
        "id": f"cluster:{name}",
        "type": "cluster",
        "label": f"{CLUSTER_LABELS.get(name, name.title())} ({size})",
        "canonical_key": f"cluster:{name}",
        "confidence": 1.0,
        "cluster": name,
        "degree": size,
        "depth": 1,
        "is_target": False,
        "sources": [],
        "first_seen": None,
        "last_seen": None,
        "is_cluster": True,
        "attributes": {"size": size},
    }


def _edge(rel: Relationship) -> dict[str, Any]:
    return {
        "id": str(rel.id),
        "source": str(rel.source_entity_id),
        "target": str(rel.target_entity_id),
        "type": rel.type,
        "confidence": round(rel.confidence, 4),
        "assertion": rel.assertion,
        "provider": rel.provider,
        "is_correlation": rel.type in {str(t) for t in CORRELATION_EDGES},
        "created_at": _iso(rel.created_at),
        "why": rel.evidence.get("why", ""),
    }


def _cluster_summary(entities: list[Entity]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for entity in entities:
        counts[cluster_of(entity)] += 1
    return dict(counts)


def _aware(value: datetime) -> datetime:
    from datetime import timezone

    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value else None


__all__ = ["GraphFilters", "GraphProjection", "RelationshipType", "cluster_of"]
