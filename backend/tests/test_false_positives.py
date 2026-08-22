"""False-positive resistance: the scenarios that must NOT produce an identity match.

Every case here is a pair of accounts that a naive username-matching tool would link and
that this engine must refuse to link — plus the true positives that prove the refusal is
discrimination rather than blanket timidity. A model that rejects everything is not
conservative, it is useless; these two halves have to pass together.

The worked example from the brief:

    Username: johnsmith
    Found:    Instagram, GitHub, Reddit
    Result:   Possible Correlation
    Confidence: Low
    Reason:   Common username / no shared website / no shared email / no shared avatar
"""

from __future__ import annotations

import pytest

from app.core.enums import EntityType, MatchBand
from app.correlation.engine import CorrelationEngine, assess_false_positive_risk
from app.correlation.signals import EntityView

ENGINE = CorrelationEngine()


def account(entity_id: str, platform: str, **attributes: object) -> EntityView:
    identifiers = attributes.pop("identifiers", {})
    return EntityView(
        id=entity_id,
        type=EntityType.SOCIAL_ACCOUNT,
        label=f"{platform}/{entity_id}",
        canonical_key=f"{platform.lower()}:user:{entity_id}",
        attributes={"platform": platform, **attributes},
        identifiers=identifiers,  # type: ignore[arg-type]
        sources=[platform.lower()],
    )


def assess(a: EntityView, b: EntityView):
    score, band, outcomes = ENGINE.assess(a, b)
    return score, band, outcomes, assess_false_positive_risk(outcomes)


# -- the classic false positives ----------------------------------------------


@pytest.mark.parametrize("handle", ["johnsmith", "john", "mike", "admin", "info", "david99"])
def test_a_common_handle_alone_is_not_recorded_as_a_candidate(handle: str) -> None:
    """Three platforms, one common handle, nothing else: no candidate at all."""
    score, band, _, risk = assess(
        account("a", "Instagram", identifiers={"username": {handle}}),
        account("b", "GitHub", identifiers={"username": {handle}}),
    )
    assert score < ENGINE.min_score, f"{handle} would be stored as a candidate at {score}"
    assert band is MatchBand.POSSIBLE
    assert risk.level == "high"


def test_the_briefs_johnsmith_scenario_end_to_end() -> None:
    """The exact example from the specification, asserted line by line."""
    instagram = account("ig", "Instagram", identifiers={"username": {"johnsmith"}})
    github = account("gh", "GitHub", identifiers={"username": {"johnsmith"}})
    score, band, outcomes, risk = assess(instagram, github)

    assert band is MatchBand.POSSIBLE
    assert score < ENGINE.min_score
    assert risk.level == "high"

    reason_text = " ".join(risk.reasons).lower()
    assert "common one" in reason_text
    missing = " ".join(risk.missing).lower()
    assert "no shared artefact" in missing
    assert "no directly observed evidence" in missing

    username = next(o for o in outcomes if o.name == "same_username")
    assert "common handle" in username.reason


def test_a_shared_display_name_alone_is_not_a_match() -> None:
    """Two people called 'John Smith' are not one person."""
    score, _, _, risk = assess(
        account("a", "GitHub", display_name="John Smith", identifiers={"username": {"jsmith1"}}),
        account("b", "Reddit", display_name="John Smith", identifiers={"username": {"jsmith2"}}),
    )
    assert score < ENGINE.min_score
    assert risk.level in ("high", "medium")


def test_two_accounts_on_the_same_platform_are_penalised_even_with_a_rare_handle() -> None:
    """A person rarely holds two accounts on one platform; siblings and fans do."""
    score, _, _, _ = assess(
        account("a", "GitHub", identifiers={"username": {"xk7q_zephyr"}}),
        account("b", "GitHub", identifiers={"username": {"xk7q_zephyr"}}),
    )
    assert score < ENGINE.min_score


def test_a_common_handle_plus_one_soft_signal_still_is_not_probable() -> None:
    """Accumulating resemblance must not substitute for a shared artefact."""
    score, band, _, risk = assess(
        account("a", "GitHub", display_name="Mike", identifiers={"username": {"mike"}}),
        account("b", "Reddit", display_name="Mike", identifiers={"username": {"mike"}}),
    )
    assert band is MatchBand.POSSIBLE
    assert score < 0.70
    assert risk.level == "high"


def test_contradicting_evidence_is_surfaced_as_risk() -> None:
    score, _, _, risk = assess(
        account(
            "a", "GitHub",
            identifiers={"username": {"bluefox42"}},
            bio="Backend engineer working on distributed graph databases",
        ),
        account(
            "b", "Reddit",
            identifiers={"username": {"bluefox42"}},
            bio="Amateur baker sharing sourdough recipes and bread photos",
        ),
    )
    assert any("contradicting signal" in r for r in risk.reasons)
    assert score < 0.70


def test_a_shared_default_avatar_does_not_confirm_identity() -> None:
    """Perceptually identical images are a candidate, never a confirmation.

    Platform default avatars, stock photos and memes all hash alike. This is exactly
    why the perceptual signal is not direct evidence.
    """
    same_picture = "ffffffffffffffff"
    _, band, outcomes, _ = assess(
        account("a", "GitHub", avatar_phash=same_picture, identifiers={"username": {"user1"}}),
        account("b", "Reddit", avatar_phash=same_picture, identifiers={"username": {"user2"}}),
    )
    assert band is not MatchBand.CONFIRMED
    fired = {o.name for o in outcomes if o.fired}
    assert "same_image_candidate" in fired
    assert not any(o.fired and o.direct_evidence for o in outcomes)


