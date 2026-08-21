from __future__ import annotations

import httpx

from app.core.enums import Assertion, TargetType
from app.providers.types import Target

TARGET = Target(type=TargetType.USERNAME, value="example_user", normalized="example_user")

RESULTS = {
    "results": [
        {"url": "https://example.com/about", "title": "About", "engine": "duckduckgo",
         "content": "example_user writes here"},
        {"url": "javascript:alert(1)", "title": "bad", "engine": "x"},
    ]
}


def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=RESULTS)


async def test_disabled_without_configuration(load_provider, make_ctx) -> None:
    provider = load_provider("search")
    health = await provider.health_check()
    assert health.state == "unavailable"
    assert await provider.search(TARGET, make_ctx(handler)) == []


async def test_results_are_unverified_and_sanitised(load_provider, make_ctx) -> None:
    provider = load_provider("search")
    ctx = make_ctx(handler, base_url="https://searx.example.com")
    observations = await provider.search(TARGET, ctx)

    assert len(observations) == 1, "non-http results must be discarded"
    hit = observations[0]
    assert hit.assertion is Assertion.UNVERIFIED, "a search hit is a mention, not ownership"
    assert hit.url == "https://example.com/about"
    assert hit.edges[0].target_value == "example.com"
