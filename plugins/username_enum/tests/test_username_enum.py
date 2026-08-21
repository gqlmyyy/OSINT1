from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.core.enums import Assertion, TargetType
from app.providers.types import Target

TARGET = Target(type=TargetType.USERNAME, value="example_user", normalized="example_user")
MANIFEST = Path(__file__).resolve().parents[1] / "platforms.json"


def test_manifest_is_well_formed() -> None:
    sites = json.loads(MANIFEST.read_text())["sites"]
    assert len(sites) >= 30
    names = [s["name"] for s in sites]
    assert len(names) == len(set(names)), "duplicate site names would produce duplicate nodes"
    for site in sites:
        assert "{}" in site["url"], f"{site['name']} has no handle placeholder"
        assert site["url"].startswith("https://"), f"{site['name']} must use TLS"
        assert site["method"] in ("status", "body_absent", "body_present")
        if site["method"] != "status":
            assert site.get("marker"), f"{site['name']} needs a marker for {site['method']}"


async def test_body_markers_decide_hits(load_provider, make_ctx) -> None:
    provider = load_provider("username_enum")

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if "reddit" in host:
            return httpx.Response(200, json={"name": "example_user"})
        if "ycombinator" in host:
            return httpx.Response(200, text="<html>No such user.</html>")
        if "t.me" in host:
            return httpx.Response(200, text="<html>tgme_page_title</html>")
        return httpx.Response(404)

    ctx = make_ctx(handler, sites=["Reddit", "HackerNews", "Telegram", "GitHub"])
    observations = await provider.search(TARGET, ctx)
    platforms = {o.data["platform"] for o in observations}

    assert "Reddit" in platforms, "body_present marker matched"
    assert "Telegram" in platforms, "body_absent marker not found means the profile exists"
    assert "HackerNews" not in platforms, "the 'no such user' marker must suppress the hit"
    assert "GitHub" not in platforms, "404 is not a hit"


async def test_network_errors_do_not_fail_the_whole_sweep(load_provider, make_ctx) -> None:
    provider = load_provider("username_enum")
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if "reddit" in request.url.host:
            raise httpx.ConnectError("connection reset")
        return httpx.Response(200, text="<html>tgme_page_title</html>")

    ctx = make_ctx(flaky, sites=["Reddit", "Telegram"])
    observations = await provider.search(TARGET, ctx)
    assert [o.data["platform"] for o in observations] == ["Telegram"]


async def test_status_only_checks_stay_unverified(load_provider, make_ctx) -> None:
    provider = load_provider("username_enum")
    ctx = make_ctx(lambda r: httpx.Response(200, text="ok"), sites=["GitHub"])
    observations = await provider.search(TARGET, ctx)
    assert observations[0].assertion is Assertion.UNVERIFIED
