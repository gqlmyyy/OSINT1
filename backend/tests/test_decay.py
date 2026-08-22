"""Confidence decay: the stored number never changes; only what is displayed does."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.evidence.decay import (
    DEFAULT_HALF_LIFE_DAYS,
    MIN_DECAYED_CONFIDENCE,
    apply_decay,
    decay_factor,
    half_life_for_sources,
)


def test_no_decay_at_zero_age() -> None:
    result = apply_decay(0.9, datetime.now(tz=UTC), "dns")
    assert result.display_confidence == 0.9
    assert result.is_stale is False


def test_decays_toward_half_at_the_half_life() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    observed = now - timedelta(days=60)  # mastodon half-life is 60 days
    result = apply_decay(0.8, observed, "mastodon", now=now)
    assert result.display_confidence == pytest.approx(0.4, abs=0.01)
    assert result.is_stale is True


def test_slow_decaying_sources_stay_fresh_longer() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    observed = now - timedelta(days=60)
    dns_result = apply_decay(0.8, observed, "dns", now=now)  # 720-day half-life
    social_result = apply_decay(0.8, observed, "mastodon", now=now)  # 60-day half-life
    assert dns_result.display_confidence > social_result.display_confidence


def test_never_decays_below_the_floor() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    observed = now - timedelta(days=365 * 20)
    result = apply_decay(0.9, observed, "mastodon", now=now)
    assert result.display_confidence == MIN_DECAYED_CONFIDENCE


def test_unknown_provider_uses_the_default_half_life() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    observed = now - timedelta(days=DEFAULT_HALF_LIFE_DAYS)
    result = apply_decay(0.8, observed, "some_future_provider_not_yet_calibrated", now=now)
    assert result.display_confidence == pytest.approx(0.4, abs=0.01)


def test_naive_datetimes_are_treated_as_utc() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    naive_observed = datetime(2025, 11, 2)  # 60 days earlier, no tzinfo
    result = apply_decay(0.8, naive_observed, "mastodon", now=now)
    assert result.display_confidence == pytest.approx(0.4, abs=0.01)


def test_stale_note_is_present_only_when_stale() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fresh = apply_decay(0.8, now - timedelta(days=1), "mastodon", now=now)
    stale = apply_decay(0.8, now - timedelta(days=400), "mastodon", now=now)
    assert fresh.note is None
    assert stale.note is not None and "reduced confidence" in stale.note


def test_half_life_for_sources_is_the_most_conservative() -> None:
    """An entity corroborated by a fast- and a slow-decaying source is treated as
    fast-decaying — one stale contributor is enough to flag it for re-verification."""
    assert half_life_for_sources(["dns", "mastodon"]) == 60
    assert half_life_for_sources(["dns", "whois"]) == 720
    assert half_life_for_sources([]) == DEFAULT_HALF_LIFE_DAYS
    assert half_life_for_sources(["target"]) == DEFAULT_HALF_LIFE_DAYS


@pytest.mark.parametrize("half_life", [0, -5])
def test_zero_or_negative_half_life_does_not_crash(half_life: int) -> None:
    assert decay_factor(100, half_life) == 1.0


def test_zero_age_never_decays() -> None:
    assert decay_factor(0, 365) == 1.0
    assert decay_factor(-5, 365) == 1.0


async def test_projection_exposes_decay_on_every_node(session, user) -> None:
    from app.demo.seed import seed_demo_investigation
    from app.graph.projection import GraphProjection

    investigation = await seed_demo_investigation(session, user.id)
    await session.commit()

    graph = await GraphProjection(session, investigation.id).build()
    assert graph["nodes"], "the demo investigation must produce nodes"
    for node in graph["nodes"]:
        assert 0.0 <= node["display_confidence"] <= 1.0
        assert isinstance(node["is_stale"], bool)
        # display_confidence can only ever be <= the raw confidence, never inflate it.
        assert node["display_confidence"] <= node["confidence"] + 1e-9


async def test_entity_detail_carries_decay_fields(client, auth_client) -> None:
    seeded = await auth_client.post("/api/v1/demo/seed")
    assert seeded.status_code == 201, seeded.text
    investigation_id = seeded.json()["investigation_id"]

    entities = await auth_client.get(f"/api/v1/investigations/{investigation_id}/entities")
    assert entities.status_code == 200
    entity_id = entities.json()["items"][0]["id"]

    detail = await auth_client.get(f"/api/v1/entities/{entity_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert "display_confidence" in body
    assert "is_stale" in body
    assert body["display_confidence"] <= body["confidence"] + 1e-9
