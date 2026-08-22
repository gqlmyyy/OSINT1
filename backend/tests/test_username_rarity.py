"""Username rarity: the model itself, and the bounds it must respect.

Paired with ``test_correlation_baseline.py``: that file records what the engine did
before rarity existed, this one asserts what changed and — more importantly — what was
not allowed to change.
"""

from __future__ import annotations

import math

import pytest

from app.core.enums import MatchBand
from app.correlation.engine import PRIOR_ODDS, CorrelationEngine
from app.correlation.signals import SameUsernameSignal
from app.identity import rarity
from tests.test_correlation_baseline import (
    COMMON_HANDLES,
    RARE_HANDLES,
    USERNAME_ONLY_BASELINE,
    pair,
    score_of,
    view,
)

ENGINE = CorrelationEngine()


# -- the model ----------------------------------------------------------------


@pytest.mark.parametrize("handle", COMMON_HANDLES)
def test_common_handles_are_recognised(handle: str) -> None:
    assessment = rarity.assess(handle)
    assert assessment.is_common, f"{handle} scored {assessment.rarity} ({assessment.reason})"
    assert assessment.factor < 1.0


@pytest.mark.parametrize("handle", RARE_HANDLES)
def test_distinctive_handles_are_recognised(handle: str) -> None:
    assessment = rarity.assess(handle)
    assert assessment.rarity > 0.65, f"{handle}: {assessment.reason}"
    assert assessment.factor > 1.0


@pytest.mark.parametrize(
    ("handle", "expected_fragment"),
    [
        ("admin", "role or throwaway"),
        ("john", "given name"),
        ("mike", "short form"),
        ("smith", "surname"),
        ("johnsmith", "person's name"),
        ("dragon", "dictionary word"),
        ("david99", "given name"),
        ("bluefox", "combination of ordinary words"),
    ],
)
def test_every_score_states_its_reason(handle: str, expected_fragment: str) -> None:
    """A score a user cannot interrogate is a score they cannot disagree with."""
    assessment = rarity.assess(handle)
    assert expected_fragment in assessment.reason, assessment.reason


def test_leetspeak_does_not_disguise_a_common_name() -> None:
    """`d4v1d` is `david`; letting substitutions buy rarity would be trivially gamed."""
    assert rarity.assess("d4v1d").rarity < rarity.assess("qzvlmrx").rarity


def test_digits_appended_to_a_name_stay_common() -> None:
    assert rarity.assess("david99").is_common
    assert rarity.assess("john2024").is_common


def test_scoring_is_deterministic() -> None:
    assert rarity.assess("example_user").rarity == rarity.assess("example_user").rarity


def test_separators_are_ignored() -> None:
    """`john.smith`, `john_smith` and `johnsmith` are the same handle."""
    scores = {rarity.assess(h).rarity for h in ("john.smith", "john_smith", "john-smith", "johnsmith")}
    assert len(scores) == 1


def test_case_is_ignored() -> None:
    assert rarity.assess("JohnSmith").rarity == rarity.assess("johnsmith").rarity


@pytest.mark.parametrize("handle", ["", "   ", "@"])
def test_degenerate_handles_are_neutral_not_rare(handle: str) -> None:
    """An unparseable handle must not be rewarded as distinctive."""
    assessment = rarity.assess(handle)
    assert assessment.rarity == 0.5
    assert assessment.factor == 1.0


def test_unknown_handles_land_mid_scale_not_at_the_top() -> None:
    """Absence of evidence that a handle is common is not evidence that it is rare."""
    assessment = rarity.assess("qwertyuiopz")
    assert 0.5 <= assessment.rarity <= 0.95


# -- the bounds ---------------------------------------------------------------


@pytest.mark.parametrize(
    "handle",
    COMMON_HANDLES + RARE_HANDLES + ["", "a", "x" * 200, "!!!", "123456", "ünïcödé"],
)
def test_the_factor_is_always_bounded(handle: str) -> None:
    """The core guarantee: rarity is a bounded modifier, never a free variable."""
    factor = rarity.assess(handle).factor
    assert rarity.MIN_FACTOR <= factor <= rarity.MAX_FACTOR


