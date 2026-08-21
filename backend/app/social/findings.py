"""Key findings: the few sentences worth reading first.

A finished investigation holds hundreds of rows. Showing them all is the same as showing
nothing, so this module answers "what did we actually learn?" in ranked, plain language —
and keeps the two kinds of claim visibly apart:

* **identity** findings say two accounts may be one person, with a band and reasons;
* **interaction** findings say two accounts publicly interact, with a count.

Every finding carries the entity and evidence ids behind it, so nothing here is a claim
the graph cannot back up.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import EntityType, InteractionStrength, MatchBand, RelationshipType
from app.correlation.engine import BAND_LABELS
from app.models import Entity, Identifier, IdentityCandidate, Relationship
from app.social.interactions import InteractionLedger

#: Findings weaker than this are noise on a summary screen; they remain in the graph.
MIN_INTERACTIONS_TO_REPORT = 3
MIN_PLATFORMS_TO_REPORT = 2


@dataclass
class Finding:
    kind: str  # identity | interaction | pivot | reach
    severity: str  # strong | probable | possible | informational
    headline: str
    detail: str
    entity_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "headline": self.headline,
            "detail": self.detail,
            "entity_ids": self.entity_ids,
            "evidence_ids": self.evidence_ids,
            "metrics": self.metrics,
        }


#: Ranking: an identity claim outranks activity, and stronger outranks weaker.
_SEVERITY_ORDER = {"strong": 0, "probable": 1, "possible": 2, "informational": 3}
_KIND_ORDER = {"identity": 0, "interaction": 1, "pivot": 2, "reach": 3}

_BAND_SEVERITY = {
    MatchBand.CONFIRMED: "strong",
    MatchBand.STRONG: "strong",
    MatchBand.PROBABLE: "probable",
    MatchBand.POSSIBLE: "possible",
}


class FindingsService:
    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID) -> None:
        self.session = session
        self.investigation_id = investigation_id

    async def build(self) -> dict[str, Any]:
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
        candidates = list(
            (
                await self.session.execute(
                    select(IdentityCandidate).where(
                        IdentityCandidate.investigation_id == self.investigation_id
                    )
                )
            ).scalars()
        )
        interactions = await InteractionLedger(self.session, self.investigation_id).build()

        findings: list[Finding] = []
        findings.extend(self._identity_findings(candidates, entities))
        findings.extend(self._interaction_findings(interactions))
        findings.extend(await self._cross_platform_findings(entities))
        findings.extend(self._shared_website_findings(entities))

        findings.sort(
            key=lambda f: (
                _KIND_ORDER.get(f.kind, 9),
                _SEVERITY_ORDER.get(f.severity, 9),
                -sum(f.metrics.values()) if f.metrics else 0,
            )
        )
        return {
            "summary": self._summary(entities, relationships, candidates, interactions),
            "findings": [f.as_dict() for f in findings],
        }

    # -- summary ---------------------------------------------------------------

    def _summary(
        self,
        entities: list[Entity],
        relationships: list[Relationship],
        candidates: list[IdentityCandidate],
        interactions: list[Any],
    ) -> dict[str, Any]:
        by_type: dict[str, int] = defaultdict(int)
        for entity in entities:
            by_type[entity.type] += 1

        sources: set[str] = set()
        for entity in entities:
            sources.update(entity.sources)
        sources.discard("target")

        strong = [c for c in candidates if c.band in (MatchBand.STRONG, MatchBand.CONFIRMED)]
        possible = [c for c in candidates if c.band in (MatchBand.POSSIBLE, MatchBand.PROBABLE)]

        return {
            "targets": by_type.get(EntityType.USERNAME, 0) + by_type.get(EntityType.EMAIL, 0),
            "accounts": by_type.get(EntityType.SOCIAL_ACCOUNT, 0),
            "posts": by_type.get(EntityType.POST, 0),
            "hashtags": by_type.get(EntityType.HASHTAG, 0),
            "domains": by_type.get(EntityType.DOMAIN, 0),
            "external_links": by_type.get(EntityType.URL, 0),
            "emails": by_type.get(EntityType.EMAIL, 0),
            "repositories": by_type.get(EntityType.REPOSITORY, 0),
            "entities": len(entities),
            "relationships": len(relationships),
            "public_interactions": sum(i.total for i in interactions),
            "interacting_accounts": len(interactions),
            "strong_correlations": len(strong),
            "possible_correlations": len(possible),
            "sources": len(sources),
        }

    # -- finding builders ------------------------------------------------------

    def _identity_findings(
        self, candidates: list[IdentityCandidate], entities: list[Entity]
    ) -> list[Finding]:
        labels = {e.id: e.label for e in entities}
        findings: list[Finding] = []
        for candidate in sorted(candidates, key=lambda c: -c.score):
            band = MatchBand(candidate.band)
            if band is MatchBand.POSSIBLE and candidate.score < 0.6:
                continue  # too weak to lead with; still visible in the matches list
            positives = [r for r in candidate.reasons if r.startswith("+")]
            findings.append(
                Finding(
                    kind="identity",
                    severity=_BAND_SEVERITY.get(band, "possible"),
                    headline=(
                        f"{BAND_LABELS[band]}: {labels.get(candidate.entity_a_id, '?')} "
                        f"and {labels.get(candidate.entity_b_id, '?')}"
                    ),
                    detail=(
                        f"{len(positives)} supporting signal(s): "
                        + "; ".join(r.lstrip('+ ') for r in positives[:4])
                        + ". This is a correlation, not a verified identity."
                    ),
                    entity_ids=[str(candidate.entity_a_id), str(candidate.entity_b_id)],
                    metrics={"confidence_percent": round(candidate.score * 100)},
                )
            )
        return findings[:8]

    def _interaction_findings(self, interactions: list[Any]) -> list[Finding]:
        findings: list[Finding] = []
        for summary in interactions:
            if summary.total < MIN_INTERACTIONS_TO_REPORT:
                continue
            severity = (
                "probable"
                if summary.strength
                in (InteractionStrength.FREQUENT, InteractionStrength.REGULAR)
                else "informational"
            )
            findings.append(
                Finding(
                    kind="interaction",
                    severity=severity,
                    headline=(
                        f"{summary.actor_label} interacts publicly with {summary.target_label}"
                    ),
                    # The disclaimer is part of the finding, not a footnote elsewhere.
                    detail=summary.describe(),
                    entity_ids=[str(summary.actor_id), str(summary.target_id)],
                    evidence_ids=summary.evidence_ids[:5],
                    metrics={
                        "interactions": summary.total,
                        "comments": summary.comments,
                        "mentions": summary.mentions,
                    },
                )
            )
        return findings[:8]

    async def _cross_platform_findings(self, entities: list[Entity]) -> list[Finding]:
        """The same handle seen on several platforms — a lead, explicitly not a match."""
        identifiers = list(
            (
                await self.session.execute(
                    select(Identifier).where(
                        Identifier.investigation_id == self.investigation_id,
                        Identifier.kind == "username",
                    )
                )
            ).scalars()
        )
        by_entity = {e.id: e for e in entities}
        platforms: dict[str, set[str]] = defaultdict(set)
        members: dict[str, list[str]] = defaultdict(list)

        for row in identifiers:
            entity = by_entity.get(row.entity_id)
            if entity is None or entity.type != EntityType.SOCIAL_ACCOUNT:
                continue
            platform = str(entity.attributes.get("platform") or "unknown")
            platforms[row.normalized].add(platform)
            members[row.normalized].append(str(entity.id))

        findings: list[Finding] = []
        for handle, seen in sorted(platforms.items(), key=lambda kv: -len(kv[1])):
            if len(seen) < MIN_PLATFORMS_TO_REPORT:
                continue
            findings.append(
                Finding(
                    kind="pivot",
                    severity="possible",
                    headline=f"Handle '{handle}' appears on {len(seen)} platforms",
                    detail=(
                        f"Seen on {', '.join(sorted(seen))}. A shared handle is a lead, not "
                        "proof of a shared owner — corroborate before relying on it."
                    ),
                    entity_ids=members[handle][:10],
                    metrics={"platforms": len(seen)},
                )
            )
        return findings[:5]

    def _shared_website_findings(self, entities: list[Entity]) -> list[Finding]:
        """Independent profiles declaring the same site — a genuinely strong signal."""
        from app.evidence.normalizer import NormalizationError, normalize_domain

        by_site: dict[str, list[Entity]] = defaultdict(list)
        for entity in entities:
            if entity.type != EntityType.SOCIAL_ACCOUNT:
                continue
            raw = entity.attributes.get("website") or entity.attributes.get("blog")
            if not isinstance(raw, str) or not raw.strip():
                continue
            try:
                by_site[normalize_domain(raw)].append(entity)
            except NormalizationError:
                continue

        findings: list[Finding] = []
        for site, accounts in sorted(by_site.items(), key=lambda kv: -len(kv[1])):
            if len(accounts) < 2:
                continue
            findings.append(
                Finding(
                    kind="pivot",
                    severity="strong",
                    headline=f"{len(accounts)} public profiles declare the same website",
                    detail=(
                        f"{site} is declared by "
                        + ", ".join(a.label for a in accounts[:5])
                        + ". A self-declared shared site is among the stronger links "
                        "available from public data."
                    ),
                    entity_ids=[str(a.id) for a in accounts[:10]],
                    metrics={"profiles": len(accounts)},
                )
            )
        return findings[:5]


__all__ = ["Finding", "FindingsService", "RelationshipType"]
