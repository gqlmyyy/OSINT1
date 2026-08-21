from __future__ import annotations

import httpx

from app.core.enums import TargetType
from app.providers.types import Target

TARGET = Target(type=TargetType.DOMAIN, value="example.com", normalized="example.com")

ENTRIES = [
    {"name_value": "blog.example.com\nwww.example.com", "issuer_name": "C=US, O=Example CA",
     "not_before": "2025-11-16T00:00:00"},
    {"name_value": "*.api.example.com", "issuer_name": "Example CA", "not_before": "2026-01-01"},
    {"name_value": "example.com", "issuer_name": "Example CA", "not_before": "2026-01-01"},
    {"name_value": "unrelated.other.net", "issuer_name": "Other CA", "not_before": "2026-01-01"},
]


def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=ENTRIES)


async def test_collects_subdomains_and_skips_the_apex_and_foreign_hosts(
    load_provider, make_ctx
) -> None:
    provider = load_provider("crtsh")
    observations = await provider.search(TARGET, make_ctx(handler))
    hosts = {o.value for o in observations}

    assert hosts == {"blog.example.com", "www.example.com", "api.example.com"}
    assert all(o.kind == "domain" for o in observations)
    assert all(("domain", host) in o.derived_targets for o, host in zip(
        sorted(observations, key=lambda o: o.value), sorted(hosts), strict=True
    ))


async def test_non_json_response_is_survivable(load_provider, make_ctx) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>rate limited</html>")

    provider = load_provider("crtsh")
    assert await provider.search(TARGET, make_ctx(broken)) == []