def test_rarity_cannot_lift_a_username_alone_past_possible() -> None:
    """Proved from the constants, not spot-checked against examples.

    If a future weight change would let a bare username reach 'probable', this fails.
    """
    ceiling = rarity.max_username_only_score(
        SameUsernameSignal.positive_lr, PRIOR_ODDS
    )
    assert ceiling < 0.70, f"a username alone could reach {ceiling:.4f}"


@pytest.mark.parametrize("handle", RARE_HANDLES)
def test_even_the_rarest_handle_alone_stays_possible(handle: str) -> None:
    score, band, _ = ENGINE.assess(*pair(handle))
    assert band is MatchBand.POSSIBLE
    assert score < 0.70


@pytest.mark.parametrize("handle", RARE_HANDLES)
def test_rarity_alone_never_produces_confirmed(handle: str) -> None:
    """The direct-evidence gate is untouched by rarity.

    Maximum rarity plus every *soft* signal available still cannot reach CONFIRMED,
    because none of them is a directly observed shared artefact.
    """
    a, b = pair(handle, display_name="Same Person", bio="x" * 60)
    _, band, outcomes = ENGINE.assess(a, b)
    assert band is not MatchBand.CONFIRMED
    assert not any(o.fired and o.direct_evidence for o in outcomes)


def test_the_signal_still_reports_a_contradiction() -> None:
    """Rarity weighting must not have removed the negative branch."""
    a = view("a", "GitHub", identifiers={"username": {"alpha"}})
    b = view("b", "Reddit", identifiers={"username": {"beta"}})
    outcome = SameUsernameSignal().evaluate(a, b)
    assert outcome is not None and not outcome.fired


def test_no_shared_username_produces_no_outcome() -> None:
    """Rarity cannot invent a signal where there is no agreement."""
    a = view("a", "GitHub", identifiers={})
    b = view("b", "Reddit", identifiers={"username": {"anything"}})
    assert SameUsernameSignal().evaluate(a, b) is None


# -- the effect ---------------------------------------------------------------


@pytest.mark.parametrize("handle", COMMON_HANDLES)
def test_common_handles_now_score_below_the_old_flat_baseline(handle: str) -> None:
    assert score_of(handle) < USERNAME_ONLY_BASELINE


@pytest.mark.parametrize("handle", COMMON_HANDLES)
def test_common_handles_fall_below_the_recording_threshold(handle: str) -> None:
    """The practical payoff: these are no longer stored as candidates at all."""
    assert score_of(handle) < ENGINE.min_score


@pytest.mark.parametrize("handle", RARE_HANDLES)
def test_distinctive_handles_score_above_the_old_flat_baseline(handle: str) -> None:
    assert score_of(handle) > USERNAME_ONLY_BASELINE


def test_a_distinctive_handle_outranks_a_common_one() -> None:
    """The defect this phase fixed, stated directly."""
    assert score_of("xk7q_zephyr") > score_of("johnsmith")
    # And the gap is meaningful, not cosmetic.
    assert score_of("xk7q_zephyr") - score_of("johnsmith") > 0.25


def test_the_reason_reaches_the_correlation_output() -> None:
    """A user reading the match must see *why* the handle was discounted."""
    _, _, outcomes = ENGINE.assess(*pair("johnsmith"))
    username = next(o for o in outcomes if o.name == "same_username")
    assert "common handle" in username.reason
    assert "person's name" in username.reason


def test_the_bayesian_core_is_unchanged() -> None:
    """The engine still combines ratios in log space with the same prior and clamps."""
    a, b = pair("xk7q_zephyr", website="example.com")
    score, _, outcomes = ENGINE.assess(a, b)
    expected_log_odds = math.log(PRIOR_ODDS) + sum(o.log_odds for o in outcomes)
    expected = 1 / (1 + math.exp(-max(-12.0, min(12.0, expected_log_odds))))
    assert score == pytest.approx(round(min(expected, 0.995), 4), abs=1e-4)
