"""DNS provider: public resolver lookups for A/AAAA/MX/NS/TXT/CNAME."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver

from app.core.enums import Assertion, MatchStrength, ProviderType, TargetType
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.types import (
    EdgeHint,
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)

RECORD_TYPES = ("A", "AAAA", "MX", "NS", "TXT", "CNAME")
SPF_INCLUDE = re.compile(r"include:([A-Za-z0-9.\-_]+)")


class DNSProvider(OSINTProvider):
    name = "dns"
    provider_type = ProviderType.NETWORK

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.DOMAIN],
            emits=["ip", "domain", "technology"],
            rate_limit=ProviderRateLimit(rpm=120, concurrency=8, timeout_seconds=12),
            reliability=0.97,
            cost="free",
            cache_ttl_seconds=1800,
            description="Public DNS records: A, AAAA, MX, NS, TXT, CNAME.",
        )

    async def health_check(self) -> ProviderHealth:
        try:
            resolver = dns.asyncresolver.Resolver()
            resolver.lifetime = 5.0
            await resolver.resolve("example.com", "A")
            return ProviderHealth.ok(self.name, "resolver reachable")
        except Exception as exc:
            return ProviderHealth.degraded(self.name, f"resolver check failed: {exc}")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        domain = self.safe_value(target.normalized)
        resolver = dns.asyncresolver.Resolver()
        resolver.lifetime = ctx.settings.http_timeout_seconds

        results = await asyncio.gather(
            *(self._query(resolver, domain, rtype) for rtype in RECORD_TYPES),
            return_exceptions=True,
        )

        observations: list[Observation] = []
        for rtype, answers in zip(RECORD_TYPES, results, strict=True):
            if isinstance(answers, BaseException) or not answers:
                continue
            observations.extend(self._to_observations(domain, rtype, answers))
        return observations

    @staticmethod
    async def _query(
        resolver: dns.asyncresolver.Resolver, domain: str, rtype: str
    ) -> list[str]:
        try:
            answer = await resolver.resolve(domain, rtype)
        except (
            dns.resolver.NXDOMAIN,
            dns.resolver.NoAnswer,
            dns.resolver.NoNameservers,
            dns.exception.Timeout,
        ):
            return []
        return [rdata.to_text().strip('"') for rdata in answer]

    def _to_observations(self, domain: str, rtype: str, answers: list[str]) -> list[Observation]:
        out: list[Observation] = []
        for value in answers:
            common: dict[str, Any] = {"record": rtype, "domain": domain, "raw": value}
            if rtype in ("A", "AAAA"):
                out.append(
                    self.observation(
                        kind="ip",
                        value=value,
                        match=MatchStrength.EXACT_ID,
                        data={**common, "version": 4 if rtype == "A" else 6},
                        raw=common,
                        excerpt=f"{domain}. IN {rtype} {value}",
                    )
                )
            elif rtype in ("MX", "NS", "CNAME"):
                host = value.split()[-1].rstrip(".")
                if not host:
                    continue
                out.append(
                    self.observation(
                        kind="domain",
                        value=host,
                        match=MatchStrength.EXACT_ID,
                        data={**common, "role": rtype.lower()},
                        raw=common,
                        excerpt=f"{domain}. IN {rtype} {value}",
                        edges=[
                            EdgeHint(
                                type="HOSTED_ON" if rtype != "CNAME" else "REDIRECTS_TO",
                                target_kind="domain",
                                target_value=host,
                                why=f"{rtype} record for {domain} points at {host}.",
                            )
                        ],
                    )
                )
            elif rtype == "TXT":
                for include in SPF_INCLUDE.findall(value):
                    out.append(
                        self.observation(
                            kind="domain",
                            value=include,
                            match=MatchStrength.PATTERN_MATCH,
                            assertion=Assertion.OBSERVED,
                            data={**common, "role": "spf_include"},
                            raw=common,
                            excerpt=f"SPF record includes {include}",
                        )
                    )
                if value.lower().startswith(("google-site-verification", "ms=", "atlassian-")):
                    out.append(
                        self.observation(
                            kind="technology",
                            value=value.split("=")[0],
                            match=MatchStrength.PATTERN_MATCH,
                            data={**common, "category": "domain_verification"},
                            raw=common,
                            excerpt=value[:200],
                        )
                    )
        return out


PROVIDERS = [DNSProvider]
