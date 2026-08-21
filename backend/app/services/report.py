"""Evidence report assembly (spec 23).

The report is built from stored rows only. It never states a conclusion the graph does
not hold, and it labels every statement with its assertion class so a reader can tell an
observed fact from a correlation at a glance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Assertion, MatchBand
from app.correlation.engine import BAND_LABELS
from app.models import (
    Entity,
    Evidence,
    IdentityCandidate,
    Investigation,
    Observation,
    ProviderRun,
    Relationship,
    Target,
)

ASSERTION_LABELS = {
    Assertion.OBSERVED: "Observed fact",
    Assertion.INFERRED: "Inference",
    Assertion.CORRELATED: "Correlation",
    Assertion.UNVERIFIED: "Unverified claim",
}

DISCLAIMER = (
    "This report aggregates publicly available information. Correlations are probabilistic "
    "and are not statements of identity. Verify every conclusion against the linked evidence "
    "before acting on it."
)


class ReportBuilder:
    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID) -> None:
        self.session = session
        self.investigation_id = investigation_id

    async def build(self, min_confidence: float = 0.0) -> dict[str, Any]:
        investigation = await self.session.get(Investigation, self.investigation_id)
        if investigation is None:
            raise LookupError("investigation not found")

        targets = await self._scalars(select(Target).where(Target.investigation_id == self.investigation_id))
        entities = [
            e
            for e in await self._scalars(
                select(Entity).where(Entity.investigation_id == self.investigation_id)
            )
            if e.confidence >= min_confidence
        ]
        keep = {e.id for e in entities}
        relationships = [
            r
            for r in await self._scalars(
                select(Relationship).where(Relationship.investigation_id == self.investigation_id)
            )
            if r.source_entity_id in keep and r.target_entity_id in keep
        ]
        observations = await self._scalars(
            select(Observation).where(Observation.investigation_id == self.investigation_id)
        )
        candidates = await self._scalars(
            select(IdentityCandidate).where(
                IdentityCandidate.investigation_id == self.investigation_id
            )
        )
        runs = await self._scalars(
            select(ProviderRun).where(ProviderRun.investigation_id == self.investigation_id)
        )
        evidence_rows = await self._scalars(
            select(Evidence).join(Observation).where(
                Observation.investigation_id == self.investigation_id
            )
        )
        evidence_by_observation: dict[uuid.UUID, list[Evidence]] = {}
        for row in evidence_rows:
            evidence_by_observation.setdefault(row.observation_id, []).append(row)

        labels = {e.id: e.label for e in entities}
        by_type: dict[str, list[Entity]] = {}
        for entity in entities:
            by_type.setdefault(entity.type, []).append(entity)

        return {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "disclaimer": DISCLAIMER,
            "investigation": {
                "id": str(investigation.id),
                "name": investigation.name,
                "description": investigation.description,
                "status": investigation.status,
                "created_at": investigation.created_at.isoformat(),
                "tags": investigation.tags,
            },
            "targets": [{"type": t.type, "value": t.value} for t in targets],
            "summary": {
                "entities": len(entities),
                "relationships": len(relationships),
                "observations": len(observations),
                "providers_used": sorted({r.provider for r in runs}),
                "accounts_discovered": len(by_type.get("social_account", [])),
                "domains": len(by_type.get("domain", [])),
                "repositories": len(by_type.get("repository", [])),
                "emails": len(by_type.get("email", [])),
                "strong_correlations": len(
                    [c for c in candidates if c.band in (MatchBand.STRONG, MatchBand.CONFIRMED)]
                ),
                "possible_matches": len(
                    [c for c in candidates if c.band in (MatchBand.POSSIBLE, MatchBand.PROBABLE)]
                ),
                "by_type": {k: len(v) for k, v in sorted(by_type.items())},
            },
            "entities": [
                {
                    "id": str(e.id),
                    "type": e.type,
                    "label": e.label,
                    "confidence": e.confidence,
                    "sources": e.sources,
                    "first_seen": e.first_seen.isoformat(),
                    "last_seen": e.last_seen.isoformat(),
                    "attributes": e.attributes,
                }
                for e in sorted(entities, key=lambda e: (-e.confidence, e.type, e.label))
            ],
            "relationships": [
                {
                    "id": str(r.id),
                    "source": labels.get(r.source_entity_id, str(r.source_entity_id)),
                    "target": labels.get(r.target_entity_id, str(r.target_entity_id)),
                    "type": r.type,
                    "confidence": r.confidence,
                    "assertion": r.assertion,
                    "assertion_label": ASSERTION_LABELS.get(
                        Assertion(r.assertion), r.assertion
                    ),
                    "provider": r.provider,
                    "why": r.evidence.get("why", ""),
                    "evidence_url": r.evidence.get("url"),
                }
                for r in sorted(relationships, key=lambda r: -r.confidence)
            ],
            "evidence": [
                {
                    "observation_id": str(o.id),
                    "entity": labels.get(o.entity_id, ""),
                    "provider": o.provider,
                    "source": o.source,
                    "kind": o.kind,
                    "url": o.url,
                    "observed_at": o.observed_at.isoformat(),
                    "confidence": o.confidence,
                    "assertion": o.assertion,
                    "assertion_label": ASSERTION_LABELS.get(Assertion(o.assertion), o.assertion),
                    "data": o.data,
                    "sha256": [e.sha256 for e in evidence_by_observation.get(o.id, [])],
                    "excerpt": next(
                        (e.excerpt for e in evidence_by_observation.get(o.id, []) if e.excerpt), ""
                    ),
                }
                for o in sorted(observations, key=lambda o: o.observed_at, reverse=True)
            ],
            "correlation_analysis": [
                {
                    "id": str(c.id),
                    "a": labels.get(c.entity_a_id, str(c.entity_a_id)),
                    "b": labels.get(c.entity_b_id, str(c.entity_b_id)),
                    "score": c.score,
                    "band": c.band,
                    "band_label": BAND_LABELS.get(MatchBand(c.band), c.band),
                    "reasons": c.reasons,
                    "explanation": c.explanation,
                }
                for c in sorted(candidates, key=lambda c: -c.score)
            ],
            "unverified_claims": [
                {
                    "entity": labels.get(o.entity_id, ""),
                    "provider": o.provider,
                    "kind": o.kind,
                    "url": o.url,
                    "data": o.data,
                }
                for o in observations
                if o.assertion == Assertion.UNVERIFIED
            ],
            "provider_runs": [
                {
                    "provider": r.provider,
                    "status": r.status,
                    "duration_ms": r.duration_ms,
                    "observations": r.observation_count,
                    "cache_hit": r.cache_hit,
                    "error": r.error,
                }
                for r in runs
            ],
        }

    async def _scalars(self, stmt: Any) -> list[Any]:
        return list((await self.session.execute(stmt)).scalars())
