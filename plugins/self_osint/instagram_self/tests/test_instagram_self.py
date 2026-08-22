"""The self-audit Instagram provider: mapping, scope gating, and the no-scraping policy.

Live graph.instagram.com is not reachable from this environment (and would need a real
user's token in any case), so these run a mocked transport through the *real* SSRF-guarded
client — URL validation and redirect handling still execute on every request.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import httpx
import pytest

from app.core.enums import TargetType
from app.providers.types import Target

PROVIDER_SOURCE = Path(__file__).resolve().parents[1] / "provider.py"
TOKEN = "IGQVJXsecret-token-value"
TARGET = Target(type=TargetType.USERNAME, value="example_user", normalized="example_user")

PROFILE = {
    "user_id": "17841400000000000",
    "username": "example_user",
    "name": "Example User",
    "account_type": "BUSINESS",
    "biography": "Coffee and code. Contact hi@example.com #osint with @friend_acct",
    "website": "https://example.com",
    "profile_picture_url": "https://cdn.test/pic.jpg",
    "followers_count": 1234,
    "follows_count": 321,
    "media_count": 2,
}

MEDIA = {
    "data": [
        {
            "id": "media-1",
            "caption": "Launch day in Berlin #startup with @cofounder",
            "media_type": "IMAGE",
            "permalink": "https://www.instagram.com/p/AAA111/",
            "timestamp": "2026-05-01T10:00:00+0000",
            "like_count": 42,
            "comments_count": 2,
        },
        {
            "id": "media-2",
            "caption": "Second post",
            "media_type": "VIDEO",
            "permalink": "https://www.instagram.com/p/BBB222/",
            "timestamp": "2026-05-02T10:00:00+0000",
            "like_count": 7,
            "comments_count": 0,
        },
    ]
}

COMMENTS = {
    "data": [
        {
            "id": "c-1",
            "text": "congrats!",
            "timestamp": "2026-05-01T11:00:00+0000",
            "username": "commenter_one",
        },
        # The account replying to itself is not an interaction and must be dropped.
        {
            "id": "c-2",
            "text": "thanks all",
            "timestamp": "2026-05-01T12:00:00+0000",
            "username": "example_user",
        },
    ]
}


def make_handler(
    *, profile=PROFILE, media=MEDIA, comments=COMMENTS, status=200, record=None
):
    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(str(request.url))
        path = request.url.path
        if path.endswith("/me"):
            return httpx.Response(status, json=profile)
        if path.endswith("/me/media"):
            return httpx.Response(status, json=media)
        # Comments are per media id, as they are in the real API: only the first post
        # has any, so the test can tell one post's comments from another's.
        if path.endswith("/media-1/comments"):
            return httpx.Response(status, json=comments)
        if re.search(r"/media-\d+/comments$", path):
            return httpx.Response(status, json={"data": []})
        return httpx.Response(404)

    return handler


def config(**overrides):
    base = {
        "access_token": TOKEN,
        "graph_base": "https://graph.instagram.com/v23.0",
        "scopes": ["instagram_business_basic"],
    }
    return {**base, **overrides}


# -- collection ---------------------------------------------------------------


async def test_maps_the_authorized_profile(load_provider, make_ctx) -> None:
    provider = load_provider("instagram_self")
    observations = await provider.search(TARGET, make_ctx(make_handler(), **config()))

    profile = next(o for o in observations if o.kind == "social_account")
    assert profile.data["username"] == "example_user"
    assert profile.data["display_name"] == "Example User"
    assert profile.data["website"] == "https://example.com"
    assert profile.data["followers"] == 1234
    assert profile.data["account_type"] == "BUSINESS"
    # Marks this as the account the user authorised, which the report keys off.
    assert profile.data["authorized_self"] is True

    edges = {(e.type, e.target_value) for e in profile.edges}
    assert ("LINKS_TO", "https://example.com") in edges
    assert ("MENTIONS", "friend_acct") in edges
    assert ("USES_HASHTAG", "osint") in edges
    assert ("MENTIONS", "hi@example.com") in edges


async def test_maps_media_with_provenance(load_provider, make_ctx) -> None:
    provider = load_provider("instagram_self")
    observations = await provider.search(TARGET, make_ctx(make_handler(), **config()))

    posts = [o for o in observations if o.kind == "post"]
    assert len(posts) == 2
    first = posts[0]
    # Provenance the brief requires on every observation.
    assert first.url == "https://www.instagram.com/p/AAA111/"
    assert first.provider == "" or first.provider == "instagram_self"
    assert first.data["source"] == "instagram_login_api"
    assert first.observed_at.year == 2026  # the post's own timestamp, parsed
    assert first.data["hashtags"] == ["startup"]
    assert first.data["mentions"] == ["cofounder"]


async def test_comments_require_the_comments_scope(load_provider, make_ctx) -> None:
    """Without the scope, no comment request is made and no comment data is stored."""
    provider = load_provider("instagram_self")
    calls: list[str] = []
    observations = await provider.search(
        TARGET, make_ctx(make_handler(record=calls), **config())
    )
    assert not any("/comments" in url for url in calls)
    assert not any(o.data.get("role") == "commenter" for o in observations)


async def test_comments_are_collected_when_the_scope_is_granted(
    load_provider, make_ctx
) -> None:
    provider = load_provider("instagram_self")
    ctx = make_ctx(
        make_handler(),
        **config(scopes=["instagram_business_basic", "instagram_business_manage_comments"]),
    )
    observations = await provider.search(TARGET, ctx)

    commenters = [o for o in observations if o.data.get("role") == "commenter"]
    assert [o.data["username"] for o in commenters] == ["commenter_one"]
    assert commenters[0].data["post_url"] == "https://www.instagram.com/p/AAA111/"
    assert any(e.type == "COMMENTED_ON" for e in commenters[0].edges)


async def test_without_a_token_it_collects_nothing(load_provider, make_ctx) -> None:
    """A stray registry-wide scan must not reach Instagram at all."""
    calls: list[str] = []
    provider = load_provider("instagram_self")
    result = await provider.search(TARGET, make_ctx(make_handler(record=calls)))
    assert result == []
    assert calls == []


async def test_registry_health_is_unavailable_without_a_per_user_credential(
    load_provider,
) -> None:
    health = await load_provider("instagram_self").health_check()
    assert health.state == "unavailable"
    assert "own Instagram account" in health.detail


# -- token hygiene ------------------------------------------------------------


async def test_the_token_never_appears_in_stored_evidence(load_provider, make_ctx) -> None:
    """The strongest form of this test: serialise every observation and grep it."""
    provider = load_provider("instagram_self")
    ctx = make_ctx(
        make_handler(),
        **config(scopes=["instagram_business_basic", "instagram_business_manage_comments"]),
    )
    observations = await provider.search(TARGET, ctx)
    assert observations

    blob = json.dumps([o.model_dump(mode="json") for o in observations])
    assert TOKEN not in blob
    assert "access_token" not in blob


# -- failure modes ------------------------------------------------------------


async def test_api_error_yields_nothing_rather_than_raising(load_provider, make_ctx) -> None:
    provider = load_provider("instagram_self")
    ctx = make_ctx(make_handler(status=400), **config())
    assert await provider.search(TARGET, ctx) == []


async def test_malformed_json_does_not_crash(load_provider, make_ctx) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    provider = load_provider("instagram_self")
    assert await provider.search(TARGET, make_ctx(handler, **config())) == []


async def test_unexpected_payload_shapes_are_survivable(load_provider, make_ctx) -> None:
    """Provider output must degrade, not explode, when Meta changes a response."""
    provider = load_provider("instagram_self")
    ctx = make_ctx(
        make_handler(media={"data": ["not-a-dict", {"id": "x"}, {}]}), **config()
    )
    observations = await provider.search(TARGET, ctx)
    # Profile survives; the unusable media entries are skipped rather than mapped.
    assert [o.kind for o in observations] == ["social_account"]


async def test_media_failure_still_returns_the_profile(load_provider, make_ctx) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json=PROFILE)
        return httpx.Response(500)

    provider = load_provider("instagram_self")
    observations = await provider.search(TARGET, make_ctx(handler, **config()))
    assert len(observations) == 1
    assert observations[0].kind == "social_account"


# -- policy -------------------------------------------------------------------


def test_provider_contains_no_scraping_or_bypass_path() -> None:
    """If this fails, someone added a route around Instagram's access controls."""
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
        "cookie",
        "captcha",
        "selenium",
        "playwright",
        "webdriver",
        "x-ig-app-id",
        "www.instagram.com/api",
        "i.instagram.com",
    ]
    present = [token for token in forbidden if token in body]
    assert not present, f"bypass machinery found in the provider: {present}"


def test_provider_only_talks_to_the_official_graph_host() -> None:
    body = PROVIDER_SOURCE.read_text()
    for url in re.findall(r"https?://[^\s\"')]+", body):
        assert (
            "graph.instagram.com" in url or "www.instagram.com/" in url
        ), f"unexpected host: {url}"


@pytest.mark.parametrize(
    ("value", "expected_year"),
    [("2026-05-01T10:00:00+0000", 2026), ("2026-05-01T10:00:00Z", 2026), ("nonsense", None)],
)
def test_timestamp_parsing(value: str, expected_year: int | None) -> None:
    from graphintel_plugins import self_osint_instagram_self as module

    parsed = module._parse_time(value)
    assert (parsed.year if parsed else None) == expected_year
