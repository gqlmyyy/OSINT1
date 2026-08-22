from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.common import ORMModel, StrictModel


class EntityOut(ORMModel):
    id: uuid.UUID
    investigation_id: uuid.UUID
    type: str
    label: str
    canonical_key: str
    confidence: float
    attributes: dict[str, Any]
    sources: list[str]
    first_seen: datetime
    last_seen: datetime
    depth: int


class IdentifierOut(ORMModel):
    kind: str
    value: str
    normalized: str


class EvidenceOut(ORMModel):
    id: uuid.UUID
    content_type: str
    sha256: str
    excerpt: str
    payload: dict[str, Any]
    captured_at: datetime


class ObservationOut(ORMModel):
    id: uuid.UUID
    entity_id: uuid.UUID | None
    provider: str
    source: str
    kind: str
    url: str | None
    observed_at: datetime
    confidence: float
    assertion: str
    data: dict[str, Any]
    evidence: list[EvidenceOut] = Field(default_factory=list)


class RelationshipOut(ORMModel):
    id: uuid.UUID
    source_entity_id: uuid.UUID
    target_entity_id: uuid.UUID
    type: str
    confidence: float
    assertion: str
    provider: str
    evidence: dict[str, Any]
    created_at: datetime


class RelationshipDetail(RelationshipOut):
    """The "why does this relationship exist?" payload (spec 39)."""

    source_label: str
    target_label: str
    why: str
    evidence_url: str | None
    observation: ObservationOut | None = None


class EntityDetail(EntityOut):
    display_confidence: float = 0.0
    is_stale: bool = False
    staleness_note: str | None = None
    identifiers: list[IdentifierOut] = Field(default_factory=list)
    observation_count: int = 0
    relationships: list[RelationshipOut] = Field(default_factory=list)
    external_links: list[str] = Field(default_factory=list)
    recent_observations: list[ObservationOut] = Field(default_factory=list)


class TimelineEvent(StrictModel):
    at: datetime
    kind: str
    entity_id: uuid.UUID | None
    label: str
    description: str
    provider: str
    confidence: float
    url: str | None = None


class GraphNode(StrictModel):
    id: str
    type: str
    label: str
    canonical_key: str
    confidence: float
    display_confidence: float = Field(description="Age-discounted confidence for display")
    is_stale: bool = False
    staleness_note: str | None = None
    cluster: str
    degree: int
    depth: int
    is_target: bool
    sources: list[str]
    first_seen: datetime | None
    last_seen: datetime | None
    is_cluster: bool
    attributes: dict[str, Any]


class GraphEdge(StrictModel):
    id: str
    source: str
    target: str
    type: str
    confidence: float
    assertion: str
    provider: str
    is_correlation: bool
    created_at: datetime | None
    why: str


class GraphCluster(StrictModel):
    id: str
    label: str
    size: int


class GraphOut(StrictModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    clusters: list[GraphCluster]
    clustered: bool
    stats: dict[str, Any]


class MatchOut(ORMModel):
    id: uuid.UUID
    entity_a_id: uuid.UUID
    entity_b_id: uuid.UUID
    entity_a_label: str = ""
    entity_b_label: str = ""
    score: float
    band: str
    band_label: str = ""
    reasons: list[str]
    explanation: str
    #: False-positive assessment: {level, reasons[], missing[]}. Separate from `score`
    #: on purpose — score says how much evidence there is, this says what kind it is and
    #: what is conspicuously absent. Empty for candidates recorded before the assessment
    #: existed; they gain one on the next correlation run.
    risk: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ShortestPathRequest(StrictModel):
    source_id: uuid.UUID
    target_id: uuid.UUID


class PairRequest(StrictModel):
    a_id: uuid.UUID
    b_id: uuid.UUID


class PathStepOut(StrictModel):
    entity_id: str
    label: str
    type: str
    via: str | None = None
    via_confidence: float | None = None
    why: str | None = None


class PathOut(StrictModel):
    found: bool
    steps: list[PathStepOut]
    markdown: str


class SearchHit(StrictModel):
    entity_id: uuid.UUID
    label: str
    type: str
    confidence: float
    matched_on: str
    snippet: str


class SearchOut(StrictModel):
    query: str
    hits: list[SearchHit]
    total: int


class FindingOut(StrictModel):
    """One plain-language conclusion, with the entities and evidence behind it."""

    kind: str
    severity: str
    headline: str
    detail: str
    entity_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class FindingsOut(StrictModel):
    summary: dict[str, Any]
    findings: list[FindingOut]


class InteractionOut(StrictModel):
    """Public activity between two accounts. A count, never an identity claim."""

    actor_id: uuid.UUID
    target_id: uuid.UUID
    actor_label: str
    target_label: str
    comments: int
    replies: int
    mentions: int
    total: int
    strength: str
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    identity_match: str = "not established"
