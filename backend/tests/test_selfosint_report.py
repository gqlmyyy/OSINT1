"""Exposure report: findings come from real rows, and correlation stays conservative.

The failure mode this guards against is the one the brief names explicitly — a tool that
sees the same username on three sites and announces it has identified a person. Here that
must surface as a *correlation risk* with a low confidence, never as an identity claim.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.enums import EntityType, ExposureCategory, Severity
from app.selfosint.report import ExposureReportBuilder


async def make_entity(
    session: Any,
    investigation_id: uuid.UUID,
    *,
    type_: str,
    label: str,
    attributes: dict[str, Any] | None = None,
    sources: list[str] | None = None,
) -> Any:
    from app.models import Entity

    entity = Entity(
        id=uuid.uuid4(),
        investigation_id=investigation_id,
        type=type_,
        label=label,
        canonical_key=f"{type_}:{label}".lower(),
        confidence=0.9,
        attributes=attributes or {},
        sources=sources or ["test"],
    )
    session.add(entity)
    await session.flush()
    return entity


@pytest.fixture
async def audit(session: Any, user: Any) -> Any:
    """A self-audit investigation with the authorised Instagram account in it."""
    from app.models import Investigation

    investigation = Investigation(
        id=uuid.uuid4(),
        name="Self-audit: Instagram",
        owner_id=user.id,
        tags=["self-osint"],
        config={"self_osint": True},
    )
    session.add(investigation)
    await session.flush()
    await make_entity(
        session,
        investigation.id,
        type_=EntityType.SOCIAL_ACCOUNT,
        label="Instagram/example_user",
        attributes={
            "platform": "Instagram",
            "username": "example_user",
            "display_name": "Example User",
            "bio": "Coffee and code",
            "website": "https://example.com",
            "url": "https://www.instagram.com/example_user/",
            "authorized_self": True,
        },
        sources=["instagram_self"],
    )
    await session.commit()
    return investigation


async def build(session: Any, investigation: Any) -> dict[str, Any]:
    return await ExposureReportBuilder(session, investigation.id).build(handle="example_user")


# -- findings -----------------------------------------------------------------


async def test_identity_finding_is_built_from_the_profile(session: Any, audit: Any) -> None:
    report = await build(session, audit)
    identity = [
        f for f in report["findings"] if f["category"] == str(ExposureCategory.IDENTITY)
    ]
    assert len(identity) == 1
    assert identity[0]["evidence"], "a finding without evidence is an assertion"
    labels = " ".join(e["label"] for e in identity[0]["evidence"])
    assert "Example User" in labels
    assert identity[0]["mitigation"]


async def test_username_reuse_across_three_platforms_is_high_severity(
    session: Any, audit: Any
) -> None:
    """The brief's worked example, end to end."""
    for platform, source in (("GitHub", "github"), ("Reddit", "username_enum")):
        await make_entity(
            session,
            audit.id,
            type_=EntityType.SOCIAL_ACCOUNT,
            label=f"{platform}/example_user",
            attributes={"platform": platform, "username": "example_user"},
            sources=[source],
        )
    await session.commit()

    report = await build(session, audit)
    reuse = next(
        f for f in report["findings"] if f["category"] == str(ExposureCategory.USERNAME_REUSE)
    )
    assert reuse["severity"] == str(Severity.HIGH)
    assert reuse["title"] == "Username reused across multiple platforms"
    sources = {e["source"] for e in reuse["evidence"]}
    assert "instagram_self" in sources
    assert len(reuse["evidence"]) == 3
    # The honesty clause must be in the text the user reads.
    assert "does not prove" in reuse["description"]


async def test_a_single_other_platform_is_not_high_severity(
    session: Any, audit: Any
) -> None:
    await make_entity(
        session,
        audit.id,
        type_=EntityType.SOCIAL_ACCOUNT,
        label="GitHub/example_user",
        attributes={"platform": "GitHub", "username": "example_user"},
        sources=["github"],
    )
    await session.commit()

    report = await build(session, audit)
    reuse = next(
        f for f in report["findings"] if f["category"] == str(ExposureCategory.USERNAME_REUSE)
    )
    assert reuse["severity"] == str(Severity.MEDIUM)


