"""Graph intelligence (spec 38), computed with NetworkX over the projected graph."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import networkx as nx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MatchBand
from app.models import Entity, Identifier, IdentityCandidate, Relationship


@dataclass
class PathStep:
    entity_id: str
    label: str
    type: str
    via: str | None = None
    via_confidence: float | None = None
    why: str | None = None


class GraphAnalytics:
    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID) -> None:
        self.session = session
        self.investigation_id = investigation_id
        self._graph: nx.MultiDiGraph | None = None
        self._entities: dict[str, Entity] = {}

    async def load(self) -> nx.MultiDiGraph:
        if self._graph is not None:
            return self._graph
        entities = list(
            (
                await self.session.execute(
                    select(Entity).where(Entity.investigation_id == self.investigation_id)
                )
            ).scalars()
        )
        relationships = list(
            (
                await self.session.execute(
                    select(Relationship).where(
                        Relationship.investigation_id == self.investigation_id
                    )
                )
            ).scalars()
        )
        graph: nx.MultiDiGraph = nx.MultiDiGraph()
        for entity in entities:
            self._entities[str(entity.id)] = entity
            graph.add_node(
                str(entity.id),
                label=entity.label,
                type=entity.type,
                confidence=entity.confidence,
            )
        for rel in relationships:
            graph.add_edge(
                str(rel.source_entity_id),
                str(rel.target_entity_id),
                key=str(rel.id),
                type=rel.type,
                confidence=rel.confidence,
                assertion=rel.assertion,
                why=rel.evidence.get("why", ""),
            )
        self._graph = graph
        return graph

    # -- paths -----------------------------------------------------------------

    async def shortest_path(self, source_id: str, target_id: str) -> list[PathStep]:
        graph = await self.load()
        if source_id not in graph or target_id not in graph:
            raise LookupError("entity not present in this investigation graph")
        undirected = graph.to_undirected(as_view=False)
        try:
            node_path: list[str] = nx.shortest_path(undirected, source_id, target_id)
        except nx.NetworkXNoPath:
            return []

        steps: list[PathStep] = []
        for index, node_id in enumerate(node_path):
            entity = self._entities[node_id]
            step = PathStep(entity_id=node_id, label=entity.label, type=entity.type)
            if index > 0:
                previous = node_path[index - 1]
                edge = self._best_edge(graph, previous, node_id)
                if edge:
                    step.via = edge["type"]
                    step.via_confidence = edge["confidence"]
                    step.why = edge.get("why") or None
            steps.append(step)
        return steps

    @staticmethod
    def _best_edge(graph: nx.MultiDiGraph, a: str, b: str) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        if graph.has_edge(a, b):
            candidates.extend(graph.get_edge_data(a, b).values())
        if graph.has_edge(b, a):
            candidates.extend(graph.get_edge_data(b, a).values())
        return max(candidates, key=lambda e: e["confidence"]) if candidates else None

    async def common_neighbors(self, a_id: str, b_id: str) -> list[dict[str, Any]]:
        graph = (await self.load()).to_undirected(as_view=False)
        if a_id not in graph or b_id not in graph:
            raise LookupError("entity not present in this investigation graph")
        shared = set(graph.neighbors(a_id)) & set(graph.neighbors(b_id))
        return [
            {
                "id": node_id,
                "label": self._entities[node_id].label,
                "type": self._entities[node_id].type,
            }
            for node_id in sorted(shared)
        ]

    async def shared_identifiers(self, a_id: str, b_id: str) -> list[dict[str, str]]:
        rows = list(
            (
                await self.session.execute(
                    select(Identifier).where(
                        Identifier.investigation_id == self.investigation_id,
                        Identifier.entity_id.in_([uuid.UUID(a_id), uuid.UUID(b_id)]),
                    )
                )
            ).scalars()
        )
        left = {(r.kind, r.normalized) for r in rows if str(r.entity_id) == a_id}
        right = {(r.kind, r.normalized) for r in rows if str(r.entity_id) == b_id}
        return [{"kind": k, "value": v} for k, v in sorted(left & right)]

    # -- structure -------------------------------------------------------------

    async def centrality(self, metric: str = "degree", limit: int = 20) -> list[dict[str, Any]]:
        graph = (await self.load()).to_undirected(as_view=False)
        if graph.number_of_nodes() == 0:
            return []
        if metric == "betweenness":
            scores = nx.betweenness_centrality(nx.Graph(graph))
        elif metric == "closeness":
            scores = nx.closeness_centrality(nx.Graph(graph))
        else:
            scores = nx.degree_centrality(graph)
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        return [
            {
                "id": node_id,
                "label": self._entities[node_id].label,
                "type": self._entities[node_id].type,
                "score": round(score, 4),
                "degree": graph.degree(node_id),
            }
            for node_id, score in ranked
        ]

    async def connected_components(self) -> list[dict[str, Any]]:
        graph = (await self.load()).to_undirected(as_view=False)
        components = sorted(nx.connected_components(graph), key=len, reverse=True)
        return [
            {
                "index": index,
                "size": len(component),
                "members": [
                    {"id": n, "label": self._entities[n].label, "type": self._entities[n].type}
                    for n in sorted(component)
                ][:50],
            }
            for index, component in enumerate(components)
        ]

    async def isolated(self) -> list[dict[str, Any]]:
        graph = (await self.load()).to_undirected(as_view=False)
        return [
            {"id": n, "label": self._entities[n].label, "type": self._entities[n].type}
            for n in graph.nodes
            if graph.degree(n) == 0
        ]

    async def clusters(self) -> list[dict[str, Any]]:
        """Label-propagation communities: deterministic, no extra dependency."""
        graph = nx.Graph((await self.load()).to_undirected(as_view=False))
        if graph.number_of_nodes() == 0:
            return []
        communities = list(nx.community.label_propagation_communities(graph))
        communities.sort(key=len, reverse=True)
        return [
            {
                "index": index,
                "size": len(community),
                "label": _community_label(
                    [self._entities[n] for n in community if n in self._entities]
                ),
                "members": [
                    {"id": n, "label": self._entities[n].label, "type": self._entities[n].type}
                    for n in sorted(community)
                ][:50],
            }
            for index, community in enumerate(communities)
        ]

    # -- identity --------------------------------------------------------------

    async def strong_correlations(self, threshold: float = 0.85) -> list[dict[str, Any]]:
        rows = list(
            (
                await self.session.execute(
                    select(IdentityCandidate).where(
                        IdentityCandidate.investigation_id == self.investigation_id,
                        IdentityCandidate.score >= threshold,
                    )
                )
            ).scalars()
        )
        await self.load()
        return [
            {
                "id": str(row.id),
                "a": self._describe(row.entity_a_id),
                "b": self._describe(row.entity_b_id),
                "score": row.score,
                "band": row.band,
                "reasons": row.reasons,
                "explanation": row.explanation,
            }
            for row in sorted(rows, key=lambda r: -r.score)
        ]

    async def duplicate_identities(self) -> list[dict[str, Any]]:
        from app.correlation.dedupe import DeduplicationService

        service = DeduplicationService(self.session, self.investigation_id)
        groups = await service.find_duplicates()
        return [
            {
                "reason": g.reason,
                "kind": g.kind,
                "value": g.value,
                "entity_ids": [str(i) for i in g.entity_ids],
                "labels": g.labels,
            }
            for g in groups
        ]

    def _describe(self, entity_id: uuid.UUID) -> dict[str, Any]:
        entity = self._entities.get(str(entity_id))
        if entity is None:
            return {"id": str(entity_id), "label": "(removed)", "type": "unknown"}
        return {"id": str(entity_id), "label": entity.label, "type": entity.type}


def _community_label(members: list[Entity]) -> str:
    if not members:
        return "empty"
    platforms = [
        str(m.attributes.get("platform")) for m in members if m.attributes.get("platform")
    ]
    if platforms:
        return f"{max(set(platforms), key=platforms.count)} community"
    types = [m.type for m in members]
    return f"{max(set(types), key=types.count)} community"


BAND_ORDER = [MatchBand.POSSIBLE, MatchBand.PROBABLE, MatchBand.STRONG, MatchBand.CONFIRMED]
