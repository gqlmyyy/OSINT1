"""Instagram provider: correct behaviour when unconfigured, and no scraping path.

The second half of this file is a policy test. It exists so that if someone later adds
login-wall evasion to this provider, the suite fails and says why.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import httpx
import pytest

from app.core.enums import TargetType
from app.providers.types import Target

PROVIDER_SOURCE = Path(__file__).resolve().parents[1] / "provider.py"
TARGET = Target(type=TargetType.USERNAME, value="example_user", normalized="example_user")

DISCOVERY = {
    "business_discovery": {
        "username": "example_user",
        "name": "Example User",
        "biography": "Building things with @alice #opensource",
        "website": "https://example.com",
        "followers_count": 1200,
        "media_count": 2,
        "profile_picture_url": "https://cdn.test/a.jpg",
        "media": {
            "data": [
                {
                    "id": "1",
                    "caption": "new project #osint with @bob https://example.com/x",
                    "permalink": "https://www.instagram.com/p/AAA/",
                    "timestamp": "2026-05-01T00:00:00+0000",
                    "media_type": "IMAGE",
                    "like_count": 40,
                    "comments_count": 3,
                }
            ]
        },
    }
}


def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=DISCOVERY)


# -- unconfigured is the default and must be honest ---------------------------


async def test_reports_unavailable_without_a_credential(load_provider, monkeypatch) -> None:
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", raising=False)

    health = await load_provider("instagram").health_check()
    assert health.state == "unavailable"
    # The operator must learn what to do, not just that it failed.
    assert "INSTAGRAM_ACCESS_TOKEN" in health.detail
    assert "does not work around access controls" in health.detail


async def test_collects_nothing_without_a_credential(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", raising=False)
    assert await load_provider("instagram").search(TARGET, make_ctx(handler)) == []


def test_declares_that_it_requires_a_key(load_provider) -> None:
    assert load_provider("instagram").capabilities().requires_api_key is True


# -- configured: the official API path ----------------------------------------


async def test_maps_the_graph_api_response(load_provider, make_ctx, monkeypatch) -> None:
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "17841400000000000")

    observations = await load_provider("instagram").search(TARGET, make_ctx(handler))
    profile = next(o for o in observations if o.kind == "social_account")
    assert profile.data["platform"] == "Instagram"
    assert profile.data["website"] == "https://example.com"
    edges = {(e.type, e.target_value) for e in profile.edges}
    assert ("MENTIONS", "alice") in edges
    assert ("USES_HASHTAG", "opensource") in edges

    post = next(o for o in observations if o.kind == "post")
    assert post.url == "https://www.instagram.com/p/AAA/"
    assert post.data["hashtags"] == ["osint"]
    assert post.data["mentions"] == ["bob"]


async def test_the_access_token_is_never_stored_in_evidence(
    load_provider, make_ctx, monkeypatch
) -> None:
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "super-secret-token")
    monkeypatch.setenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "17841400000000000")

    observations = await load_provider("instagram").search(TARGET, make_ctx(handler))
    for observation in observations:
        assert "super-secret-token" not in str(observation.raw)
        assert "super-secret-token" not in str(observation.data)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.instagram.com/example_user/", "example_user"),
        ("https://instagram.com/Example.User", "example.user"),
        ("https://www.instagram.com/p/ABC123/", None),
        ("https://www.instagram.com/explore/", None),
    ],
)
def test_profile_url_parsing(url: str, expected: str | None) -> None:
    import importlib

    module = importlib.import_module("graphintel_plugins.social_instagram")
    assert module.parse_profile_url(url) == expected


# -- policy ------------------------------------------------------------------


def test_provider_contains_no_scraping_path() -> None:
    """No login-wall evasion, session reuse, or private endpoints may appear here.

    Instagram gates this data and its terms forbid automated collection, so the only
    lawful route is an operator's own API credential. If this test fails, someone has
    added a bypass — that is a policy violation, not a feature.
    """
    # Scan executable code only. The module docstring names these very terms in order
    # to explain why they are absent, so matching prose would be self-defeating.
    tree = ast.parse(PROVIDER_SOURCE.read_text())
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and ast.get_docstring(node) is not None:
            node.body = node.body[1:]
    body = ast.unparse(tree).lower()

    forbidden = [
        "sessionid",
        "csrftoken",
        "x-ig-app-id",
        "__a=1",
        "cookie",
        "captcha",
        "selenium",
        "playwright",
        "webdriver",
    ]
    present = [token for token in forbidden if token in body]
    assert not present, f"unauthenticated-access machinery found in the provider: {present}"

    # Every outbound call must go to Meta's documented API host.
    for url in re.findall(r"https?://[^\s\"']+", body):
        assert "graph.facebook.com" in url or "instagram.com" in url, url
