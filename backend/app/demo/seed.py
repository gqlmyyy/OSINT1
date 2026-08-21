"""Demo mode (spec 36): a complete, self-consistent investigation with no network access.

The dataset is synthetic. Every value below is invented for demonstration: the usernames,
the domain (from RFC 2606's reserved `example.com`), and the hashes. Nothing here is a
real person, and no request leaves the process.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Assertion, InvestigationStage, InvestigationStatus, MatchStrength
from app.correlation.engine import CorrelationEngine
from app.evidence.extractor import EntityExtractor
from app.models import Investigation
from app.providers.types import EdgeHint, Observation, Target
from app.schemas.investigation import TargetIn
from app.services.investigation import InvestigationService

DEMO_USERNAME = "example_user"
DEMO_EMAIL = "example.user@example.com"
DEMO_DOMAIN = "example.com"
AVATAR_HASH = "7f3c2b19d84a6e50f21c9b7e4a0d8c6355ae91b2c7d40f8e6a1b93c5d2074e8f"
BASE = datetime(2025, 3, 11, 9, 30, tzinfo=timezone.utc)


def _obs(
    provider: str,
    kind: str,
    value: str,
    *,
    days: int,
    url: str | None = None,
    label: str | None = None,
    data: dict[str, object] | None = None,
    excerpt: str = "",
    match: MatchStrength = MatchStrength.EXACT_ID,
    assertion: Assertion = Assertion.OBSERVED,
    edges: list[EdgeHint] | None = None,
    derived: list[tuple[str, str]] | None = None,
) -> Observation:
    return Observation(
        provider=provider,
        source=provider,
        kind=kind,
        value=value,
        url=url,
        label=label,
        observed_at=BASE + timedelta(days=days),
        match=match,
        assertion=assertion,
        confidence=None,
        data=data or {},
        raw={"demo": True, "provider": provider, "value": value, **(data or {})},
        excerpt=excerpt,
        edges=edges or [],
        derived_targets=derived or [],
    )


def demo_observations() -> list[Observation]:
    """The synthetic finding set, shaped exactly like real provider output."""
    return [
        _obs(
            "github",
            "social_account",
            DEMO_USERNAME,
            days=0,
            url=f"https://github.com/{DEMO_USERNAME}",
            label=f"GitHub/{DEMO_USERNAME}",
            data={
                "platform": "GitHub",
                "username": DEMO_USERNAME,
                "display_name": "Example User",
                "bio": "Backend engineer working on graph databases and data pipelines.",
                "website": f"https://{DEMO_DOMAIN}",
                "avatar_hash": AVATAR_HASH,
                "public_repos": 12,
                "followers": 87,
            },
            excerpt="Backend engineer working on graph databases and data pipelines. blog: example.com",
            edges=[
                EdgeHint(
                    type="LINKS_TO",
                    target_kind="website",
                    target_value=DEMO_DOMAIN,
                    why="The public GitHub profile declares this website.",
                    target_attributes={"platform": "web"},
                ),
                EdgeHint(
                    type="ASSOCIATED_WITH",
                    target_kind="avatar",
                    target_value=f"https://avatars.example.com/{AVATAR_HASH[:16]}.png",
                    why="Profile avatar image published on the account page.",
                    target_attributes={"sha256": AVATAR_HASH},
                ),
            ],
            derived=[("domain", DEMO_DOMAIN)],
        ),
        _obs(
            "github",
            "repository",
            f"{DEMO_USERNAME}/graph-pipeline",
            days=299,
            url=f"https://github.com/{DEMO_USERNAME}/graph-pipeline",
            data={
                "platform": "github",
                "full_name": f"{DEMO_USERNAME}/graph-pipeline",
                "language": "Python",
                "stars": 214,
                "homepage": f"https://{DEMO_DOMAIN}/projects/graph-pipeline",
            },
            excerpt="A streaming pipeline that materialises entity graphs.",
            edges=[
                EdgeHint(
                    type="LINKS_TO",
                    target_kind="url",
                    target_value=f"https://{DEMO_DOMAIN}/projects/graph-pipeline",
                    why="Repository homepage field points at this page.",
                )
            ],
        ),
        _obs(
            "username_enum",
            "social_account",
            DEMO_USERNAME,
            days=185,
            url=f"https://reddit.com/user/{DEMO_USERNAME}",
            label=f"Reddit/{DEMO_USERNAME}",
            data={
                "platform": "Reddit",
                "username": DEMO_USERNAME,
                "display_name": "Example User",
                "bio": "Backend engineer working on graph databases and data pipelines.",
                "avatar_hash": AVATAR_HASH,
            },
            excerpt="Profile page returned HTTP 200 with a matching handle.",
        ),
        _obs(
            "username_enum",
            "social_account",
            "example.user",
            days=190,
            url="https://news.ycombinator.com/user?id=example.user",
            label="HackerNews/example.user",
            data={"platform": "HackerNews", "username": "example.user", "display_name": "Example U."},
            match=MatchStrength.PATTERN_MATCH,
            assertion=Assertion.UNVERIFIED,
            excerpt="Handle is a variant of the target; no corroborating profile data found.",
        ),
        _obs(
            "website",
            "website",
            DEMO_DOMAIN,
            days=91,
            url=f"https://{DEMO_DOMAIN}",
            data={
                "title": "Example User — engineering notes",
                "generator": "Hugo 0.128",
                "emails": [DEMO_EMAIL],
                "display_name": "Example User",
            },
            excerpt="<title>Example User — engineering notes</title> … contact: example.user@example.com",
            edges=[
                EdgeHint(
                    type="MENTIONS",
                    target_kind="email",
                    target_value=DEMO_EMAIL,
                    why="Address published in the page's contact section.",
                ),
                EdgeHint(
                    type="LINKS_TO",
                    target_kind="social_account",
                    target_value=DEMO_USERNAME,
                    why="Site footer links to this profile.",
                    target_attributes={"platform": "GitHub", "username": DEMO_USERNAME},
                ),
                EdgeHint(
                    type="ASSOCIATED_WITH",
                    target_kind="technology",
                    target_value="Hugo",
                    why="Generator meta tag on the homepage.",
                ),
            ],
            derived=[("email", DEMO_EMAIL)],
        ),
        _obs(
            "username_enum",
            "social_account",
            DEMO_USERNAME,
            days=210,
            url=f"https://x.com/{DEMO_USERNAME}",
            label=f"X/{DEMO_USERNAME}",
            data={
                "platform": "X",
                "username": DEMO_USERNAME,
                "display_name": "Example User",
                "website": f"https://{DEMO_DOMAIN}",
            },
            match=MatchStrength.CLAIMED_LINK,
            excerpt="Public profile page resolved and declares the same website.",
        ),
        _obs(
            "github",
            "location",
            "Berlin, Germany",
            days=0,
            data={"raw": "Berlin, Germany"},
            match=MatchStrength.CLAIMED_LINK,
            excerpt="Location field on the public GitHub profile.",
        ),
        _obs(
            "github",
            "organization",
            "Example Labs",
            days=0,
            data={"source_field": "company"},
            match=MatchStrength.CLAIMED_LINK,
            excerpt="Company field on the public GitHub profile: @Example Labs",
        ),
        _obs(
            "website",
            "url",
            f"https://{DEMO_DOMAIN}/about",
            days=91,
            url=f"https://{DEMO_DOMAIN}/about",
            data={"title": "About — Example User"},
            excerpt="Linked from the site navigation.",
        ),
        _obs(
            "dns",
            "ip",
            "203.0.113.42",
            days=91,
            data={"record": "A", "ttl": 300},
            excerpt="example.com. 300 IN A 203.0.113.42",
        ),
        _obs(
            "whois",
            "organization",
            "Example Registrar LLC",
            days=91,
            url=f"https://rdap.example.net/domain/{DEMO_DOMAIN}",
            data={"registrar": "Example Registrar LLC", "created": "2019-04-02", "status": "active"},
            excerpt="Registrar: Example Registrar LLC · created 2019-04-02 · registrant redacted",
            assertion=Assertion.OBSERVED,
        ),
        _obs(
            "gravatar",
            "avatar",
            f"https://www.gravatar.com/avatar/{AVATAR_HASH[:32]}",
            days=200,
            data={"sha256": AVATAR_HASH, "source": "gravatar"},
            excerpt="Gravatar profile image exists for the hashed address.",
        ),
        _obs(
            "crtsh",
            "domain",
            f"blog.{DEMO_DOMAIN}",
            days=250,
            url=f"https://crt.sh/?q={DEMO_DOMAIN}",
            data={"issuer": "Example CA", "not_before": "2025-11-16"},
            excerpt="Certificate transparency log entry covering blog.example.com",
        ),
    ]


async def seed_demo_investigation(session: AsyncSession, owner_id: uuid.UUID) -> Investigation:
    """Build a fully populated investigation without touching the network."""
    service = InvestigationService(session)
    investigation = Investigation(
        id=uuid.uuid4(),
        name=f"Demo — {DEMO_USERNAME}",
        description=(
            "Synthetic offline investigation. Every entity, relationship and piece of "
            "evidence below is fabricated for demonstration and refers to no real person."
        ),
        owner_id=owner_id,
        tags=["demo", "offline"],
        status=InvestigationStatus.COMPLETED,
        stage=InvestigationStage.DONE,
        config={"demo": True},
    )
    session.add(investigation)
    await session.flush()

    await service.add_targets(
        investigation.id,
        [
            TargetIn(value=DEMO_USERNAME),
            TargetIn(value=DEMO_EMAIL),
            TargetIn(value=DEMO_DOMAIN),
        ],
    )

    extractor = EntityExtractor(session, investigation.id)
    username_target = Target(
        type="username", value=DEMO_USERNAME, normalized=DEMO_USERNAME, depth=0
    )
    domain_target = Target(type="domain", value=DEMO_DOMAIN, normalized=DEMO_DOMAIN, depth=1)

    observations = demo_observations()
    for observation in observations:
        observation.confidence = observation.scored(0.92)

    by_origin = {
        "dns": domain_target,
        "whois": domain_target,
        "crtsh": domain_target,
        "website": domain_target,
    }
    for observation in observations:
        origin = by_origin.get(observation.provider, username_target)
        await extractor.ingest(origin, [observation])

    await CorrelationEngine().correlate_investigation(session, investigation.id)
    await session.flush()
    return investigation
