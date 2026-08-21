"""The provider contract."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import Settings, get_settings
from app.core.enums import Assertion, MatchStrength, ProviderType
from app.providers.types import (
    EdgeHint,
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    Target,
)
from app.security.ssrf import SafeAsyncClient

#: Anything sent to an external process or interpolated into a provider URL must match
#: this. It is the last line of defence against command/URL injection via a target value.
SAFE_TARGET_RE = re.compile(r"^[A-Za-z0-9._@+:/-]{1,190}$")


class ProviderContext:
    """Everything a provider is allowed to use. No DB session, no event bus."""

    def __init__(
        self,
        http: SafeAsyncClient,
        *,
        settings: Settings | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.http = http
        self.settings = settings or get_settings()
        self.config = config or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)


class OSINTProvider(ABC):
    """Base class every plugin implements.

    Subclasses must be import-safe: no network calls or binary probing at import time.
    """

    name: str = "unnamed"
    provider_type: ProviderType = ProviderType.AGGREGATOR

    @abstractmethod
    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        """Return observations for ``target``. Raise on hard failure; return [] on no hit."""

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        """Report whether this provider can run right now."""

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Static declaration used by the orchestrator to plan jobs."""

    # -- helpers available to every provider -----------------------------------

    def accepts(self, target: Target) -> bool:
        return target.type in self.capabilities().accepts

    def observation(
        self,
        *,
        kind: str,
        value: str,
        url: str | None = None,
        label: str | None = None,
        data: dict[str, Any] | None = None,
        raw: dict[str, Any] | None = None,
        excerpt: str = "",
        match: MatchStrength = MatchStrength.PATTERN_MATCH,
        assertion: Assertion = Assertion.OBSERVED,
        confidence: float | None = None,
        edges: list[EdgeHint] | None = None,
        derived_targets: list[tuple[str, str]] | None = None,
        source: str | None = None,
    ) -> Observation:
        return Observation(
            provider=self.name,
            source=source or self.name,
            kind=kind,
            value=value,
            url=url,
            label=label,
            data=data or {},
            raw=raw or {},
            excerpt=excerpt[:2000],
            match=match,
            assertion=assertion,
            confidence=confidence,
            edges=edges or [],
            derived_targets=derived_targets or [],
        )

    @staticmethod
    def safe_value(value: str) -> str:
        """Validate a value before it reaches a URL path or a subprocess argv."""
        if not SAFE_TARGET_RE.match(value):
            raise ValueError(f"unsafe target value rejected: {value!r}")
        return value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name}>"


__all__ = ["OSINTProvider", "ProviderContext", "SAFE_TARGET_RE"]
