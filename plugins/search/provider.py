"""Search-engine provider backed by a SearxNG-compatible JSON endpoint.

Disabled until an operator points ``SEARX_BASE_URL`` (or the source's ``base_url``
config) at an instance they are allowed to query — we do not scrape search engines that
forbid it, and we do not bundle a default endpoint someone else pays for.
"""

from __future__ import annotations

import contextlib
from urllib.parse import quote

from app.core.enums import Assertion, MatchStrength, ProviderType, TargetType
from app.evidence.normalizer import NormalizationError, normalize_domain
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.types import (
    EdgeHint,
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)

MAX_RESULTS = 20


class SearchEngineProvider(OSINTProvider):
    name = "search"
    provider_type = ProviderType.SEARCH_ENGINE

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[
                TargetType.USERNAME,
                TargetType.EMAIL,
                TargetType.DOMAIN,
                TargetType.FULL_NAME,
                TargetType.DISPLAY_NAME,
                TargetType.COMPANY,
            ],
            emits=["url", "website"],
            requires_api_key=True,
            rate_limit=ProviderRateLimit(rpm=20, concurrency=2, timeout_seconds=25),
            reliability=0.6,
            cost="free",
            recursive=False,
            description="Queries a SearxNG-compatible JSON endpoint you configure.",
        )

    def _base_url(self, ctx: ProviderContext) -> str | None:
        base = ctx.get("base_url") or ctx.settings.searx_base_url
        return str(base).rstrip("/") if base else None

    async def health_check(self) -> ProviderHealth:
        from app.core.config import get_settings

        if not get_settings().searx_base_url:
            return ProviderHealth.unavailable(
                self.name, "set SEARX_BASE_URL (or the source's base_url) to enable"
            )
        return ProviderHealth.ok(self.name, "endpoint configured")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        base = self._base_url(ctx)
        if not base:
            return []
        query = quote(f'"{target.normalized}"', safe="")
        response = await ctx.http.get(f"{base}/search?q={query}&format=json&safesearch=0")
        if response.status_code != 200:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []

        observations: list[Observation] = []
        for item in payload.get("results", [])[:MAX_RESULTS]:
            url = str(item.get("url", ""))
            if not url.startswith(("http://", "https://")):
                continue
            edges = []
            with contextlib.suppress(NormalizationError):
                edges.append(
                    EdgeHint(
                        type="REFERENCES",
                        target_kind="website",
                        target_value=normalize_domain(url),
                        why="Search result hosted on this site.",
                    )
                )
            observations.append(
                self.observation(
                    kind="url",
                    value=url,
                    url=url,
                    label=str(item.get("title") or url)[:200],
                    # A search hit mentions the term; it does not establish ownership.
                    match=MatchStrength.WEAK_HEURISTIC,
                    assertion=Assertion.UNVERIFIED,
                    data={
                        "title": item.get("title"),
                        "engine": item.get("engine"),
                        "query": target.normalized,
                    },
                    raw=item,
                    excerpt=str(item.get("content") or "")[:400],
                    edges=edges,
                )
            )
        return observations


PROVIDERS = [SearchEngineProvider]
