from __future__ import annotations

import httpx

from app.core.enums import TargetType
from app.providers.types import Target

TARGET = Target(type=TargetType.DOMAIN, value="example.com", normalized="example.com")

PAGE = """<!doctype html><html><head>
<title>  Example User —   engineering notes </title>
<meta name="generator" content="Hugo 0.128">
<meta name="description" content="Notes about graphs">
</head><body>
<a href="https://github.com/example_user">code</a>
<a href="https://www.reddit.com/user/example_user">reddit</a>
<a href="https://x.com/example_user">x</a>
<a href="https://github.com/login">login</a>
<a href="/about">about</a>
<a href="mailto:example.user@example.com">mail</a>
<p>or write to fallback@example.com</p>
<script src="/wp-content/theme.js"></script>
</body></html>"""


def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=PAGE, headers={"content-type": "text/html; charset=utf-8"})


async def test_extracts_title_socials_emails_and_technology(load_provider, make_ctx) -> None:
    provider = load_provider("website")
    observations = await provider.search(TARGET, make_ctx(handler))
    assert len(observations) == 1
    site = observations[0]

    assert site.data["title"] == "Example User — engineering notes"
    assert site.data["generator"] == "Hugo 0.128"
    assert "example.user@example.com" in site.data["emails"]
    assert "fallback@example.com" in site.data["emails"]
    assert "WordPress" in site.data["technologies"]

    socials = {
        (e.target_attributes["platform"], e.target_value)
        for e in site.edges
        if e.target_kind == "social_account"
    }
    assert ("GitHub", "example_user") in socials
    assert ("Reddit", "example_user") in socials
    assert ("X", "example_user") in socials
    assert not any(handle == "login" for _, handle in socials), "site chrome must be filtered out"

    derived = dict(site.derived_targets)
    assert derived.get("email") or ("email", "fallback@example.com") in site.derived_targets


async def test_non_html_responses_are_ignored(load_provider, make_ctx) -> None:
    def binary(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})

    provider = load_provider("website")
    assert await provider.search(TARGET, make_ctx(binary)) == []


async def test_error_pages_are_ignored(load_provider, make_ctx) -> None:
    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope", headers={"content-type": "text/html"})

    provider = load_provider("website")
    assert await provider.search(TARGET, make_ctx(missing)) == []


async def test_malformed_html_does_not_raise(load_provider, make_ctx) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="<html><title>unclosed <a href=", headers={"content-type": "text/html"}
        )

    provider = load_provider("website")
    observations = await provider.search(TARGET, make_ctx(broken))
    assert len(observations) == 1
