from __future__ import annotations

import httpx

from app.core.enums import TargetType
from app.providers.types import Target

TARGET = Target(type=TargetType.DOMAIN, value="example.com", normalized="example.com")

RDAP = {
    "handle": "EXAMPLE-COM",
    "status": ["client transfer prohibited"],
    "events": [
        {"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2027-08-13T04:00:00Z"},
    ],
    "nameservers": [{"ldhName": "A.IANA-SERVERS.NET."}, {"ldhName": "B.IANA-SERVERS.NET."}],
    "entities": [
        {
            "roles": ["registrar"],
            "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                     ["fn", {}, "text", "Example Registrar LLC"]]],
        }
    ],
}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/domain/example.com":
        return httpx.Response(200, json=RDAP)
    return httpx.Response(404, json={})


async def test_parses_registrar_dates_and_nameservers(load_provider, make_ctx) -> None:
    provider = load_provider("whois")
    observations = await provider.search(TARGET, make_ctx(handler))
    assert len(observations) == 1

    record = observations[0]
    assert record.value == "Example Registrar LLC"
    assert record.data["created"].startswith("1995")
    assert record.data["nameservers"] == ["a.iana-servers.net", "b.iana-servers.net"]
    assert "registrant redacted" in record.excerpt, "redaction must be stated, not hidden"

    ns_edges = [e for e in record.edges if e.target_kind == "domain"]
    assert len(ns_edges) == 2
    assert all(e.type == "HOSTED_ON" for e in ns_edges)


async def test_unknown_domain_yields_nothing(load_provider, make_ctx) -> None:
    provider = load_provider("whois")
    target = Target(type=TargetType.DOMAIN, value="nope.invalid", normalized="nope.invalid")
    assert await provider.search(target, make_ctx(handler)) == []