async def test_a_different_username_does_not_produce_a_reuse_finding(
    session: Any, audit: Any
) -> None:
    """Guards against matching on substrings or on the wrong field."""
    await make_entity(
        session,
        audit.id,
        type_=EntityType.SOCIAL_ACCOUNT,
        label="GitHub/example_user_2",
        attributes={"platform": "GitHub", "username": "example_user_2"},
        sources=["github"],
    )
    await session.commit()

    report = await build(session, audit)
    assert not [
        f for f in report["findings"] if f["category"] == str(ExposureCategory.USERNAME_REUSE)
    ]


async def test_contact_exposure_is_high_and_names_its_source(
    session: Any, audit: Any
) -> None:
    await make_entity(
        session,
        audit.id,
        type_=EntityType.EMAIL,
        label="hi@example.com",
        sources=["website"],
    )
    await session.commit()

    report = await build(session, audit)
    contact = next(
        f for f in report["findings"] if f["category"] == str(ExposureCategory.CONTACT)
    )
    assert contact["severity"] == str(Severity.HIGH)
    # Must state that Instagram's API did not supply this.
    assert "does not disclose" in contact["description"]
    assert contact["evidence"][0]["source"] == "website"


async def test_gps_metadata_finding(session: Any, audit: Any) -> None:
    await make_entity(
        session,
        audit.id,
        type_=EntityType.IMAGE,
        label="avatar.jpg",
        attributes={"has_gps": True, "source_url": "https://example.com/a.jpg"},
        sources=["image_geo"],
    )
    await session.commit()

    report = await build(session, audit)
    metadata = next(
        f for f in report["findings"] if f["category"] == str(ExposureCategory.METADATA)
    )
    assert metadata["severity"] == str(Severity.HIGH)
    assert "EXIF" in metadata["description"]


async def test_no_findings_when_nothing_was_collected(session: Any, user: Any) -> None:
    """An empty audit reports zero, not a fabricated baseline."""
    from app.models import Investigation

    empty = Investigation(
        id=uuid.uuid4(), name="empty", owner_id=user.id, tags=[], config={}
    )
    session.add(empty)
    await session.commit()

    report = await ExposureReportBuilder(session, empty.id).build(handle="nobody")
    assert report["findings"] == []
    assert report["score"]["overall"] == 0
    assert report["score"]["level"] == "none"


# -- correlation ---------------------------------------------------------------


async def test_shared_username_is_not_an_identity_claim(session: Any, audit: Any) -> None:
    """The central false-positive guard."""
    for platform in ("GitHub", "Reddit"):
        await make_entity(
            session,
            audit.id,
            type_=EntityType.SOCIAL_ACCOUNT,
            label=f"{platform}/example_user",
            attributes={"platform": platform, "username": "example_user"},
            sources=[platform.lower()],
        )
    await session.commit()

    report = await build(session, audit)
    username_correlations = [
        c for c in report["correlations"] if c["signal"] == "shared username"
    ]
    assert len(username_correlations) == 1
    correlation = username_correlations[0]
    assert correlation["identity_claim"] is False
    assert correlation["confidence"] <= 0.4, "a username match must stay a weak signal"
    assert "not an identification" in correlation["explanation"]
    assert "never as proof" in correlation["explanation"]


async def test_multi_signal_candidates_are_reported_with_their_reasons(
    session: Any, audit: Any
) -> None:
    from app.models import IdentityCandidate

    other = await make_entity(
        session,
        audit.id,
        type_=EntityType.SOCIAL_ACCOUNT,
        label="GitHub/example_user",
        attributes={"platform": "GitHub", "username": "example_user"},
        sources=["github"],
    )
    entities = (await build(session, audit), other)
    self_entity_id = uuid.uuid4()
    session.add(
        IdentityCandidate(
            id=uuid.uuid4(),
            investigation_id=audit.id,
            entity_a_id=self_entity_id,
            entity_b_id=other.id,
            score=0.87,
            band="strong",
            reasons=["+ same avatar image hash", "+ same declared website"],
            explanation="Two independent shared artefacts.",
        )
    )
    await session.commit()
    assert entities

    report = await build(session, audit)
    correlation = next(
        c for c in report["correlations"] if c["signal"].startswith("multi-signal")
    )
    assert correlation["identity_claim"] is True
    assert correlation["confidence"] == pytest.approx(0.87)
    assert len(correlation["evidence"]) == 2
    # The signal names the profile it links to, so several candidates are distinguishable.
    assert "GitHub/example_user" in correlation["signal"]