def test_perceptual_avatar_match_alone_cannot_reach_confirmed() -> None:
    """Even stacked with soft signals, a picture match does not gate the top band."""
    shared = {
        "avatar_phash": "0f1e2d3c4b5a6978",
        "display_name": "Same Name",
        "bio": "The same biography text repeated on both accounts, long enough to count",
    }
    _, band, _, _ = assess(
        account("a", "GitHub", identifiers={"username": {"xk7q_zephyr"}}, **shared),
        account("b", "Reddit", identifiers={"username": {"xk7q_zephyr"}}, **shared),
    )
    assert band is not MatchBand.CONFIRMED


# -- the true positives that must survive -------------------------------------


def test_a_shared_email_still_confirms() -> None:
    """The discrimination check: real evidence still gets through."""
    score, band, _, risk = assess(
        account("a", "GitHub", identifiers={"username": {"jsmith"}, "email": {"j@example.com"}}),
        account("b", "Reddit", identifiers={"username": {"jsmith"}, "email": {"j@example.com"}}),
    )
    assert band is MatchBand.CONFIRMED
    assert score > 0.90
    assert risk.level in ("low", "medium")


def test_a_common_handle_with_two_shared_artefacts_is_still_strong() -> None:
    """Rarity discounts the handle; it does not veto the corroboration around it."""
    shared = {"website": "example.com", "avatar_hash": "deadbeef" * 8}
    score, band, _, risk = assess(
        account("a", "GitHub", identifiers={"username": {"johnsmith"}}, **shared),
        account("b", "Reddit", identifiers={"username": {"johnsmith"}}, **shared),
    )
    assert band in (MatchBand.STRONG, MatchBand.CONFIRMED)
    assert score > 0.85
    assert risk.level == "low"


def test_a_distinctive_handle_with_one_artefact_is_strong() -> None:
    score, band, _, _ = assess(
        account("a", "GitHub", website="example.com", identifiers={"username": {"xk7q_zephyr"}}),
        account("b", "Reddit", website="example.com", identifiers={"username": {"xk7q_zephyr"}}),
    )
    assert band in (MatchBand.STRONG, MatchBand.PROBABLE)
    assert score > 0.85


def test_a_shared_repository_still_carries_weight() -> None:
    score, _, _, _ = assess(
        account("a", "GitHub", identifiers={"username": {"mike"}, "repository": {"mike/proj"}}),
        account("b", "Reddit", identifiers={"username": {"mike"}, "repository": {"mike/proj"}}),
    )
    assert score > ENGINE.min_score, "a shared repository must survive a common handle"


# -- the risk model itself ----------------------------------------------------


def test_risk_is_low_only_with_direct_evidence_and_corroboration() -> None:
    _, _, _, risk = assess(
        account(
            "a", "GitHub",
            identifiers={"username": {"xk7q_zephyr"}, "email": {"x@y.com"}},
            website="example.com",
        ),
        account(
            "b", "Reddit",
            identifiers={"username": {"xk7q_zephyr"}, "email": {"x@y.com"}},
            website="example.com",
        ),
    )
    assert risk.level == "low"
    assert risk.missing == []


def test_risk_names_what_is_missing() -> None:
    _, _, _, risk = assess(
        account("a", "GitHub", identifiers={"username": {"graphintel"}}),
        account("b", "Reddit", identifiers={"username": {"graphintel"}}),
    )
    assert risk.missing, "a resemblance-only candidate must say what it lacks"
    assert any("shared artefact" in m for m in risk.missing)


def test_risk_with_no_signals_at_all_is_high() -> None:
    assert assess_false_positive_risk([]).level == "high"


def test_risk_is_serialisable_for_storage() -> None:
    _, _, _, risk = assess(
        account("a", "GitHub", identifiers={"username": {"johnsmith"}}),
        account("b", "Reddit", identifiers={"username": {"johnsmith"}}),
    )
    payload = risk.as_dict()
    assert set(payload) == {"level", "reasons", "missing"}
    assert isinstance(payload["reasons"], list)


async def test_risk_is_persisted_on_the_candidate_row(session, investigation) -> None:
    """The stored fact, not a re-derivation from prose at read time."""
    import uuid

    from sqlalchemy import select

    from app.models import Entity, Identifier, IdentityCandidate

    for index, platform in enumerate(("GitHub", "Reddit")):
        entity = Entity(
            id=uuid.uuid4(),
            investigation_id=investigation.id,
            type=EntityType.SOCIAL_ACCOUNT,
            label=f"{platform}/xk7q_zephyr",
            canonical_key=f"{platform.lower()}:user:xk7q_zephyr:{index}",
            confidence=0.9,
            attributes={"platform": platform, "website": "example.com"},
            sources=[platform.lower()],
        )
        session.add(entity)
        await session.flush()
        session.add(
            Identifier(
                id=uuid.uuid4(),
                entity_id=entity.id,
                investigation_id=investigation.id,
                kind="username",
                value="xk7q_zephyr",
                normalized="xk7q_zephyr",
            )
        )
    await session.commit()

    await ENGINE.correlate_investigation(session, investigation.id)
    await session.commit()

    candidate = (
        await session.execute(
            select(IdentityCandidate).where(
                IdentityCandidate.investigation_id == investigation.id
            )
        )
    ).scalars().first()
    assert candidate is not None, "a distinctive handle plus a shared website must correlate"
    assert candidate.risk.get("level") in ("low", "medium", "high")
    assert isinstance(candidate.risk.get("reasons"), list)
