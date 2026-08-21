"""Built-in username enumeration across public profile pages.

Architecturally inspired by Sherlock and Maigret, implemented independently: the site
manifest in ``platforms.json`` is data, and the checker below is a small, auditable
state machine over it.

It asks public profile URLs whether a handle exists, exactly as a browser would. It does
not solve CAPTCHAs, does not authenticate, does not retry against rate-limit walls, and
treats any ambiguous answer as *unverified* rather than a hit.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from app.core.enums import Assertion, MatchStrength, ProviderType, TargetType
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.types import (
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)

MANIFEST = Path(__file__).parent / "platforms.json"
BATCH = 8


class UsernameEnumProvider(OSINTProvider):
    name = "username_enum"
    provider_type = ProviderType.USERNAME

    def __init__(self) -> None:
        self._sites: list[dict[str, Any]] | None = None

    def sites(self) -> list[dict[str, Any]]:
        if self._sites is None:
            with MANIFEST.open(encoding="utf-8") as handle:
                self._sites = list(json.load(handle)["sites"])
        return self._sites

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME],
            emits=["social_account"],
            rate_limit=ProviderRateLimit(rpm=90, concurrency=8, timeout_seconds=60, max_retries=1),
            reliability=0.82,
            cost="free",
            description=f"Checks {len(self.sites())} public profile pages for a handle.",
        )

    async def health_check(self) -> ProviderHealth:
        try:
            count = len(self.sites())
        except Exception as exc:
            return ProviderHealth.unavailable(self.name, f"manifest unreadable: {exc}")
        return ProviderHealth.ok(self.name, f"{count} sites in manifest")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        handle = self.safe_value(target.normalized)
        sites = self.sites()
        only = ctx.get("sites")
        if isinstance(only, list) and only:
            wanted = {str(s).lower() for s in only}
            sites = [s for s in sites if s["name"].lower() in wanted]

        observations: list[Observation] = []
        semaphore = asyncio.Semaphore(BATCH)

        async def check(site: dict[str, Any]) -> Observation | None:
            async with semaphore:
                return await self._check_site(site, handle, ctx)

        for result in await asyncio.gather(
            *(check(site) for site in sites), return_exceptions=True
        ):
            if isinstance(result, Observation):
                observations.append(result)
        return observations

    async def _check_site(
        self, site: dict[str, Any], handle: str, ctx: ProviderContext
    ) -> Observation | None:
        url = str(site["url"]).replace("{}", handle)
        try:
            response = await ctx.http.get(url, headers={"Accept": "text/html,application/json"})
        except (httpx.HTTPError, ValueError, OSError):
            return None

        valid_status = set(site.get("valid_status", [200]))
        method = site.get("method", "status")
        if response.status_code not in valid_status:
            return None

        marker = site.get("marker", "")
        body = response.text[:200_000]
        if method == "body_absent" and marker and marker in body:
            return None
        if method == "body_present" and marker and marker not in body:
            return None

        # A 200 from a status-only check is the weakest signal in the set: some sites
        # answer 200 with a soft-404 page. Say so instead of overclaiming.
        strength = (
            MatchStrength.CLAIMED_LINK if method != "status" else MatchStrength.PATTERN_MATCH
        )
        assertion = (
            Assertion.OBSERVED if method != "status" else Assertion.UNVERIFIED
        )
        return self.observation(
            kind="social_account",
            value=handle,
            url=str(response.url),
            label=f"{site['name']}/{handle}",
            match=strength,
            assertion=assertion,
            data={
                "platform": site["name"],
                "username": handle,
                "category": site.get("category", "other"),
                "check_method": method,
                "status_code": response.status_code,
            },
            raw={
                "site": site["name"],
                "url": str(response.url),
                "status_code": response.status_code,
                "method": method,
            },
            excerpt=(
                f"{site['name']} returned HTTP {response.status_code} for this handle"
                + (" and the page contains the expected marker." if marker else ".")
            ),
        )


PROVIDERS = [UsernameEnumProvider]