async def test_a_one_signal_candidate_is_not_called_multi_signal(
    session: Any, audit: Any
) -> None:
    """The label must not overstate what the explanation underneath admits."""
    from app.models import IdentityCandidate

    other = await make_entity(
        session,
        audit.id,
        type_=EntityType.SOCIAL_ACCOUNT,
        label="GitHub/example_user",
        attributes={"platform": "GitHub", "username": "example_user"},
        sources=["github"],
    )
    session.add(
        IdentityCandidate(
            id=uuid.uuid4(),
            investigation_id=audit.id,
            entity_a_id=uuid.uuid4(),
            entity_b_id=other.id,
            score=0.51,
            band="possible",
            reasons=["+ same username: example_user"],
            explanation="1 supporting and 0 contradicting signal(s).",
        )
    )
    await session.commit()

    report = await build(session, audit)
    candidate = next(
        c for c in report["correlations"] if "identity correlation" in c["signal"]
    )
    assert candidate["signal"].startswith("single-signal")
    assert candidate["identity_claim"] is False


# -- scoring -------------------------------------------------------------------


async def test_the_score_is_traceable_to_individual_findings(
    session: Any, audit: Any
) -> None:
    await make_entity(
        session, audit.id, type_=EntityType.EMAIL, label="hi@example.com", sources=["website"]
    )
    await session.commit()

    report = await build(session, audit)
    score = report["score"]
    assert 0 < score["overall"] <= 100
    assert score["categories"], "a score with no categories cannot be explained"

    titles = {f["title"] for f in report["findings"]}
    for category in score["categories"]:
        assert category["contributions"], f"{category['category']} has unexplained points"
        for contribution in category["contributions"]:
            assert contribution["title"] in titles
            assert contribution["points"] >= 0
        # Category total equals the sum of its contributions (capped at 100).
        assert category["score"] == min(
            100, sum(c["points"] for c in category["contributions"])
        )


async def test_the_score_is_deterministic(session: Any, audit: Any) -> None:
    """No randomness anywhere: the same data must always give the same number."""
    first = await build(session, audit)
    second = await build(session, audit)
    assert first["score"]["overall"] == second["score"]["overall"]


async def test_more_severe_findings_raise_the_score(session: Any, audit: Any) -> None:
    before = (await build(session, audit))["score"]["overall"]
    await make_entity(
        session, audit.id, type_=EntityType.EMAIL, label="hi@example.com", sources=["website"]
    )
    await session.commit()
    after = (await build(session, audit))["score"]["overall"]
    assert after > before


async def test_the_score_explanation_states_the_method(session: Any, audit: Any) -> None:
    report = await build(session, audit)
    explanation = report["score"]["explanation"]
    assert "high 40" in explanation and "medium 20" in explanation
    assert "attributable to a listed finding" in explanation


async def test_every_finding_carries_the_required_fields(session: Any, audit: Any) -> None:
    await make_entity(
        session, audit.id, type_=EntityType.EMAIL, label="hi@example.com", sources=["website"]
    )
    await session.commit()

    report = await build(session, audit)
    assert report["findings"]
    for finding in report["findings"]:
        for field in (
            "severity",
            "title",
            "description",
            "evidence",
            "confidence",
            "mitigation",
            "category",
        ):
            assert finding[field] not in (None, ""), f"{finding['title']} is missing {field}"
        assert 0.0 < finding["confidence"] <= 1.0
        for evidence in finding["evidence"]:
            assert evidence["label"] and evidence["source"]
