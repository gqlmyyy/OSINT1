"""Value objects exchanged between the platform and providers.

These are deliberately plain dataclasses/Pydantic models with no SQLAlchemy imports:
a provider must never need to know the database schema.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    MATCH_STRENGTH_WEIGHT,
    Assertion,
    HealthState,
    MatchStrength,
    ProviderType,
    TargetType,
)


class Target(BaseModel):
    """One identifier a provider is asked to look at."""

    model_config = ConfigDict(frozen=True)

    type: TargetType
    value: str
    normalized: str
    depth: int = 0
    context: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_root(self) -> bool:
        return self.depth == 0


class ProviderRateLimit(BaseModel):
    model_config = ConfigDict(frozen=True)

    rpm: int = 60
    concurrency: int = 5
    timeout_seconds: float = 15.0
    max_retries: int = 2
    backoff_base_seconds: float = 0.5


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepts: list[TargetType]
    emits: list[str]
    requires_api_key: bool = False
    rate_limit: ProviderRateLimit = Field(default_factory=ProviderRateLimit)
    reliability: float = 0.8
    cost: str = "free"
    recursive: bool = True
    cache_ttl_seconds: int | None = None
    description: str = ""


class ProviderHealth(BaseModel):
    name: str
    state: HealthState
    detail: str = ""
    checked_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @classmethod
    def ok(cls, name: str, detail: str = "") -> ProviderHealth:
        return cls(name=name, state=HealthState.OK, detail=detail)

    @classmethod
    def degraded(cls, name: str, detail: str) -> ProviderHealth:
        return cls(name=name, state=HealthState.DEGRADED, detail=detail)

    @classmethod
    def unavailable(cls, name: str, detail: str) -> ProviderHealth:
        return cls(name=name, state=HealthState.UNAVAILABLE, detail=detail)

    @property
    def usable(self) -> bool:
        return self.state in (HealthState.OK, HealthState.DEGRADED)


class EdgeHint(BaseModel):
    """A relationship a provider believes exists, expressed in canonical-key terms."""

    model_config = ConfigDict(frozen=True)

    type: str
    target_kind: str
    target_value: str
    why: str = ""
    target_attributes: dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    """The single unit of provider output. Always carries its own evidence."""

    provider: str = ""
    source: str = ""
    kind: str
    value: str
    url: str | None = None
    label: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    match: MatchStrength = MatchStrength.PATTERN_MATCH
    assertion: Assertion = Assertion.OBSERVED
    confidence: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    excerpt: str = ""
    edges: list[EdgeHint] = Field(default_factory=list)
    derived_targets: list[tuple[str, str]] = Field(default_factory=list)

    def scored(self, reliability: float) -> float:
        """Confidence = provider reliability x how directly the provider verified it."""
        if self.confidence is not None:
            return max(0.0, min(1.0, self.confidence))
        return round(max(0.0, min(1.0, reliability * MATCH_STRENGTH_WEIGHT[self.match])), 4)


class ProviderResult(BaseModel):
    provider: str
    observations: list[Observation] = Field(default_factory=list)
    cache_hit: bool = False
    duration_ms: int = 0
    error: str | None = None


__all__ = [
    "EdgeHint",
    "Observation",
    "ProviderCapabilities",
    "ProviderHealth",
    "ProviderRateLimit",
    "ProviderResult",
    "ProviderType",
    "Target",
]
