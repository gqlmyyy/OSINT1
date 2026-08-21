"""Certificate Transparency provider (crt.sh): public CT logs -> subdomains."""

from __future__ import annotations

from app.core.enums import MatchStrength, ProviderType, TargetType
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.types import (
    EdgeHint,
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)

CRTSH = "https://crt.sh"
MAX_SUBDOMAINS = 60


class CrtShProvider(OSINTProvider):
    name = "crtsh"
    provider_type = ProviderType.DOMAIN

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.DOMAIN],
            emits=["domain", "organization"],
            rate_limit=ProviderRateLimit(rpm=10, concurrency=1, timeout_seconds=45),
            reliability=0.9,
            cost="free",
            cache_ttl_seconds=86400,
            description="Subdomains published in public Certificate Transparency logs.",
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name, "public CT log search")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        domain = self.safe_value(target.normalized)
        response = await ctx.http.get(f"{CRTSH}/?q=%25.{domain}&output=json")
        if response.status_code != 200:
            return []
        try:
            entries = response.json()
        except ValueError:
            return []
        if not isinstance(entries, list):
            return []

        seen: dict[str, dict[str, str]] = {}
        for entry in entries:
            for name in str(entry.get("name_value", "")).splitlines():
                host = name.strip().lower().lstrip("*.").rstrip(".")
                if not host or host == domain or not host.endswith(f".{domain}"):
                    continue
                seen.setdefault(
                    host,
                    {
                        "issuer": str(entry.get("issuer_name", ""))[:200],
                        "not_before": str(entry.get("not_before", "")),
                    },
                )
                if len(seen) >= MAX_SUBDOMAINS:
                    break
            if len(seen) >= MAX_SUBDOMAINS:
                break

        return [
            self.observation(
                kind="domain",
                value=host,
                url=f"{CRTSH}/?q={domain}",
                match=MatchStrength.EXACT_ID,
                data={"parent_domain": domain, "role": "ct_subdomain", **meta},
                raw={"host": host, **meta},
                excerpt=f"Certificate transparency entry for {host} (issuer: {meta['issuer']})",
                edges=[
                    EdgeHint(
                        type="ASSOCIATED_WITH",
                        target_kind="domain",
                        target_value=domain,
                        why=f"{host} appears in a public certificate covering {domain}.",
                    )
                ],
                derived_targets=[("domain", host)],
            )
            for host, meta in sorted(seen.items())
        ]


PROVIDERS = [CrtShProvider]
