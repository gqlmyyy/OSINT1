from __future__ import annotations

import dns.resolver
import pytest

from app.core.enums import TargetType
from app.providers.types import Target

TARGET = Target(type=TargetType.DOMAIN, value="example.com", normalized="example.com")

ANSWERS = {
    "A": ["93.184.216.34"],
    "AAAA": ["2606:2800:220:1:248:1893:25c8:1946"],
    "MX": ["10 mail.example.com."],
    "NS": ["ns1.example.net.", "ns2.example.net."],
    "TXT": ["v=spf1 include:_spf.example.net ~all", "google-site-verification=abc"],
    "CNAME": [],
}


@pytest.fixture
def stub_resolver(monkeypatch, load_provider):
    provider = load_provider("dns")

    async def fake_query(_resolver, domain, rtype):
        return ANSWERS.get(rtype, [])

    monkeypatch.setattr(type(provider), "_query", staticmethod(fake_query))
    return provider


async def test_maps_records_to_entities_and_edges(stub_resolver, make_ctx) -> None:
    observations = await stub_resolver.search(TARGET, make_ctx(lambda r: None))
    kinds = [(o.kind, o.value) for o in observations]

    assert ("ip", "93.184.216.34") in kinds
    assert ("ip", "2606:2800:220:1:248:1893:25c8:1946") in kinds
    assert ("domain", "mail.example.com") in kinds
    assert ("domain", "ns1.example.net") in kinds
    assert ("domain", "_spf.example.net") in kinds, "SPF includes are useful pivots"
    assert any(kind == "technology" for kind, _ in kinds)

    ns = next(o for o in observations if o.value == "ns1.example.net")
    assert ns.edges[0].type == "HOSTED_ON"
    assert "NS record" in ns.edges[0].why


async def test_nxdomain_is_empty_not_an_error(monkeypatch, load_provider, make_ctx) -> None:
    provider = load_provider("dns")

    async def raising(_resolver, domain, rtype):
        raise dns.resolver.NXDOMAIN()

    async def guarded(_resolver, domain, rtype):
        try:
            return await raising(_resolver, domain, rtype)
        except dns.resolver.NXDOMAIN:
            return []

    monkeypatch.setattr(type(provider), "_query", staticmethod(guarded))
    assert await provider.search(TARGET, make_ctx(lambda r: None)) == []
