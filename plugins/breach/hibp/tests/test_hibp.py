"""HIBP breach provider: metadata only, honest degrade without a key."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx

from app.core.enums import TargetType
from app.providers.types import Target

PROVIDER_SOURCE = Path(__file__).resolve().parents[1] / "provider.py"
TARGET = Target(type=TargetType.EMAIL, value="user@example.com", normalized="user@example.com")

BREACHES = [
    {
        "Name": "Adobe",
        "Title": "Adobe",
        "Domain": "adobe.com",
        "BreachDate": "2013-10-04",
        "Description": "In October 2013, 153 million Adobe accounts were breached...",
        "DataClasses": ["Email addresses", "Password hints", "Passwords", "Usernames"],
    },
    {
        "Name": "Gawker",
        "Title": "Gawker",
        "Domain": "",
        "BreachDate": "2010-12-11",
        "Description": "In December 2010, a set of Gawker's databases were breached...",
        "DataClasses": ["Email addresses", "Passwords"],
    },
]


def handler(request: httpx.Request) -> httpx.Response:
    if "no-account" in str(request.url):
        return httpx.Response(404)
    if request.headers.get("hibp-api-key") != "test-key":
        return httpx.Response(401, json={"message": "Access denied"})
    return httpx.Response(200, json=BREACHES)


# -- unconfigured is the default and must be honest ---------------------------


async def test_reports_unavailable_without_a_key(load_provider, monkeypatch) -> None:
    monkeypatch.delenv("HIBP_API_KEY", raising=False)
    health = await load_provider("hibp").health_check()
    assert health.state == "unavailable"
    assert "HIBP_API_KEY" in health.detail


async def test_collects_nothing_without_a_key(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.delenv("HIBP_API_KEY", raising=False)
    assert await load_provider("hibp").search(TARGET, make_ctx(handler)) == []


def test_declares_that_it_requires_a_key(load_provider) -> None:
    assert load_provider("hibp").capabilities().requires_api_key is True


# -- configured: the official API path -----------------------------------------


async def test_maps_breaches_to_metadata_only_observations(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.setenv("HIBP_API_KEY", "test-key")

    observations = await load_provider("hibp").search(TARGET, make_ctx(handler))
    assert len(observations) == 2
    adobe = next(o for o in observations if o.data["breach_name"] == "Adobe")
    assert adobe.kind == "breach"
    assert adobe.data["breach_date"] == "2013-10-04"
    assert adobe.data["source"] == "adobe.com"
    assert adobe.data["email"] == "user@example.com"

    gawker = next(o for o in observations if o.data["breach_name"] == "Gawker")
    assert gawker.data["source"] == "haveibeenpwned.com"  # no domain given, falls back


async def test_never_stores_leaked_data_or_description(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.setenv("HIBP_API_KEY", "test-key")

    observations = await load_provider("hibp").search(TARGET, make_ctx(handler))
    for observation in observations:
        payload = str(observation.data) + str(observation.raw)
        assert "153 million" not in payload
        assert "DataClasses" not in payload
        assert "Password hints" not in payload


async def test_no_breach_found_yields_nothing(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.setenv("HIBP_API_KEY", "test-key")
    target = Target(type=TargetType.EMAIL, value="no-account@example.com", normalized="no-account@example.com")
    assert await load_provider("hibp").search(target, make_ctx(handler)) == []


async def test_invalid_key_at_call_time_yields_nothing_not_a_crash(
    load_provider, make_ctx, monkeypatch
) -> None:
    monkeypatch.setenv("HIBP_API_KEY", "bad-key")
    assert await load_provider("hibp").search(TARGET, make_ctx(handler)) == []


async def test_malformed_json_response_does_not_crash(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.setenv("HIBP_API_KEY", "test-key")

    def broken_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    assert await load_provider("hibp").search(TARGET, make_ctx(broken_handler)) == []


async def test_unexpected_json_shape_does_not_crash(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.setenv("HIBP_API_KEY", "test-key")

    def odd_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "a list"})

    assert await load_provider("hibp").search(TARGET, make_ctx(odd_handler)) == []


async def test_the_api_key_is_never_stored_in_evidence(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.setenv("HIBP_API_KEY", "super-secret-key")

    def key_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=BREACHES)

    observations = await load_provider("hibp").search(TARGET, make_ctx(key_handler))
    assert observations
    for observation in observations:
        assert "super-secret-key" not in str(observation.raw)
        assert "super-secret-key" not in str(observation.data)


# -- policy ---------------------------------------------------------------------


def test_provider_contains_no_unauthenticated_or_scraping_path() -> None:
    tree = ast.parse(PROVIDER_SOURCE.read_text())
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and ast.get_docstring(node) is not None:
            node.body = node.body[1:]
    body = ast.unparse(tree).lower()

    forbidden = ["selenium", "playwright", "webdriver", "cookie", "captcha"]
    present = [token for token in forbidden if token in body]
    assert not present, f"scraping machinery found in the provider: {present}"

    import re

    for url in re.findall(r"https?://[^\s\"']+", body):
        assert "haveibeenpwned.com" in url, url


def test_provider_never_reads_dataclasses_or_description_fields() -> None:
    """Policy: the fields carrying leak *content signals* must never be read."""
    body = PROVIDER_SOURCE.read_text()
    assert '"DataClasses"' not in body
    assert '"Description"' not in body
    assert "IsVerified" not in body
