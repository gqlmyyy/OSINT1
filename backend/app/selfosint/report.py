"""Privacy exposure report: what a stranger could learn about you, and from where.

Two rules shape this module.

**Nothing is invented.** Every finding is built from rows that exist — entities,
observations, evidence — and carries the identifiers of the records it was derived from.
If a category has no supporting rows it produces no finding, and the UI shows the reason
(nothing found, versus not offered by the API) rather than an empty panel.

**Correlation stays conservative.** A shared username is *not* treated as evidence that
two accounts belong to the same person; it is reported as a correlation *risk* — "these
are easy to link together" — which is the honest claim and is also the one that matters
for a privacy audit. Actual identity candidates come from the existing log-odds
correlation engine, which already requires independent corroborating signals, and are
surfaced with their reasons attached.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Availability, EntityType, ExposureCategory, Severity
from app.models import Entity, IdentityCandidate, Observation

#: Points a finding contributes to its category, by severity. Fixed and published so a
#: score can always be explained by listing the findings behind it.
SEVERITY_POINTS: dict[str, int] = {
    Severity.HIGH: 40,
    Severity.MEDIUM: 20,
    Severity.LOW: 8,
    Severity.INFO: 0,
}

#: How much each category weighs in the overall score. Contact and identity exposure
#: carry the most because they are the hardest to undo once public.
CATEGORY_WEIGHT: dict[str, float] = {
    ExposureCategory.IDENTITY: 1.0,
    ExposureCategory.CONTACT: 1.0,
    ExposureCategory.USERNAME_REUSE: 0.9,
    ExposureCategory.EXTERNAL_LINKAGE: 0.9,
    ExposureCategory.LOCATION: 0.8,
    ExposureCategory.ORGANIZATION: 0.6,
    ExposureCategory.METADATA: 0.6,
    ExposureCategory.MEDIA: 0.5,
    ExposureCategory.EXTERNAL_MENTIONS: 0.5,
}

#: Providers whose findings are "you published this yourself on Instagram" as opposed to
#: "someone else's site carries it".
SELF_SOURCES = {"instagram_self"}


@dataclass
class EvidenceRef:
    """A pointer back to the record a finding came from."""

    label: str
    source: str
    url: str | None = None
    entity_id: str | None = None
    observation_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "source": self.source,
            "url": self.url,
            "entity_id": self.entity_id,
            "observation_id": self.observation_id,
        }


@dataclass
class Finding:
    category: str
    severity: str
    title: str
    description: str
    mitigation: str
    confidence: float
    evidence: list[EvidenceRef] = field(default_factory=list)
    availability: str = str(Availability.AVAILABLE)

    @property
    def points(self) -> int:
        return SEVERITY_POINTS.get(self.severity, 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "mitigation": self.mitigation,
            "confidence": round(self.confidence, 3),
            "availability": self.availability,
            "evidence": [e.as_dict() for e in self.evidence],
        }


@dataclass
class Correlation:
    """A link between the audited account and something else public."""

    signal: str
    source: str
    confidence: float
    explanation: str
    evidence: list[EvidenceRef] = field(default_factory=list)
    #: Whether this is an identity claim at all. Username-only links are not.
    identity_claim: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "source": self.source,
            "confidence": round(self.confidence, 3),
            "explanation": self.explanation,
            "identity_claim": self.identity_claim,
            "evidence": [e.as_dict() for e in self.evidence],
        }


class ExposureReportBuilder:
    """Builds the report for one self-audit investigation."""

    def __init__(self, session: AsyncSession, investigation_id: uuid.UUID) -> None:
        self.session = session
        self.investigation_id = investigation_id

    async def build(self, *, handle: str | None = None) -> dict[str, Any]:
        entities = await self._entities()
        observations = await self._observations()
        candidates = await self._candidates()

        self_account = _find_self_account(entities, handle)
        findings: list[Finding] = []
        findings.extend(self._identity_findings(self_account))
        findings.extend(self._username_reuse(entities, self_account))
        findings.extend(self._contact_findings(entities))
        findings.extend(self._location_findings(entities))
        findings.extend(self._organization_findings(entities))
        findings.extend(self._linkage_findings(entities, candidates))
        findings.extend(self._media_findings(entities, observations))
        findings.extend(self._metadata_findings(entities))
        findings.extend(self._mention_findings(entities, self_account))

        correlations = self._correlations(entities, candidates, self_account)
        score = self._score(findings)
        return {
            "findings": [f.as_dict() for f in findings],
            "correlations": [c.as_dict() for c in correlations],
            "score": score,
            "counts": {
                "entities": len(entities),
                "observations": len(observations),
                "findings": len(findings),
                "correlations": len(correlations),
            },
        }

    # -- data ------------------------------------------------------------------

    async def _entities(self) -> list[Entity]:
        return list(
            (
                await self.session.execute(
                    select(Entity).where(Entity.investigation_id == self.investigation_id)
                )
            ).scalars()
        )

    async def _observations(self) -> list[Observation]:
        return list(
            (
                await self.session.execute(
                    select(Observation).where(
                        Observation.investigation_id == self.investigation_id
                    )
                )
            ).scalars()
        )

    async def _candidates(self) -> list[IdentityCandidate]:
        return list(
            (
                await self.session.execute(
                    select(IdentityCandidate).where(
                        IdentityCandidate.investigation_id == self.investigation_id
                    )
                )
            ).scalars()
        )

    # -- findings --------------------------------------------------------------

    def _identity_findings(self, self_account: Entity | None) -> list[Finding]:
        if self_account is None:
            return []
        attributes = self_account.attributes
        exposed: list[EvidenceRef] = []
        for field_name, label in (
            ("display_name", "Display name"),
            ("bio", "Biography"),
            ("website", "Website"),
        ):
            value = attributes.get(field_name)
            if value:
                exposed.append(
                    EvidenceRef(
                        label=f"{label}: {str(value)[:120]}",
                        source="instagram_self",
                        url=attributes.get("url"),
                        entity_id=str(self_account.id),
                    )
                )
        if not exposed:
            return []
        has_real_name = bool(attributes.get("display_name"))
        return [
            Finding(
                category=str(ExposureCategory.IDENTITY),
                severity=str(Severity.MEDIUM if has_real_name else Severity.LOW),
                title="Your profile publishes identifying details",
                description=(
                    "These fields are visible to anyone who opens your profile and are "
                    "returned by Instagram's API to any app you authorise. A display "
                    "name that matches your real name is the single strongest link "
                    "between an online handle and an offline identity."
                ),
                mitigation=(
                    "Consider whether your display name needs to be your legal name, and "
                    "whether your biography needs to carry details that identify you "
                    "offline (employer, school, city, family)."
                ),
                confidence=0.95,
                evidence=exposed,
            )
        ]

    def _username_reuse(
        self, entities: list[Entity], self_account: Entity | None
    ) -> list[Finding]:
        """The example finding from the brief, built strictly from observed accounts."""
        if self_account is None:
            return []
        handle = str(self_account.attributes.get("username") or "").lower()
        if not handle:
            return []
        matches = [
            entity
            for entity in entities
            if entity.id != self_account.id
            and entity.type in (EntityType.SOCIAL_ACCOUNT, EntityType.USERNAME)
            and str(entity.attributes.get("username") or entity.label).lower().endswith(handle)
            and _same_handle(entity, handle)
        ]
        if not matches:
            return []
        evidence = [
            EvidenceRef(
                label=f"{_platform_of(entity)} @{handle}",
                source=", ".join(entity.sources) or "unknown",
                url=str(entity.attributes.get("url") or "") or None,
                entity_id=str(entity.id),
            )
            for entity in matches
        ]
        evidence.insert(
            0,
            EvidenceRef(
                label=f"Instagram @{handle}",
                source="instagram_self",
                url=str(self_account.attributes.get("url") or "") or None,
                entity_id=str(self_account.id),
            ),
        )
        platforms = len({_platform_of(e) for e in matches}) + 1
        severity = Severity.HIGH if platforms >= 3 else Severity.MEDIUM
        return [
            Finding(
                category=str(ExposureCategory.USERNAME_REUSE),
                severity=str(severity),
                title="Username reused across multiple platforms",
                description=(
                    f"The handle '{handle}' appears on {platforms} services that public "
                    "sources could see. Reusing one handle lets anyone assemble a "
                    "cross-platform picture of you from a single search, without needing "
                    "any private data. Note that a shared username on its own does not "
                    "prove the accounts belong to the same person — but it is enough to "
                    "make them worth checking, which is exactly the risk."
                ),
                mitigation=(
                    "Use a distinct handle on accounts you do not want linked to this "
                    "one, particularly anywhere you post under your real name."
                ),
                confidence=0.9,
                evidence=evidence,
            )
        ]

    def _contact_findings(self, entities: list[Entity]) -> list[Finding]:
        emails = [e for e in entities if e.type == EntityType.EMAIL]
        phones = [e for e in entities if e.type == EntityType.PHONE]
        if not emails and not phones:
            return []
        evidence = [
            EvidenceRef(
                label=entity.label,
                source=", ".join(entity.sources) or "unknown",
                url=str(entity.attributes.get("url") or "") or None,
                entity_id=str(entity.id),
            )
            for entity in [*emails, *phones]
        ]
        return [
            Finding(
                category=str(ExposureCategory.CONTACT),
                severity=str(Severity.HIGH),
                title="Contact details are publicly discoverable",
                description=(
                    "An email address or phone number tied to your identity was found in "
                    "public sources. Contact identifiers are the most valuable single "
                    "item for phishing, account-recovery attacks and cross-service "
                    "correlation, because they are stable and rarely changed. "
                    "Instagram's API does not disclose your registered email or phone; "
                    "anything listed here was published somewhere public."
                ),
                mitigation=(
                    "Remove the address from public profiles where it is not needed, or "
                    "move to a dedicated address for public contact. Enable two-factor "
                    "authentication on any account that uses it for recovery."
                ),
                confidence=0.85,
                evidence=evidence,
            )
        ]

    def _location_findings(self, entities: list[Entity]) -> list[Finding]:
        locations = [e for e in entities if e.type == EntityType.LOCATION]
        if not locations:
            return []
        return [
            Finding(
                category=str(ExposureCategory.LOCATION),
                severity=str(Severity.MEDIUM),
                title="Location information is publicly stated",
                description=(
                    "A place is associated with your public profile or posts. This came "
                    "from text you published, not from photo GPS data — Instagram's API "
                    "does not return media location. Combined with a real name it "
                    "narrows you down considerably."
                ),
                mitigation=(
                    "Remove specific places from your public biography if you do not "
                    "need them there; prefer a region over a city or address."
                ),
                confidence=0.7,
                evidence=[
                    EvidenceRef(
                        label=entity.label,
                        source=", ".join(entity.sources) or "unknown",
                        entity_id=str(entity.id),
                    )
                    for entity in locations
                ],
            )
        ]

    def _organization_findings(self, entities: list[Entity]) -> list[Finding]:
        orgs = [
            e
            for e in entities
            if e.type in (EntityType.ORGANIZATION, EntityType.COMPANY)
        ]
        if not orgs:
            return []
        return [
            Finding(
                category=str(ExposureCategory.ORGANIZATION),
                severity=str(Severity.LOW),
                title="An employer or organisation is publicly linked to you",
                description=(
                    "Public sources associate you with an organisation. This is often "
                    "intentional, but it also gives a social-engineering attacker the "
                    "context to impersonate a colleague convincingly."
                ),
                mitigation=(
                    "This is usually fine to keep. Be aware that it makes targeted "
                    "phishing referencing your workplace more credible."
                ),
                confidence=0.7,
                evidence=[
                    EvidenceRef(
                        label=entity.label,
                        source=", ".join(entity.sources) or "unknown",
                        entity_id=str(entity.id),
                    )
                    for entity in orgs
                ],
            )
        ]

    def _linkage_findings(
        self, entities: list[Entity], candidates: list[IdentityCandidate]
    ) -> list[Finding]:
        findings: list[Finding] = []
        websites = [
            e for e in entities if e.type in (EntityType.WEBSITE, EntityType.DOMAIN)
        ]
        if websites:
            findings.append(
                Finding(
                    category=str(ExposureCategory.EXTERNAL_LINKAGE),
                    severity=str(Severity.MEDIUM),
                    title="Your profile links to a website you control",
                    description=(
                        "A link in your profile connects this account to a domain. "
                        "Domain registration, certificate transparency logs and the "
                        "site's own contents are all public, so this link can expose "
                        "far more than the page itself shows."
                    ),
                    mitigation=(
                        "Check that the domain's WHOIS record uses privacy protection and "
                        "that the site does not publish contact details you would rather "
                        "keep off this profile."
                    ),
                    confidence=0.9,
                    evidence=[
                        EvidenceRef(
                            label=entity.label,
                            source=", ".join(entity.sources) or "unknown",
                            url=str(entity.attributes.get("url") or "") or None,
                            entity_id=str(entity.id),
                        )
                        for entity in websites
                    ],
                )
            )
        strong = [c for c in candidates if c.band in ("strong", "confirmed")]
        if strong:
            findings.append(
                Finding(
                    category=str(ExposureCategory.EXTERNAL_LINKAGE),
                    severity=str(Severity.HIGH),
                    title="Independent signals link this account to other profiles",
                    description=(
                        "More than one independent signal — not just a matching username "
                        "— connects your Instagram account to other public profiles. "
                        "This is the strongest form of exposure in this report, because "
                        "it survives changing any single identifier."
                    ),
                    mitigation=(
                        "Review the linked profiles below. Breaking these links usually "
                        "means changing the shared artefact (an avatar image, a website, "
                        "a contact address), not just the username."
                    ),
                    confidence=0.8,
                    evidence=[
                        EvidenceRef(
                            label=f"{c.band} match: {'; '.join(c.reasons[:3])}",
                            source="correlation engine",
                            entity_id=str(c.entity_a_id),
                        )
                        for c in strong
                    ],
                )
            )
        return findings

    def _media_findings(
        self, entities: list[Entity], observations: list[Observation]
    ) -> list[Finding]:
        posts = [e for e in entities if e.type == EntityType.POST]
        if not posts:
            return []
        return [
            Finding(
                category=str(ExposureCategory.MEDIA),
                severity=str(Severity.LOW),
                title=f"{len(posts)} public posts are readable through the API",
                description=(
                    "Your posts, their captions and their permalinks are retrievable. "
                    "Captions are searchable text: places, names, employers and dates "
                    "mentioned in them are all discoverable long after posting."
                ),
                mitigation=(
                    "Review older captions for details you would not publish today. "
                    "Switching the account to private stops public discovery, though it "
                    "does not retract what has already been indexed."
                ),
                confidence=0.95,
                evidence=[
                    EvidenceRef(
                        label=entity.label[:120],
                        source="instagram_self",
                        url=str(entity.attributes.get("url") or "") or None,
                        entity_id=str(entity.id),
                    )
                    for entity in posts[:10]
                ],
            )
        ]

    def _metadata_findings(self, entities: list[Entity]) -> list[Finding]:
        gps_images = [
            e
            for e in entities
            if e.type == EntityType.IMAGE and e.attributes.get("has_gps")
        ]
        if not gps_images:
            return []
        return [
            Finding(
                category=str(ExposureCategory.METADATA),
                severity=str(Severity.HIGH),
                title="An image carries embedded GPS coordinates",
                description=(
                    "An image reachable from your public identifiers still contains EXIF "
                    "location metadata. This records where the photo was taken, to within "
                    "metres, independently of anything written in the caption."
                ),
                mitigation=(
                    "Strip EXIF before publishing images, and disable location tagging in "
                    "your camera app. Most social platforms strip it on upload, so an "
                    "image that still has it is usually hosted somewhere that does not."
                ),
                confidence=0.97,
                evidence=[
                    EvidenceRef(
                        label=entity.label,
                        source=", ".join(entity.sources) or "image_geo",
                        url=str(entity.attributes.get("source_url") or "") or None,
                        entity_id=str(entity.id),
                    )
                    for entity in gps_images
                ],
            )
        ]

    def _mention_findings(
        self, entities: list[Entity], self_account: Entity | None
    ) -> list[Finding]:
        external = [
            e
            for e in entities
            if e.type == EntityType.SOCIAL_ACCOUNT
            and (self_account is None or e.id != self_account.id)
            and e.attributes.get("role") == "commenter"
        ]
        if not external:
            return []
        return [
            Finding(
                category=str(ExposureCategory.EXTERNAL_MENTIONS),
                severity=str(Severity.LOW),
                title=f"{len(external)} accounts interact publicly with your posts",
                description=(
                    "These accounts have commented on your public media. Public "
                    "interaction reveals a social graph around you that you do not "
                    "control: who engages with you, how often, and when. This is counted "
                    "activity only — it says nothing about who these people are to you."
                ),
                mitigation=(
                    "If a particular association is sensitive, consider limiting who can "
                    "comment on your posts in Instagram's privacy settings."
                ),
                confidence=0.9,
                evidence=[
                    EvidenceRef(
                        label=entity.label,
                        source="instagram_self",
                        url=str(entity.attributes.get("url") or "") or None,
                        entity_id=str(entity.id),
                    )
                    for entity in external[:15]
                ],
            )
        ]

    # -- correlations ----------------------------------------------------------

    def _correlations(
        self,
        entities: list[Entity],
        candidates: list[IdentityCandidate],
        self_account: Entity | None,
    ) -> list[Correlation]:
        correlations: list[Correlation] = []
        by_id = {entity.id: entity for entity in entities}

        for candidate in candidates:
            other = by_id.get(candidate.entity_b_id) or by_id.get(candidate.entity_a_id)
            supporting = [r for r in candidate.reasons if r.strip().startswith("+")]
            # Name the signal for what it actually is. Calling a one-signal candidate
            # "multi-signal" would overstate it in exactly the direction this whole
            # feature exists to avoid, and the explanation underneath would contradict
            # the label.
            signal = (
                "multi-signal identity correlation"
                if len(supporting) > 1
                else "single-signal identity correlation"
            )
            subject = other.label if other else "another profile"
            correlations.append(
                Correlation(
                    signal=f"{signal}: {subject}",
                    source="correlation engine (log-odds over independent signals)",
                    confidence=float(candidate.score),
                    explanation=(
                        candidate.explanation
                        or "; ".join(candidate.reasons)
                        or "Independent signals point at the same identity."
                    ),
                    identity_claim=candidate.band in ("strong", "confirmed"),
                    evidence=[
                        EvidenceRef(
                            label=reason,
                            source="correlation engine",
                            entity_id=str(other.id) if other else None,
                        )
                        for reason in candidate.reasons[:5]
                    ],
                )
            )

        if self_account is not None:
            handle = str(self_account.attributes.get("username") or "").lower()
            reused = [
                entity
                for entity in entities
                if entity.id != self_account.id
                and entity.type in (EntityType.SOCIAL_ACCOUNT, EntityType.USERNAME)
                and _same_handle(entity, handle)
            ]
            if reused:
                correlations.append(
                    Correlation(
                        signal="shared username",
                        source=", ".join(sorted({s for e in reused for s in e.sources})),
                        # Deliberately capped low: a username match is a lead, not proof,
                        # and inflating it here is exactly how OSINT tools manufacture
                        # false positives.
                        confidence=0.35,
                        explanation=(
                            f"The handle '{handle}' is used on "
                            f"{len({_platform_of(e) for e in reused}) + 1} services. This "
                            "is a correlation risk, not an identification: handles are "
                            "not unique and anyone can register the same one elsewhere. "
                            "Treat it as a lead to verify, never as proof of identity."
                        ),
                        identity_claim=False,
                        evidence=[
                            EvidenceRef(
                                label=f"{_platform_of(entity)} — {entity.label}",
                                source=", ".join(entity.sources) or "unknown",
                                url=str(entity.attributes.get("url") or "") or None,
                                entity_id=str(entity.id),
                            )
                            for entity in reused[:10]
                        ],
                    )
                )
        return correlations

    # -- score -----------------------------------------------------------------

    def _score(self, findings: list[Finding]) -> dict[str, Any]:
        """An explainable score: every point traces to a named finding.

        Category scores are capped at 100 and the overall figure is the weighted mean of
        the categories that actually produced findings — a category with nothing in it
        neither inflates nor deflates the result.
        """
        categories: dict[str, dict[str, Any]] = {}
        for finding in findings:
            bucket = categories.setdefault(
                finding.category,
                {"category": finding.category, "score": 0, "contributions": []},
            )
            bucket["score"] = min(100, bucket["score"] + finding.points)
            bucket["contributions"].append(
                {
                    "title": finding.title,
                    "severity": finding.severity,
                    "points": finding.points,
                    "evidence_count": len(finding.evidence),
                }
            )

        scored = list(categories.values())
        if not scored:
            return {
                "overall": 0,
                "level": "none",
                "categories": [],
                "explanation": (
                    "No exposure findings were produced. Either nothing was collected "
                    "yet, or no public source revealed anything about the identifiers "
                    "this account exposes."
                ),
            }

        weighted_sum = sum(
            entry["score"] * CATEGORY_WEIGHT.get(entry["category"], 0.5) for entry in scored
        )
        weight_total = sum(CATEGORY_WEIGHT.get(entry["category"], 0.5) for entry in scored)
        overall = round(weighted_sum / weight_total) if weight_total else 0
        return {
            "overall": overall,
            "level": _level(overall),
            "categories": sorted(scored, key=lambda e: -int(e["score"])),
            "explanation": (
                f"{len(findings)} finding(s) across {len(scored)} category(ies). Each "
                "finding contributes fixed points by severity (high 40, medium 20, low "
                "8, informational 0), capped at 100 per category; the overall figure is "
                "the weighted mean of the categories that produced findings. Every point "
                "above is attributable to a listed finding."
            ),
        }


def _level(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "moderate"
    if score > 0:
        return "low"
    return "none"


def _platform_of(entity: Entity) -> str:
    return str(entity.attributes.get("platform") or (entity.sources[0] if entity.sources else "?"))


def _same_handle(entity: Entity, handle: str) -> bool:
    if not handle:
        return False
    candidate = str(entity.attributes.get("username") or "").lower()
    if candidate:
        return candidate == handle
    return str(entity.label).lower() == handle


def _find_self_account(entities: list[Entity], handle: str | None) -> Entity | None:
    """The entity representing the authorised account itself."""
    wanted = (handle or "").lower()
    for entity in entities:
        if entity.type != EntityType.SOCIAL_ACCOUNT:
            continue
        attributes = entity.attributes
        if attributes.get("authorized_self"):
            return entity
        if (
            wanted
            and str(attributes.get("platform") or "").lower() == "instagram"
            and str(attributes.get("username") or "").lower() == wanted
        ):
            return entity
    return None
