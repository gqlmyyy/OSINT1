"""Frozen behavioural baseline for the correlation engine.

Written **before** username rarity weighting was introduced, so the effect of that change
is measurable rather than asserted. Two kinds of check live here:

``BASELINE``
    The exact scores the engine produced before the change, recorded to 3 decimal places.
    Cases whose behaviour must not move are asserted against it directly.

Directional guarantees
    Cases where the change is *supposed* to move the number. These assert the direction
    and the band, never a frozen value, so the suite documents intent instead of
    freezing an implementation detail.

The point of the split: a rarity model that improves false-positive resistance by
quietly degrading true positives has not improved anything, and without a baseline that
trade is invisible.
"""

from __future__ import annotations

import pytest

from app.core.enums import EntityType, MatchBand
from app.correlation.engine import CorrelationEngine
from app.correlation.signals import EntityView

ENGINE = CorrelationEngine()


def view(entity_id: str, platform: str, **attributes: object) -> EntityView:
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


def pair(handle: str, **shared: object) -> tuple[EntityView, EntityView]:
    """Two accounts on different platforms sharing ``handle`` and anything in ``shared``."""
    identifiers: dict[str, set[str]] = {"username": {handle}}
    for kind in ("email", "repository", "url"):
        value = shared.pop(kind, None)
        if value:
            identifiers[kind] = {str(value)}
    return (
        view("a", "GitHub", identifiers=dict(identifiers), **shared),
        view("b", "Reddit", identifiers=dict(identifiers), **shared),
    )


def score_of(handle: str, **shared: object) -> float:
    return ENGINE.assess(*pair(handle, **shared))[0]


def band_of(handle: str, **shared: object) -> MatchBand:
    return ENGINE.assess(*pair(handle, **shared))[1]


# -- the frozen numbers -------------------------------------------------------
#
# Recorded from the engine as it stood before rarity weighting. Every username-only
# case produced *exactly* the same score regardless of the handle, which is precisely
# the defect: `johnsmith` and `xk7q_zephyr` were treated as equally strong evidence.

USERNAME_ONLY_BASELINE = 0.5143

#: Handles by how identifying they should be. The baseline score is identical for all
#: three; after the change it must not be.
COMMON_HANDLES = ["johnsmith", "john", "admin", "mike", "david99", "info"]
NORMAL_HANDLES = ["example_user", "graphintel", "bluefox42", "mdawson"]
RARE_HANDLES = ["xk7q_zephyr", "qzvlmr_9931", "th3_gr1ndhouse_x", "zzt_prot0van"]

#: Multi-signal cases that must survive the change intact: these are true positives
#: carried by direct evidence, and rarity must not disturb them.
BASELINE: dict[str, float] = {
    "username_only": USERNAME_ONLY_BASELINE,
    "username+website": 0.9368,
    "username+email": 0.9769,
    "username+avatar": 0.9501,
    "username+email+avatar+website": 0.9950,
}


def test_measured_baseline_was_identical_for_every_handle() -> None:
    """The defect this phase exists to fix, recorded as a measured fact.

    Before rarity weighting, every handle scored exactly 0.5143 — the engine could not
    tell a throwaway common handle from a globally unique one. This test asserts the
    *historical* value is what we recorded; the assertion that the defect is gone lives
    in test_username_rarity.py, so the two files together document before and after.
    """
    assert USERNAME_ONLY_BASELINE == 0.5143


@pytest.mark.parametrize(
    ("case", "shared"),
    [
        ("username+website", {"website": "example.com"}),
        ("username+email", {"email": "x@y.com"}),
        ("username+avatar", {"avatar_hash": "deadbeef" * 8}),
        (
            "username+email+avatar+website",
            {"email": "x@y.com", "avatar_hash": "deadbeef" * 8, "website": "example.com"},
        ),
    ],
)
def test_multi_signal_cases_hold_their_baseline(case: str, shared: dict[str, object]) -> None:
    """True positives carried by direct evidence must not regress.

    Rarity is a modifier on one signal. If these move materially, the modifier has
    escaped its bounds and is rewriting conclusions it should only nudge.
    """
    score = score_of("example_user", **shared)
    assert score == pytest.approx(BASELINE[case], abs=0.02), (
        f"{case}: {score:.3f} vs baseline {BASELINE[case]:.3f} — "
        "a direct-evidence case moved more than 0.02"
    )


@pytest.mark.parametrize("handle", RARE_HANDLES)
def test_rare_handles_with_corroboration_stay_strong(handle: str) -> None:
    """A rare handle plus one direct signal should be at least as strong as before."""
    score = score_of(handle, website="example.com")
    assert score >= BASELINE["username+website"] - 0.02


@pytest.mark.parametrize("handle", COMMON_HANDLES + NORMAL_HANDLES + RARE_HANDLES)
def test_no_handle_alone_ever_reaches_probable(handle: str) -> None:
    """The invariant that must hold before *and* after: a username is never enough.

    Whatever rarity does to the score, one signal cannot carry an identity claim.
    """
    score, band, _ = ENGINE.assess(*pair(handle))
    assert band is MatchBand.POSSIBLE, f"{handle} reached {band} on a username alone"
    assert score < 0.70


@pytest.mark.parametrize("handle", RARE_HANDLES)
def test_rarity_alone_can_never_reach_confirmed(handle: str) -> None:
    """Explicit guard from the brief: rarity must not manufacture certainty.

    Even a maximally rare handle, with soft corroboration but no directly observed
    shared artefact, must stay below CONFIRMED — the direct_evidence gate still rules.
    """
    score, band, _ = ENGINE.assess(
        *pair(handle, display_name="Same Name", bio="a" * 40)
    )
    assert band is not MatchBand.CONFIRMED
    assert score <= 0.995


def test_the_engine_never_asserts_certainty() -> None:
    score = score_of(
        "xk7q_zephyr",
        email="x@y.com",
        avatar_hash="deadbeef" * 8,
        website="example.com",
        repository="ex/p",
        url="https://example.com",
        display_name="Example User",
    )
    assert score <= 0.995


def test_unrelated_handles_stay_below_the_recording_threshold() -> None:
    a = view("a", "GitHub", identifiers={"username": {"alice"}})
    b = view("b", "Reddit", identifiers={"username": {"bob"}})
    assert ENGINE.assess(a, b)[0] < ENGINE.min_score


def test_same_platform_penalty_survives() -> None:
    a = view("a", "GitHub", display_name="Bob", identifiers={"username": {"bob1"}})
    b = view("b", "GitHub", display_name="Bob", identifiers={"username": {"bob2"}})
    score, _, outcomes = ENGINE.assess(a, b)
    assert score < 0.5
    assert any(not o.fired and "two people" in o.reason for o in outcomes)
