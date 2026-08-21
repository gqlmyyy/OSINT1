"""Identity resolution: calibration, bands, explanations, and the guarantees around them."""

from __future__ import annotations

from app.core.enums import EntityType, MatchBand
from app.correlation.engine import BAND_LABELS, CorrelationEngine, band_for
from app.correlation.signals import EntityView
from app.evidence.extractor import EntityExtractor
from app.graph.store import GraphStore
from app.providers.types import Observation, Target


def view(entity_id: str, platform: str, **attributes: object) -> EntityView:
    identifiers = attributes.pop("identifiers", {})
    return EntityView(
        id=entity_id,
        type=EntityType.SOCIAL_ACCOUNT,
        label=f"{platform}/{entity_id}",
        canonical_key=f"{platform.lower()}:user:{entity_id}",
        attributes={"platform": platform, **attributes},
        identifiers=identifiers,
        sources=[platform.lower()],
    )


ENGINE = CorrelationEngine()


def test_single_shared_username_is_only_a_possible_match() -> None:
    a = view("a", "GitHub", identifiers={"username": {"ex"}})
    b = view("b", "Reddit", identifiers={"username": {"ex"}})
    score, band, _ = ENGINE.assess(a, b)
    assert band is MatchBand.POSSIBLE
    assert score < 0.7, "one weak signal must never reach 'probable'"


def test_stacked_direct_evidence_reaches_the_top_band() -> None:
    shared = {
        "avatar_hash": "deadbeef" * 8,
        "website": "example.com",
        "display_name": "Example User",
        "bio": "Backend engineer working on graph databases and pipelines",
    }
    a = view("a", "GitHub", identifiers={"username": {"ex"}, "email": {"x@y.com"}}, **shared)
    b = view("b", "Reddit", identifiers={"username": {"ex"}, "email": {"x@y.com"}}, **shared)
    score, band, outcomes = ENGINE.assess(a, b)
    assert band is MatchBand.CONFIRMED
    assert score > 0.95
    assert any("same email" in o.reason for o in outcomes if o.fired)


def test_score_can_never_reach_certainty() -> None:
    shared = {"avatar_hash": "a" * 16, "website": "example.com", "display_name": "X"}
    a = view("a", "GitHub", identifiers={"username": {"ex"}, "email": {"x@y.com"}, "repository": {"ex/p"}, "url": {"https://example.com"}}, **shared)
    b = view("b", "Reddit", identifiers={"username": {"ex"}, "email": {"x@y.com"}, "repository": {"ex/p"}, "url": {"https://example.com"}}, **shared)
    score, _, _ = ENGINE.assess(a, b)
    assert score <= 0.995, "the engine must never assert 100% identity"


def test_confirmed_requires_direct_evidence_not_an_accumulation_of_heuristics() -> None:
    """A high score built only from soft signals is capped at 'strong'."""
    assert band_for(0.99, has_direct_evidence=False) is MatchBand.STRONG
    assert band_for(0.99, has_direct_evidence=True) is MatchBand.CONFIRMED


def test_two_accounts_on_the_same_platform_are_penalised() -> None:
    a = view("a", "GitHub", display_name="Bob", identifiers={"username": {"bob1"}})
    b = view("b", "GitHub", display_name="Bob", identifiers={"username": {"bob2"}})
    score, _, outcomes = ENGINE.assess(a, b)
    assert score < 0.5
    assert any(not o.fired and "two people" in o.reason for o in outcomes)


def test_unrelated_entities_score_below_the_recording_threshold() -> None:
    a = view("a", "GitHub", identifiers={"username": {"alice"}})
    b = view("b", "Reddit", identifiers={"username": {"bob"}})
    score, _, _ = ENGINE.assess(a, b)
    assert score < ENGINE.min_score


def test_every_band_has_a_human_label() -> None:
    assert set(BAND_LABELS) == set(MatchBand)
    assert BAND_LABELS[MatchBand.CONFIRMED] == "Confirmed by Evidence"
    assert "same person" not in " ".join(BAND_LABELS.values()).lower()


async def test_correlation_persists_candidates_with_reasons(session, investigation) -> None:
    extractor = EntityExtractor(session, investigation.id)
    target = Target(type="username", value="ex", normalized="ex", depth=0)
    shared = {
        "avatar_hash": "c0ffee" * 10,
        "website": "example.com",
        "display_name": "Example User",
    }
    await extractor.ingest(
        target,
        [
            Observation(
                provider="github", source="github", kind="social_account", value="ex",
                url="https://github.com/ex", confidence=0.95,
                data={"platform": "GitHub", "username": "ex", **shared},
            ),
            Observation(
                provider="username_enum", source="username_enum", kind="social_account",
                value="ex", url="https://reddit.com/user/ex", confidence=0.8,
                data={"platform": "Reddit", "username": "ex", **shared},
            ),
        ],
    )
    await session.flush()

    assessments = await CorrelationEngine().correlate_investigation(session, investigation.id)
    assert assessments, "two lookalike accounts should produce a candidate"
    top = max(assessments, key=lambda a: a.score)
    assert top.band in (MatchBand.PROBABLE, MatchBand.STRONG, MatchBand.CONFIRMED)
    assert any(reason.startswith("+") for reason in top.reasons)
    assert "not a verified identity" in top.explanation

    edges = await GraphStore(session, investigation.id).relationships()
    correlation_edges = [e for e in edges if e.assertion == "correlated"]
    assert correlation_edges, "a correlation must be recorded as an explained edge"
    assert correlation_edges[0].evidence["candidate_id"]
    assert correlation_edges[0].evidence["reasons"]


async def test_correlation_is_idempotent(session, investigation) -> None:
    extractor = EntityExtractor(session, investigation.id)
    target = Target(type="username", value="ex", normalized="ex", depth=0)
    data = {"avatar_hash": "beef" * 16, "website": "example.com"}
    await extractor.ingest(
        target,
        [
            Observation(provider="github", kind="social_account", value="ex", confidence=0.9,
                        data={"platform": "GitHub", "username": "ex", **data}),
            Observation(provider="e", kind="social_account", value="ex", confidence=0.9,
                        data={"platform": "Reddit", "username": "ex", **data}),
        ],
    )
    engine = CorrelationEngine()
    first = await engine.correlate_investigation(session, investigation.id)
    second = await engine.correlate_investigation(session, investigation.id)
    assert len(first) == len(second)

    from sqlalchemy import func, select

    from app.models import IdentityCandidate

    count = int(
        (await session.execute(
            select(func.count(IdentityCandidate.id)).where(
                IdentityCandidate.investigation_id == investigation.id
            )
        )).scalar_one()
    )
    assert count == len(first), "re-running correlation must not duplicate candidates"
