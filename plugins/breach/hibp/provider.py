"""Have I Been Pwned: breach *metadata* only, never leaked data.

HIBP's breach API reports which named breaches an email address appeared in, and when
each breach occurred — it does not return the leaked data itself, and this provider does
not request or store any (passwords, other PII, HIBP's own "data classes" list of what a
breach exposed). What is kept is exactly what the brief asks for: which breach, when, and
where it was reported — enough to prompt "this address was compromised in the Adobe 2013
breach," never a copy or characterisation of what leaked.

Requires the operator's own HIBP API key (a paid subscription under HIBP's own pricing
model) — set HIBP_API_KEY, or the source's ``api_key`` config. There is no unauthenticated
or scraped path to this data.
"""

from __future__ import annotations

import os
from typing import Any

from app.core.enums import Assertion, MatchStrength, ProviderType, TargetType
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.types import (
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)

API = "https://haveibeenpwned.com/api/v3/breachedaccount"
USER_AGENT = "GraphIntel-OSINT-Breach-Check"

UNCONFIGURED_DETAIL = (
    "Have I Been Pwned requires your own API key (a paid HIBP subscription). Set "
    "HIBP_API_KEY to enable breach lookups; without it this stays unavailable rather "
    "than attempting any unauthenticated route."
)


class HibpProvider(OSINTProvider):
    name = "hibp"
    provider_type = ProviderType.EMAIL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.EMAIL],
            emits=["breach"],
            requires_api_key=True,
            rate_limit=ProviderRateLimit(rpm=6, concurrency=1, timeout_seconds=20, max_retries=1),
            reliability=0.97,
            cost="paid",
            recursive=False,
            cache_ttl_seconds=24 * 3600,
            description=(
                "Breach membership via the official Have I Been Pwned API. Stores only "
                "the breach name, date and source — never any leaked data. Requires your "
                "own HIBP API key."
            ),
        )

    @staticmethod
    def _key(ctx: ProviderContext | None = None) -> str | None:
        key = ctx.get("api_key") if ctx is not None else None
        return str(key) if key else os.environ.get("HIBP_API_KEY")

    async def health_check(self) -> ProviderHealth:
        if not self._key():
            return ProviderHealth.unavailable(self.name, UNCONFIGURED_DETAIL)
        return ProviderHealth.ok(self.name, "HIBP API key configured")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        key = self._key(ctx)
        if not key:
            return []
        email = self.safe_value(target.normalized)

        response = await ctx.http.get(
            f"{API}/{email}?truncateResponse=false",
            headers={"hibp-api-key": key, "user-agent": USER_AGENT},
        )
        if response.status_code != 200:
            # 404 = no breach found (not an error); 401/429/5xx = can't tell right now.
            # Either way there is nothing safe to report from this call.
            return []
        try:
            breaches = response.json()
        except ValueError:
            return []
        if not isinstance(breaches, list):
            return []

        observations = []
        for breach in breaches:
            observation = self._breach(email, breach)
            if observation is not None:
                observations.append(observation)
        return observations

    def _breach(self, email: str, breach: Any) -> Observation | None:
        if not isinstance(breach, dict) or not breach.get("Name"):
            return None
        name = str(breach["Name"])
        title = str(breach.get("Title") or name)
        date = str(breach.get("BreachDate") or "") or None
        domain = str(breach.get("Domain") or "").strip()
        source = domain or "haveibeenpwned.com"

        return self.observation(
            kind="breach",
            value=f"{email}:{name}",
            url="https://haveibeenpwned.com/PwnedWebsites",
            label=f"Breach: {title}",
            match=MatchStrength.EXACT_ID,
            assertion=Assertion.OBSERVED,
            confidence=0.95,
            data={
                # Deliberately minimal: which breach, when, where reported — never any
                # leaked field values, hashed or not, and never HIBP's own description
                # or "data classes" text.
                "email": email,
                "breach_name": name,
                "breach_title": title,
                "breach_date": date,
                "source": source,
            },
            raw={"Name": name, "BreachDate": date},
            excerpt=f"{email} appears in the reported '{title}' breach ({date or 'date unknown'}).",
        )


PROVIDERS = [HibpProvider]
