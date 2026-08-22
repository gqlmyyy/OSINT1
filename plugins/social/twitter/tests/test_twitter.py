"""X (Twitter) provider: correct behaviour when unconfigured, and no scraping path."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from app.core.enums import TargetType
from app.providers.types import Target

PROVIDER_SOURCE = Path(__file__).resolve().parents[1] / "provider.py"
TARGET = Target(type=TargetType.USERNAME, value="example_user", normalized="example_user")

USER_PAYLOAD = {
    "data": {
        "id": "999000111",
        "username": "example_user",
        "name": "Example User",
        "description": "Building things with @alice #osint",
        "url": "https://example.com",
        "profile_image_url": "https://cdn.test/a.jpg",
        "location": "Somewhere",
        "created_at": "2020-01-01T00:00:00.000Z",
        "public_metrics": {"followers_count": 500, "following_count": 10, "tweet_count": 42},
    }
}

TWEETS_PAYLOAD = {
    "data": [
        {
            "id": "1700000000000000000",
            "text": "new project #osint with @bob https://example.com/x",
            "created_at": "2026-05-01T00:00:00.000Z",
        }
    ]
}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/2/users/by/username/example_user":
        return httpx.Response(200, json=USER_PAYLOAD)
    if request.url.path == "/2/users/999000111/tweets":
        return httpx.Response(200, json=TWEETS_PAYLOAD)
    return httpx.Response(404)


# -- unconfigured is the default and must be honest ---------------------------


async def test_reports_unavailable_without_a_bearer_token(load_provider, monkeypatch) -> None:
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_BEARER_TOKEN", raising=False)

    health = await load_provider("twitter").health_check()
    assert health.state == "unavailable"
    assert "X_BEARER_TOKEN" in health.detail
    assert "does not work around access controls" in health.detail


async def test_collects_nothing_without_a_bearer_token(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_BEARER_TOKEN", raising=False)
    assert await load_provider("twitter").search(TARGET, make_ctx(handler)) == []


def test_declares_that_it_requires_a_key(load_provider) -> None:
    assert load_provider("twitter").capabilities().requires_api_key is True


# -- configured: the official API path -----------------------------------------


async def test_maps_the_official_api_response(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")

    observations = await load_provider("twitter").search(TARGET, make_ctx(handler))
    profile = next(o for o in observations if o.kind == "social_account")
    assert profile.data["platform"] == "X"
    assert profile.data["display_name"] == "Example User"
    assert profile.data["website"] == "https://example.com"
    edges = {(e.type, e.target_value) for e in profile.edges}
    assert ("MENTIONS", "alice") in edges
    assert ("USES_HASHTAG", "osint") in edges

    post = next(o for o in observations if o.kind == "post")
    assert post.url == "https://x.com/example_user/status/1700000000000000000"
    assert post.data["hashtags"] == ["osint"]
    assert post.data["mentions"] == ["bob"]


async def test_health_check_ok_when_configured(load_provider, monkeypatch) -> None:
    monkeypatch.setenv("TWITTER_BEARER_TOKEN", "test-token")
    health = await load_provider("twitter").health_check()
    assert health.state == "ok"


async def test_unknown_handle_yields_nothing(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    target = Target(type=TargetType.USERNAME, value="doesnotexist99", normalized="doesnotexist99")
    assert await load_provider("twitter").search(target, make_ctx(handler)) == []


async def test_the_bearer_token_is_never_stored_in_evidence(
    load_provider, make_ctx, monkeypatch
) -> None:
    monkeypatch.setenv("X_BEARER_TOKEN", "super-secret-token")

    observations = await load_provider("twitter").search(TARGET, make_ctx(handler))
    assert observations, "the happy path must actually produce observations to be a real test"
    for observation in observations:
        assert "super-secret-token" not in str(observation.raw)
        assert "super-secret-token" not in str(observation.data)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://x.com/example_user", "example_user"),
        ("https://twitter.com/example_user/", "example_user"),
        ("@example_user", "example_user"),
        ("example_user", "example_user"),
        ("this_handle_is_definitely_too_long_to_be_real", None),
        ("https://example.com/notx", None),
    ],
)
def test_handle_parsing(value: str, expected: str | None) -> None:
    from graphintel_plugins import social_twitter

    assert social_twitter.parse_handle(value) == expected


# -- policy ---------------------------------------------------------------------


def test_provider_contains_no_scraping_path() -> None:
    """No login-wall evasion, session reuse, or private endpoints may appear here.

    X gates this data behind auth for the web app itself and its terms forbid
    automated collection outside the API, so the only lawful route is an operator's
    own bearer token. If this test fails, someone has added a bypass — that is a
    policy violation, not a feature.
    """
    tree = ast.parse(PROVIDER_SOURCE.read_text())
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and ast.get_docstring(node) is not None:
            node.body = node.body[1:]
    body = ast.unparse(tree).lower()

    forbidden = [
        "auth_token",
        "csrf",
        "cookie",
        "captcha",
        "selenium",
        "playwright",
        "webdriver",
        "guest_token",
    ]
    present = [token for token in forbidden if token in body]
    assert not present, f"unauthenticated-access machinery found in the provider: {present}"

    import re

    for url in re.findall(r"https?://[^\s\"']+", body):
        assert "api.twitter.com" in url or "x.com" in url, url
