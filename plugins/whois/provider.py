"""Registration data via RDAP over HTTPS.

RDAP is the IETF replacement for port-43 WHOIS: it is JSON, rate-limit friendly, and
needs no extra binary. Registrant personal data is normally redacted by the registry —
this provider records what is published and never attempts to unmask it.
"""

from __future__ import annotations

from typing import Any

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

RDAP_BOOTSTRAP = "https://rdap.org"


class WhoisProvider(OSINTProvider):
    name = "whois"
    provider_type = ProviderType.DOMAIN

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.DOMAIN, TargetType.IP],
            emits=["organization", "domain", "email"],
            rate_limit=ProviderRateLimit(rpm=30, concurrency=2, timeout_seconds=20),
            reliability=0.9,
            cost="free",
            cache_ttl_seconds=86400,
            description="Registration data over RDAP (the JSON successor to WHOIS).",
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name, "RDAP bootstrap over HTTPS")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        value = self.safe_value(target.normalized)
        kind = "ip" if target.type is TargetType.IP else "domain"
        base = str(ctx.get("rdap_base_url") or RDAP_BOOTSTRAP).rstrip("/")
        response = await ctx.http.get(f"{base}/{kind}/{value}")
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            return []
        record: dict[str, Any] = response.json()

        registrar = _entity_name(record, "registrar")
        registrant = _entity_name(record, "registrant")
        events = {e.get("eventAction"): e.get("eventDate") for e in record.get("events", [])}
        nameservers = [
            ns.get("ldhName", "").lower().rstrip(".")
            for ns in record.get("nameservers", [])
            if ns.get("ldhName")
        ]

        edges = [
            EdgeHint(
                type="HOSTED_ON",
                target_kind="domain",
                target_value=ns,
                why=f"Registry lists {ns} as an authoritative nameserver.",
            )
            for ns in nameservers[:8]
        ]
        if registrar:
            edges.append(
                EdgeHint(
                    type="ASSOCIATED_WITH",
                    target_kind="organization",
                    target_value=registrar,
                    why="Sponsoring registrar published in the RDAP record.",
                )
            )

        data = {
            "registrar": registrar,
            "registrant": registrant,
            "created": events.get("registration"),
            "updated": events.get("last changed"),
            "expires": events.get("expiration"),
            "status": record.get("status", []),
            "nameservers": nameservers,
            "handle": record.get("handle"),
        }
        excerpt = " · ".join(
            filter(
                None,
                [
                    f"registrar: {registrar}" if registrar else "",
                    f"created: {data['created']}" if data["created"] else "",
                    f"status: {', '.join(data['status'][:3])}" if data["status"] else "",
                    "registrant redacted" if not registrant else f"registrant: {registrant}",
                ],
            )
        )
        return [
            self.observation(
                kind="organization" if registrar else "domain",
                value=registrar or value,
                url=f"{base}/{kind}/{value}",
                match=MatchStrength.EXACT_ID,
                data=data,
                raw=record,
                excerpt=excerpt[:400],
                edges=edges,
            )
        ]


def _entity_name(record: dict[str, Any], role: str) -> str | None:
    for entity in record.get("entities", []):
        if role not in [r.lower() for r in entity.get("roles", [])]:
            continue
        vcard = entity.get("vcardArray")
        if isinstance(vcard, list) and len(vcard) > 1:
            for item in vcard[1]:
                if isinstance(item, list) and item and item[0] == "fn" and len(item) > 3:
                    return str(item[3])
        if entity.get("handle"):
            return str(entity["handle"])
    return None


PROVIDERS = [WhoisProvider]
