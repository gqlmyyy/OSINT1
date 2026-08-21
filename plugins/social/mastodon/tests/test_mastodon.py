from __future__ import annotations

import httpx
import pytest

from app.core.enums import TargetType
from app.providers.types import Target

TARGET = Target(type=TargetType.USERNAME, value="gargron", normalized="gargron")

ACCOUNT = {
    "id": "1",
    "username": "gargron",
    "acct": "gargron",
    "display_name": "Eugen",
    "note": "<p>Founder. Reach me at @alice — building #fediverse stuff</p>",
    "url": "https://mastodon.social/@gargron",
    "avatar_static": "https://files.test/avatar.png",
    "followers_count": 300000,
    "statuses_count": 70000,
    "created_at": "2016-03-16T00:00:00.000Z",
    "fields": [{"name": "Site", "value": '<a href="https://example.com">https://example.com</a>'}],
}

STATUSES = [
    {
        "id": "100",
        "url": "https://mastodon.social/@gargron/100",
        "content": "<p>Shipping #opensource today, thanks @bob https://example.com/blog</p>",
        "created_at": "2026-05-01T10:00:00.000Z",
        "visibility": "public",
        "replies_count": 2,
        "favourites_count": 12,
    },
    {
        "id": "101",
        "url": "https://mastodon.social/@gargron/101",
        "content": "<p>A private note</p>",
        "created_at": "2026-05-02T10:00:00.000Z",
        # Not public: must never be collected even though the API returned it.
        "visibility": "private",
        "replies_count": 0,
    },
]

CONTEXT = {
    "descendants": [
        {
            "id": "200",
            "url": "https://mastodon.social/@carol/200",
            "content": "<p>congrats!</p>",
            "created_at": "2026-05-01T11:00:00.000Z",
            "visibility": "public",
            "account": {"acct": "carol"},
        },
        {
            "id": "201",
            "content": "<p>hidden</p>",
            "visibility": "direct",
            "account": {"acct": "dave"},
        },
    ]
}


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/accounts/lookup"):
        return httpx.Response(200, json=ACCOUNT)
    if path.endswith("/accounts/1/statuses"):
        return httpx.Response(200, json=STATUSES)
    if path.endswith("/statuses/100/context"):
        return httpx.Response(200, json=CONTEXT)
    return httpx.Response(404, json={})


async def test_builds_profile_posts_and_comments(load_provider, make_ctx) -> None:
    provider = load_provider("mastodon")
    observations = await provider.search(TARGET, make_ctx(handler))

    profile = next(o for o in observations if o.data.get("role") != "commenter" and o.kind == "social_account")
    assert profile.data["display_name"] == "Eugen"
    assert profile.data["website"] == "https://example.com"
    assert "Founder" in profile.data["bio"], "HTML must be reduced to the text the author wrote"

    # The bio's handle and hashtag become pivots.
    edges = {(e.type, e.target_kind, e.target_value) for e in profile.edges}
    assert ("MENTIONS", "social_account", "alice") in edges
    assert ("USES_HASHTAG", "hashtag", "fediverse") in edges
    assert ("LINKS_TO", "website", "https://example.com") in edges

    posts = [o for o in observations if o.kind == "post"]
    assert [p.data["post_id"] for p in posts] == ["100"], "non-public posts must be skipped"
    assert posts[0].data["hashtags"] == ["opensource"]
    assert posts[0].data["mentions"] == ["bob"]

    comments = [o for o in observations if o.data.get("role") == "commenter"]
    assert [c.data["username"] for c in comments] == ["carol"], "direct replies must be skipped"
    assert comments[0].data["post_id"] == "100"
    assert any(e.type == "COMMENTED_ON" for e in comments[0].edges)


async def test_authored_edge_points_from_account_to_post(load_provider, make_ctx) -> None:
    provider = load_provider("mastodon")
    posts = [o for o in await provider.search(TARGET, make_ctx(handler)) if o.kind == "post"]
    authored = next(e for e in posts[0].edges if e.type == "AUTHORED")
    assert authored.reverse is True, "an account authors a post, not the other way round"


async def test_unknown_account_yields_nothing(load_provider, make_ctx) -> None:
    provider = load_provider("mastodon")
    assert await provider.search(TARGET, make_ctx(lambda r: httpx.Response(404))) == []


async def test_malformed_payloads_do_not_raise(load_provider, make_ctx) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts/lookup"):
            return httpx.Response(200, json=ACCOUNT)
        return httpx.Response(200, text="not json at all")

    provider = load_provider("mastodon")
    observations = await provider.search(TARGET, make_ctx(broken))
    assert len(observations) == 1, "the profile survives; the unparseable rest is dropped"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://mastodon.social/@gargron", ("gargron", "mastodon.social")),
        ("https://fosstodon.org/@someone/123", ("someone", "fosstodon.org")),
        ("https://example.com/notmastodon", None),
    ],
)
def test_profile_urls_are_parsed(load_provider, url: str, expected) -> None:
    assert load_provider("mastodon")._from_url(url) == expected


def test_handle_forms(load_provider) -> None:
    provider = load_provider("mastodon")
    assert provider._split_handle("@user@instance.tld") == ("user", "instance.tld")
    assert provider._split_handle("user") == ("user", None)
