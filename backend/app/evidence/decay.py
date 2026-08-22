"""Confidence decay for display.

An observation from 2019 and one from this morning do not deserve equal visual weight,
but the *stored* confidence is evidence — what a provider actually reported at the time
— and must never be silently rewritten. Decay is therefore a read-time projection: the
database keeps the original number forever, and the API additionally returns a
``display_confidence`` that has been discounted for age, plus a flag so the UI can say
so explicitly rather than let a stale fact look as fresh as a new one.

Half-lives are calibrated per source category. A DNS record or a certificate-transparency
entry describes infrastructure that changes rarely, so it decays slowly; a social post's
relevance to "what is true about this person right now" fades faster.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

#: Days after which display confidence has halved, by provider category. Missing a
#: provider here means "does not decay" (e.g. a correlation is a statement about the
#: evidence at hand, not about the present moment, so it is intentionally absent).
HALF_LIFE_DAYS: dict[str, int] = {
    "dns": 720,
    "whois": 720,
    "crtsh": 720,
    "github": 365,
    "gravatar": 365,
    "website": 180,
    "username_enum": 180,
    "maigret": 180,
    "sherlock": 180,
    "search": 90,
    "mastodon": 60,
    "instagram": 60,
    "twitter": 60,
    "telegram": 60,
    "holehe": 365,
    "image_geo": 3650,  # EXIF is a fact about the file, not about today
    "hibp": 3650,  # a breach date does not become less true
}

#: Below this, decay is treated as negligible and the UI need not mention it.
DEFAULT_HALF_LIFE_DAYS = 365
STALE_THRESHOLD = 0.6
#: Confidence never decays below this floor — decay expresses uncertainty about
#: *currency*, not a claim that the original observation was wrong.
MIN_DECAYED_CONFIDENCE = 0.15


@dataclass(frozen=True)
class DecayResult:
    display_confidence: float
    is_stale: bool
    age_days: int
    note: str | None


def decay_factor(age_days: float, half_life_days: int) -> float:
    if age_days <= 0 or half_life_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def decay_with_half_life(
    confidence: float,
    observed_at: datetime,
    half_life_days: int,
    *,
    now: datetime | None = None,
    label: str = "Observed",
) -> DecayResult:
    """The math, parameterised on an already-resolved half-life.

    ``apply_decay`` is the per-provider entry point; ``decay_for_entity`` (graph
    projection) resolves its own half-life across multiple sources and calls this
    directly, so the arithmetic and the staleness rule exist in exactly one place.
    """
    now = now or datetime.now(tz=UTC)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    age_days = max(0, (now - observed_at).days)

    factor = decay_factor(age_days, half_life_days)
    decayed = max(MIN_DECAYED_CONFIDENCE, round(confidence * factor, 4))
    stale = decayed < confidence * STALE_THRESHOLD

    note = None
    if stale:
        years = age_days / 365
        age_text = f"{years:.1f} years ago" if years >= 1 else f"{age_days} days ago"
        note = f"{label} {age_text}; shown at reduced confidence and may need re-verification."

    return DecayResult(display_confidence=decayed, is_stale=stale, age_days=age_days, note=note)


def apply_decay(
    confidence: float,
    observed_at: datetime,
    provider: str,
    *,
    now: datetime | None = None,
) -> DecayResult:
    half_life = HALF_LIFE_DAYS.get(provider, DEFAULT_HALF_LIFE_DAYS)
    return decay_with_half_life(confidence, observed_at, half_life, now=now)


def half_life_for_sources(sources: list[str]) -> int:
    """The most conservative half-life among an entity's contributing sources."""
    if not sources:
        return DEFAULT_HALF_LIFE_DAYS
    return min(HALF_LIFE_DAYS.get(s, DEFAULT_HALF_LIFE_DAYS) for s in sources)
