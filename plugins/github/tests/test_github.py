from __future__ import annotations

import httpx

from app.core.enums import Assertion, TargetType
from app.providers.types import Target

USER = Target(type=TargetType.USERNAME, value="example_user", normalized="example_user")

PROFILE = {
    "login": "example_user", "id": 4242, "type": "User", "name": "Example User",
    "bio": "Backend engineer", "blog": "https://example.com", "email": "e@example.com",
    "company": "@ExampleLabs", "location": "Berlin", "avatar_url": "https://avatars/1",
    "html_url": "https://github.com/example_user", "public_repos": 3, "followers": 10,
    "created_at": "2019-01-02T03:04:05Z",
}
REPOS = [
    {"full_name": "example_user/graph", "html_url": "https://github.com/example_user/graph",
     "fork": False, "language": "Python", "stargazers_count": 12,
     "homepage": "https://example.com/graph", "description": "graphs",
     "updated_at": "2026-01-01T00:00:00Z"},
    {"full_name": "example_user/forked", "html_url": "https://github.com/example_user/forked",
     "fork": True, "language": "Go", "stargazers_count": 0, "homepage": "", "description": ""},
]


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/users/example_user":
        return httpx.Response(200, json=PROFILE)
    if path == "/users/example_user/repos":
        return httpx.Response(200, json=REPOS)
    if path == "/users/ghost":
        return httpx.Response(404, json={"message": "Not Found"})
    return httpx.Response(404, json={})


async def test_extracts_profile_links_and_repositories(load_provider, make_ctx) -> None:
    provider = load_provider("github")
    observations = await provider.search(USER, make_ctx(handler))

    account = next(o for o in observations if o.kind == "social_account")
    assert account.data["platform"] == "GitHub"
    assert account.data["website"] == "https://example.com"
    assert account.url == "https://github.com/example_user"
    assert account.assertion is Assertion.OBSERVED
    assert account.raw["id"] == 4242, "the raw API response is kept as evidence"

    edge_kinds = {(e.type, e.target_kind) for e in account.edges}
    assert ("LINKS_TO", "website") in edge_kinds
    assert ("ASSOCIATED_WITH", "email") in edge_kinds
    assert ("ASSOCIATED_WITH", "organization") in edge_kinds
    assert dict(account.derived_targets or []) or account.derived_targets

    repos = [o for o in observations if o.kind == "repository"]
    assert [r.value for r in repos] == ["example_user/graph"], "forks are excluded"
    assert repos[0].data["homepage"] == "https://example.com/graph"


async def test_missing_user_yields_nothing(load_provider, make_ctx) -> None:
    provider = load_provider("github")
    target = Target(type=TargetType.USERNAME, value="ghost", normalized="ghost")
    assert await provider.search(target, make_ctx(handler)) == []


async def test_email_search_results_are_marked_unverified(load_provider, make_ctx) -> None:
    def search_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/users":
            return httpx.Response(
                200,
                json={"items": [{"login": "someone", "html_url": "https://github.com/someone"}]},
            )
        return httpx.Response(404, json={})

    provider = load_provider("github")
    target = Target(type=TargetType.EMAIL, value="e@example.com", normalized="e@example.com")
    observations = await provider.search(target, make_ctx(search_handler))
    assert observations
    assert all(o.assertion is Assertion.UNVERIFIED for o in observations)


async def test_token_is_sent_when_configured(load_provider, make_ctx) -> None:
    seen: list[str | None] = []

    def auth_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json=PROFILE if request.url.path.endswith("example_user") else [])

    provider = load_provider("github")
    await provider.search(USER, make_ctx(auth_handler, api_key="ghp_test_token"))
    assert seen and seen[0] == "Bearer ghp_test_token"
