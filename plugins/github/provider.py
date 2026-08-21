"""GitHub public API provider.

Uses only unauthenticated-accessible public endpoints (a token, when configured, simply
raises the rate limit). No private repository or private profile data is requested.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import Assertion, MatchStrength, ProviderType, TargetType
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.types import (
    EdgeHint,
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)

API = "https://api.github.com"


class GitHubProvider(OSINTProvider):
    name = "github"
    provider_type = ProviderType.REPOSITORY

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME, TargetType.EMAIL],
            emits=["social_account", "repository", "website", "organization", "location"],
            requires_api_key=False,
            rate_limit=ProviderRateLimit(rpm=30, concurrency=3, timeout_seconds=20),
            reliability=0.95,
            cost="free",
            description="Public GitHub profiles, repositories and declared links.",
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name, "public API, no credentials required")

    def _headers(self, ctx: ProviderContext) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = ctx.get("api_key") or ctx.settings.github_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        if target.type is TargetType.EMAIL:
            return await self._by_email(target, ctx)
        return await self._by_username(target, ctx)

    async def _by_username(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        handle = self.safe_value(target.normalized)
        response = await ctx.http.get(f"{API}/users/{handle}", headers=self._headers(ctx))
        if response.status_code == 404:
            return []
        response.raise_for_status()
        user: dict[str, Any] = response.json()
        if user.get("type") not in ("User", "Organization"):
            return []

        edges: list[EdgeHint] = []
        derived: list[tuple[str, str]] = []

        blog = (user.get("blog") or "").strip()
        if blog:
            edges.append(
                EdgeHint(
                    type="LINKS_TO",
                    target_kind="website",
                    target_value=blog,
                    why="The public GitHub profile declares this website.",
                )
            )
            derived.append(("domain", blog))

        public_email = (user.get("email") or "").strip()
        if public_email:
            edges.append(
                EdgeHint(
                    type="ASSOCIATED_WITH",
                    target_kind="email",
                    target_value=public_email,
                    why="Address published on the public GitHub profile.",
                )
            )
            derived.append(("email", public_email))

        company = (user.get("company") or "").strip().lstrip("@")
        if company:
            edges.append(
                EdgeHint(
                    type="ASSOCIATED_WITH",
                    target_kind="organization",
                    target_value=company,
                    why="Company field on the public GitHub profile.",
                )
            )

        location = (user.get("location") or "").strip()
        if location:
            edges.append(
                EdgeHint(
                    type="ASSOCIATED_WITH",
                    target_kind="location",
                    target_value=location,
                    why="Location field on the public GitHub profile.",
                )
            )

        observations = [
            self.observation(
                kind="social_account",
                value=str(user.get("login") or handle),
                url=str(user.get("html_url") or f"https://github.com/{handle}"),
                label=f"GitHub/{user.get('login', handle)}",
                match=MatchStrength.EXACT_ID,
                data={
                    "platform": "GitHub",
                    "username": user.get("login"),
                    "display_name": user.get("name"),
                    "bio": user.get("bio"),
                    "website": blog or None,
                    "email": public_email or None,
                    "company": company or None,
                    "location": location or None,
                    "avatar": user.get("avatar_url"),
                    "public_repos": user.get("public_repos"),
                    "followers": user.get("followers"),
                    "created_at": user.get("created_at"),
                    "external_id": user.get("id"),
                },
                raw=user,
                excerpt=str(user.get("bio") or "")[:400],
                edges=edges,
                derived_targets=derived,
            )
        ]
        observations.extend(await self._repositories(handle, ctx))
        return observations

    async def _repositories(self, handle: str, ctx: ProviderContext) -> list[Observation]:
        response = await ctx.http.get(
            f"{API}/users/{handle}/repos?per_page=30&sort=updated&type=owner",
            headers=self._headers(ctx),
        )
        if response.status_code != 200:
            return []
        out: list[Observation] = []
        for repo in response.json():
            if repo.get("fork"):
                continue
            homepage = (repo.get("homepage") or "").strip()
            edges = (
                [
                    EdgeHint(
                        type="LINKS_TO",
                        target_kind="url",
                        target_value=homepage,
                        why="Repository homepage field points at this page.",
                    )
                ]
                if homepage.startswith(("http://", "https://"))
                else []
            )
            out.append(
                self.observation(
                    kind="repository",
                    value=str(repo.get("full_name")),
                    url=str(repo.get("html_url")),
                    match=MatchStrength.EXACT_ID,
                    data={
                        "platform": "github",
                        "full_name": repo.get("full_name"),
                        "language": repo.get("language"),
                        "stars": repo.get("stargazers_count"),
                        "homepage": homepage or None,
                        "updated_at": repo.get("updated_at"),
                    },
                    raw=repo,
                    excerpt=str(repo.get("description") or "")[:400],
                    edges=edges,
                    derived_targets=[("domain", homepage)] if homepage else [],
                )
            )
        return out

    async def _by_email(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        """Public commit-search only. Reports `unverified` — GitHub search is fuzzy."""
        query = self.safe_value(target.normalized)
        response = await ctx.http.get(
            f"{API}/search/users?q={query}+in:email&per_page=5", headers=self._headers(ctx)
        )
        if response.status_code != 200:
            return []
        out: list[Observation] = []
        for item in response.json().get("items", [])[:5]:
            out.append(
                self.observation(
                    kind="social_account",
                    value=str(item.get("login")),
                    url=str(item.get("html_url")),
                    label=f"GitHub/{item.get('login')}",
                    match=MatchStrength.WEAK_HEURISTIC,
                    assertion=Assertion.UNVERIFIED,
                    data={
                        "platform": "GitHub",
                        "username": item.get("login"),
                        "matched_by": "public user search on email",
                    },
                    raw=item,
                    excerpt="GitHub public user search matched this address; not corroborated.",
                )
            )
        return out


PROVIDERS = [GitHubProvider]
