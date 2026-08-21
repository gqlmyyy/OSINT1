"""Entity resolution.

The engine never says "this is the same person". It produces a *candidate identity* with
a calibrated probability, a band, and the list of reasons that produced it — positive and
negative — so an analyst can disagree with it on the evidence.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import Assertion, EntityType, MatchBand, RelationshipType
from app.correlation.signals import DEFAULT_SIGNALS, EntityView, MatchSignal, SignalOutcome
from app.models import Entity, Identifier, IdentityCandidate
from app.models.base import utcnow

#: Prior odds that two entities *inside one investigation* are the same identity.
#: Not the base rate over the whole internet: everything here was reached from the same
#: seed target, so the prior is raised accordingly - but kept low enough that a single
#: weak signal lands in "Possible Match" and never higher.
PRIOR_ODDS = 0.15 / 0.85

#: Entity types that can plausibly denote a person's presence somewhere.
CORRELATABLE = frozenset(
    {
        EntityType.SOCIAL_ACCOUNT,
        EntityType.USERNAME,
        EntityType.EMAIL,
        EntityType.PERSON,
        EntityType.REPOSITORY,
    }
)

BAND_THRESHOLDS: list[tuple[float, MatchBand]] = [
    (0.97, MatchBand.CONFIRMED),
    (0.85, MatchBand.STRONG),
    (0.70, MatchBand.PROBABLE),
    (0.50, MatchBand.POSSIBLE),
]

BAND_LABELS: dict[MatchBand, str] = {
    MatchBand.POSSIBLE: "Possible Match",
    MatchBand.PROBABLE: "Probable Match",
    MatchBand.STRONG: "Strong Match",
    MatchBand.CONFIRMED: "Confirmed by Evidence",
}


@dataclass
class MatchAssessment:
    entity_a: Entity
    entity_b: Entity
    score: float
    band: MatchBand
    outcomes: list[SignalOutcome] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [
            f"{'+' if o.fired else '-'} {o.reason}"
            for o in sorted(self.outcomes, key=lambda o: -abs(o.log_odds))
        ]

    @property
    def explanation(self) -> str:
        return (
            f"{BAND_LABELS[self.band]} at {self.score:.0%} identity confidence. "
            f"This is a correlation, not a verified identity: "
            f"{len([o for o in self.outcomes if o.fired])} supporting and "
            f"{len([o for o in self.outcomes if not o.fired])} contradicting signal(s)."
        )


def band_for(score: float, has_direct_evidence: bool) -> MatchBand:
    for threshold, band in BAND_THRESHOLDS:
        if score >= threshold:
            # "Confirmed by Evidence" requires an actually-observed shared artefact,
            # never an accumulation of weak heuristics.
            if band is MatchBand.CONFIRMED and not has_direct_evidence:
                return MatchBand.STRONG
            return band
    return MatchBand.POSSIBLE


class CorrelationEngine:
    def __init__(
        self,
        signals: list[MatchSignal] | None = None,
        *,
        min_score: float | None = None,
    ) -> None:
        self.signals = signals if signals is not None else DEFAULT_SIGNALS
        self.min_score = (
            min_score if min_score is not None else get_settings().correlation_min_score
        )

    # -- scoring ---------------------------------------------------------------

    def assess(self, a: EntityView, b: EntityView) -> tuple[float, MatchBand, list[SignalOutcome]]:
        outcomes: list[SignalOutcome] = []
        for signal in self.signals:
            outcome = signal.evaluate(a, b)
            if outcome is not None:
                outcomes.append(outcome)

        log_odds = math.log(PRIOR_ODDS) + sum(o.log_odds for o in outcomes)
        # Clamp so no combination of signals can assert certainty.
        log_odds = max(-12.0, min(12.0, log_odds))
        score = 1 / (1 + math.exp(-log_odds))
        score = round(min(score, 0.995), 4)
        direct = any(o.fired and o.direct_evidence for o in outcomes)
        return score, band_for(score, direct), outcomes

    # -- persistence -----------------------------------------------------------

    async def correlate_investigation(
        self, session: AsyncSession, investigation_id: uuid.UUID
    ) -> list[MatchAssessment]:
        entities = list(
            (
                await session.execute(
                    select(Entity).where(Entity.investigation_id == investigation_id)
                )
            ).scalars()
        )
        candidates = [e for e in entities if e.type in CORRELATABLE]
        if len(candidates) < 2:
            return []

        identifiers = list(
            (
                await session.execute(
                    select(Identifier).where(Identifier.investigation_id == investigation_id)
                )
            ).scalars()
        )
        by_entity: dict[uuid.UUID, dict[str, set[str]]] = {}
        for row in identifiers:
            by_entity.setdefault(row.entity_id, {}).setdefault(row.kind, set()).add(row.normalized)

        views = {e.id: _view(e, by_entity.get(e.id, {})) for e in candidates}
        entity_by_id = {e.id: e for e in candidates}

        assessments: list[MatchAssessment] = []
        for left, right in combinations(candidates, 2):
            score, band, outcomes = self.assess(views[left.id], views[right.id])
            if score < self.min_score or not any(o.fired for o in outcomes):
                continue
            assessments.append(
                MatchAssessment(
                    entity_a=entity_by_id[left.id],
                    entity_b=entity_by_id[right.id],
                    score=score,
                    band=band,
                    outcomes=outcomes,
                )
            )

        await self._persist(session, investigation_id, assessments)
        return assessments

    async def _persist(
        self,
        session: AsyncSession,
        investigation_id: uuid.UUID,
        assessments: list[MatchAssessment],
    ) -> None:
        from app.graph.store import GraphStore

        graph = GraphStore(session, investigation_id)
        existing = {
            (row.entity_a_id, row.entity_b_id): row
            for row in (
                await session.execute(
                    select(IdentityCandidate).where(
                        IdentityCandidate.investigation_id == investigation_id
                    )
                )
            ).scalars()
        }

        for assessment in assessments:
            a_id, b_id = _ordered(assessment.entity_a.id, assessment.entity_b.id)
            row = existing.get((a_id, b_id))
            if row is None:
                row = IdentityCandidate(
                    id=uuid.uuid4(),
                    investigation_id=investigation_id,
                    entity_a_id=a_id,
                    entity_b_id=b_id,
                    score=assessment.score,
                    band=str(assessment.band),
                    reasons=assessment.reasons,
                    explanation=assessment.explanation,
                )
                session.add(row)
                await session.flush()
            else:
                row.score = assessment.score
                row.band = str(assessment.band)
                row.reasons = assessment.reasons
                row.explanation = assessment.explanation
                row.updated_at = utcnow()

            await graph.add_relationship(
                source=assessment.entity_a,
                target=assessment.entity_b,
                rel_type=_correlation_edge(assessment),
                confidence=assessment.score,
                provider="correlation",
                assertion=Assertion.CORRELATED,
                evidence={
                    "candidate_id": str(row.id),
                    "band": str(assessment.band),
                    "score": assessment.score,
                    "reasons": assessment.reasons,
                    "why": assessment.explanation,
                },
            )
        await session.flush()


def _view(entity: Entity, identifiers: dict[str, set[str]]) -> EntityView:
    return EntityView(
        id=str(entity.id),
        type=entity.type,
        label=entity.label,
        canonical_key=entity.canonical_key,
        attributes=dict(entity.attributes),
        identifiers=identifiers,
        sources=list(entity.sources),
    )


def _ordered(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if str(a) <= str(b) else (b, a)


def _correlation_edge(assessment: MatchAssessment) -> RelationshipType:
    fired = {o.name for o in assessment.outcomes if o.fired}
    if "same_email" in fired:
        return RelationshipType.SAME_EMAIL
    if "same_avatar" in fired:
        return RelationshipType.SAME_AVATAR
    if "same_username" in fired:
        return RelationshipType.SAME_USERNAME
    return RelationshipType.POSSIBLE_MATCH
