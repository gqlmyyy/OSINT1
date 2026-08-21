"""Public interaction accounting.

Answers *"how often do these two accounts publicly interact?"* — a **count**, not a
probability, and deliberately kept away from identity resolution.

Two accounts interacting frequently is not evidence they are the same person; if
anything it is weak evidence they are not, since people rarely comment on themselves.
An OSINT tool that lets interaction volume leak into identity scoring produces confident
nonsense at scale, so the separation here is structural: this module writes only to
relationships, never to ``IdentityCandidate``, and a test enforces it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Assertion, EntityType, InteractionStrength, RelationshipType
from app.models import Entity, Relationship

#: Occurrence counts at which the label changes. Legible rather than clever: one
#: interaction is one interaction, and "frequent" should mean it.
THRESHOLDS: list[tuple[int, InteractionStrength]] = [
    (15, InteractionStrength.FREQUENT),
    (5, InteractionStrength.REGULAR),
    (2, InteractionStrength.OCCASIONAL),
    (1, InteractionStrength.SINGLE),
]

STRENGTH_LABELS: dict[InteractionStrength, str] = {
    InteractionStrength.SINGLE: "Single public interaction",
    InteractionStrength.OCCASIONAL: "Occasional public interaction",
    InteractionStrength.REGULAR: "Regular public interaction",
    InteractionStrength.FREQUENT: "Frequent public interaction",
}


def strength_for(total: int) -> InteractionStrength:
    for threshold, strength in THRESHOLDS:
        if total >= threshold:
            return strength
    return InteractionStrength.SINGLE


@dataclass
class InteractionSummary:
    actor_id: uuid.UUID
    target_id: uuid.UUID
    actor_label: str = ""
    target_label: str = ""
    comments: int = 0
    replies: int = 0
    mentions: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    evidence_ids: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.comments + self.replies + self.mentions

    @property
    def strength(self) -> InteractionStrength:
        return strength_for(self.total)

    def describe(self) -> str:
        parts = []
        if self.comments:
            parts.append(f"{self.comments} public comment{'s' if self.comments != 1 else ''}")
        if self.replies:
            parts.append(f"{self.replies} repl{'ies' if self.replies != 1 else 'y'}")
        if self.mentions:
            parts.append(f"{self.mentions} mention{'s' if self.mentions != 1 else ''}")
        detail = ", ".join(parts) or "no recorded interactions"
        return f"{STRENGTH_LABELS[self.strength]} — {detail}. Identity match not established."


class InteractionLedger:
    """Aggregates per-post interactions into an account-to-account picture."""

    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID) -> None:
        self.session = session
        self.investigation_id = investigation_id

    async def build(self) -> list[InteractionSummary]:
        entities = {
            e.id: e
            for e in (
                await self.session.execute(
                    select(Entity).where(Entity.investigation_id == self.investigation_id)
                )
            ).scalars()
        }
        edges = list(
            (
                await self.session.execute(
                    select(Relationship).where(
                        Relationship.investigation_id == self.investigation_id
                    )
                )
            ).scalars()
        )

        # Who wrote each post: a comment on a post is an interaction with its author.
        post_author: dict[uuid.UUID, uuid.UUID] = {
            edge.target_entity_id: edge.source_entity_id
            for edge in edges
            if edge.type == RelationshipType.AUTHORED
        }

        pairs: dict[tuple[uuid.UUID, uuid.UUID], InteractionSummary] = {}

        def bucket(actor: uuid.UUID, target: uuid.UUID) -> InteractionSummary | None:
            if actor == target or actor not in entities or target not in entities:
                return None
            # Only account-to-account interaction is meaningful here.
            if entities[actor].type != EntityType.SOCIAL_ACCOUNT:
                return None
            if entities[target].type != EntityType.SOCIAL_ACCOUNT:
                return None
            key = (actor, target)
            if key not in pairs:
                pairs[key] = InteractionSummary(
                    actor_id=actor,
                    target_id=target,
                    actor_label=entities[actor].label,
                    target_label=entities[target].label,
                )
            return pairs[key]

        for edge in edges:
            summary: InteractionSummary | None = None
            if edge.type in (RelationshipType.COMMENTED_ON, RelationshipType.REPLIED_TO):
                author = post_author.get(edge.target_entity_id)
                if author is not None:
                    summary = bucket(edge.source_entity_id, author)
                    if summary is not None:
                        occurrences = int(edge.evidence.get("occurrences", 1))
                        if edge.type == RelationshipType.COMMENTED_ON:
                            summary.comments += occurrences
                        else:
                            summary.replies += occurrences
            elif edge.type == RelationshipType.MENTIONS:
                summary = bucket(edge.source_entity_id, edge.target_entity_id)
                if summary is not None:
                    summary.mentions += int(edge.evidence.get("occurrences", 1))

            if summary is not None:
                observation_id = edge.evidence.get("observation_id")
                if observation_id and str(observation_id) not in summary.evidence_ids:
                    summary.evidence_ids.append(str(observation_id))
                seen = edge.created_at
                summary.first_seen = min(summary.first_seen or seen, seen)
                summary.last_seen = max(summary.last_seen or seen, seen)

        return sorted(pairs.values(), key=lambda s: -s.total)

    async def materialize(self) -> list[InteractionSummary]:
        """Record each summary as one explained edge, so the graph shows the picture."""
        from app.graph.store import GraphStore

        summaries = await self.build()
        store = GraphStore(self.session, self.investigation_id)

        for summary in summaries:
            actor = await self.session.get(Entity, summary.actor_id)
            target = await self.session.get(Entity, summary.target_id)
            if actor is None or target is None or not summary.evidence_ids:
                continue
            await store.add_relationship(
                source=actor,
                target=target,
                rel_type=RelationshipType.INTERACTS_WITH,
                # Confidence that the interaction *happened* — we counted it. Not, and
                # never to be read as, confidence about identity.
                confidence=0.95,
                provider="interaction-ledger",
                assertion=Assertion.OBSERVED,
                evidence={
                    "observation_id": summary.evidence_ids[0],
                    "supporting_observations": summary.evidence_ids[:20],
                    "occurrences": summary.total,
                    "comments": summary.comments,
                    "replies": summary.replies,
                    "mentions": summary.mentions,
                    "strength": str(summary.strength),
                    "identity_match": "not established",
                    "why": summary.describe(),
                },
            )
        await self.session.flush()
        return summaries
