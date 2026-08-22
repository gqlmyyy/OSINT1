"""X (Twitter), via the official API v2 only.

Unauthenticated scraping of X has been effectively closed off since 2023 (the public
web app itself requires a session to browse), and X's terms prohibit automated access
outside the API. So — the same shape as the Instagram provider — this reads only
through the official API, using a bearer token the operator supplies, and reports
``unavailable`` with instructions when none is configured. There is no scraping path.

Configure with the source's ``bearer_token`` config, or ``X_BEARER_TOKEN`` /
``TWITTER_BEARER_TOKEN`` in the environment.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from app.core.enums import ProviderType, TargetType
from app.providers.base import ProviderContext
from app.providers.social import MAX_POSTS, SocialProvider
from app.providers.types import (
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)

API = "https://api.twitter.com/2"
HANDLE_URL_RE = re.compile(r"^https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/?", re.I)

UNCONFIGURED_DETAIL = (
    "X requires an official API bearer token. Unauthenticated access to the public web "
    "app itself requires a session, and this platform does not work around access "
    "controls. Set X_BEARER_TOKEN (X API v2, Essential access is sufficient for read-"
    "only profile and recent-post lookups) to enable this provider."
)


def parse_handle(value: str) -> str | None:
    match = HANDLE_URL_RE.match(value.strip())
    if match:
        return match.group(1)
    candidate = value.strip().lstrip("@")
    return candidate if re.match(r"^[A-Za-z0-9_]{1,15}$", candidate) else None


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class TwitterProvider(SocialProvider):
    name = "twitter"
    platform = "X"
    provider_type = ProviderType.SOCIAL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME, TargetType.URL],
            emits=["social_account", "post", "hashtag", "website", "url"],
            requires_api_key=True,
            rate_limit=ProviderRateLimit(rpm=15, concurrency=1, timeout_seconds=20),
            reliability=0.95,
            cost="free",
            description=(
                "Public X profile and recent posts via the official API v2. Requires "
                "your own bearer token; performs no unauthenticated collection."
            ),
        )

    @staticmethod
    def _token(ctx: ProviderContext | None = None) -> str | None:
        token = ctx.get("bearer_token") if ctx is not None else None
        return str(token) if token else (
            os.environ.get("X_BEARER_TOKEN") or os.environ.get("TWITTER_BEARER_TOKEN")
        )

    async def health_check(self) -> ProviderHealth:
        if not self._token():
            return ProviderHealth.unavailable(self.name, UNCONFIGURED_DETAIL)
        return ProviderHealth.ok(self.name, "X API v2 bearer token configured")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        token = self._token(ctx)
        if not token:
            return []

        handle = parse_handle(target.normalized)
        if not handle:
            return []
        self.safe_value(handle)
        headers = {"Authorization": f"Bearer {token}"}

        user_fields = "description,public_metrics,profile_image_url,created_at,url,location"
        response = await ctx.http.get(
            f"{API}/users/by/username/{handle}?user.fields={user_fields}", headers=headers
        )
        if response.status_code != 200:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        user = (payload or {}).get("data")
        if not isinstance(user, dict) or not user.get("id"):
            return []

        observations = [self._profile(user)]
        observations.extend(await self._posts(user, headers, ctx))
        return observations

    def _profile(self, user: dict[str, Any]) -> Observation:
        handle = str(user["username"])
        metrics = user.get("public_metrics") or {}
        return self.profile_observation(
            username=handle,
            url=f"https://x.com/{handle}",
            display_name=str(user.get("name") or "") or None,
            bio=str(user.get("description") or "") or None,
            website=str(user.get("url") or "") or None,
            avatar=str(user.get("profile_image_url") or "") or None,
            location=str(user.get("location") or "") or None,
            raw={k: v for k, v in user.items() if k != "public_metrics"},
            extra={
                "followers": metrics.get("followers_count"),
                "following": metrics.get("following_count"),
                "post_count": metrics.get("tweet_count"),
                "external_id": user.get("id"),
                "created_at": user.get("created_at"),
                "source": "x_api_v2",
            },
        )

    async def _posts(
        self, user: dict[str, Any], headers: dict[str, str], ctx: ProviderContext
    ) -> list[Observation]:
        handle = str(user["username"])
        response = await ctx.http.get(
            f"{API}/users/{user['id']}/tweets"
            f"?max_results={min(max(MAX_POSTS, 5), 100)}"
            "&tweet.fields=created_at,entities"
            "&exclude=retweets,replies",
            headers=headers,
        )
        if response.status_code != 200:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        tweets = (payload or {}).get("data") or []

        observations = []
        for tweet in tweets[:MAX_POSTS]:
            if not isinstance(tweet, dict) or not tweet.get("id"):
                continue
            observations.append(
                self.post_observation(
                    author=handle,
                    post_id=str(tweet["id"]),
                    url=f"https://x.com/{handle}/status/{tweet['id']}",
                    caption=str(tweet.get("text") or "") or None,
                    posted_at=parse_time(tweet.get("created_at")),
                    raw={"id": tweet["id"], "created_at": tweet.get("created_at")},
                    extra={"source": "x_api_v2"},
                )
            )
        return observations


PROVIDERS = [TwitterProvider]
