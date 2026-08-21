"""Instagram, via official APIs only.

**Read this before extending it.**

Instagram gates profiles, posts and comments behind authentication for anonymous
clients, and its Terms of Use prohibit automated collection. Under this project's own
rules (docs/06 threat model, README "Scope and limits", brief sections 2 and 29) that
makes unauthenticated collection off-limits: a provider that cannot lawfully reach a
source reports ``unavailable`` and stops.

So there is deliberately **no scraping path in this file at all** — no login-wall
evasion, no session cookies, no private or undocumented endpoints, no CAPTCHA handling.
There is no flag to turn any of that on, because none of it is written. A future
contributor tempted to add it should read this paragraph as the answer.

What the provider does support is the **official** route: an operator who has their own
Instagram Graph API credentials (their own Business/Creator account, or a user who has
granted their app access) configures them, and the provider then reads exactly what
Meta's API returns. That is the only way this data is lawfully available in bulk, and it
puts the consent question where it belongs — with the operator and the platform.

Configure with either the source's ``access_token``/``business_account_id`` config, or
``INSTAGRAM_ACCESS_TOKEN``/``INSTAGRAM_BUSINESS_ACCOUNT_ID`` in the environment.
"""

from __future__ import annotations

import os
import re
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

GRAPH_API = "https://graph.facebook.com/v21.0"
PROFILE_URL_RE = re.compile(r"^https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]{1,30})/?", re.I)

UNCONFIGURED_DETAIL = (
    "Instagram requires an official API credential. Anonymous access to profiles, posts "
    "and comments is gated by Instagram and prohibited by its terms, and this platform "
    "does not work around access controls. Set INSTAGRAM_ACCESS_TOKEN and "
    "INSTAGRAM_BUSINESS_ACCOUNT_ID (Instagram Graph API) to enable this provider."
)


def parse_profile_url(value: str) -> str | None:
    """``instagram.com/example`` -> ``example``; ignores reserved paths."""
    match = PROFILE_URL_RE.match(value.strip())
    if not match:
        return None
    handle = match.group(1).lower()
    reserved = {"p", "reel", "reels", "explore", "stories", "accounts", "directory", "about"}
    return None if handle in reserved else handle


class InstagramProvider(SocialProvider):
    name = "instagram"
    platform = "Instagram"
    provider_type = ProviderType.SOCIAL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME, TargetType.URL],
            emits=["social_account", "post", "hashtag", "website", "url"],
            # The honest declaration: without a credential this provider does nothing.
            requires_api_key=True,
            rate_limit=ProviderRateLimit(rpm=20, concurrency=2, timeout_seconds=30),
            reliability=0.95,
            cost="free",
            description=(
                "Public Instagram profile and media via the official Graph API. Requires "
                "your own API credential; performs no unauthenticated collection."
            ),
        )

    # -- credentials -----------------------------------------------------------

    @staticmethod
    def _credentials(ctx: ProviderContext | None = None) -> tuple[str | None, str | None]:
        token = business_id = None
        if ctx is not None:
            token = ctx.get("access_token")
            business_id = ctx.get("business_account_id")
        token = token or os.environ.get("INSTAGRAM_ACCESS_TOKEN")
        business_id = business_id or os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID")
        return (str(token) if token else None, str(business_id) if business_id else None)

    async def health_check(self) -> ProviderHealth:
        token, business_id = self._credentials()
        if not token or not business_id:
            # Not "error": nothing is broken. The source is simply not lawfully
            # reachable without a credential, and the operator is told exactly why.
            return ProviderHealth.unavailable(self.name, UNCONFIGURED_DETAIL)
        return ProviderHealth.ok(self.name, "Graph API credential configured")

    # -- collection ------------------------------------------------------------

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        token, business_id = self._credentials(ctx)
        if not token or not business_id:
            return []

        username = (
            parse_profile_url(target.normalized)
            if target.type is TargetType.URL
            else target.normalized.lstrip("@")
        )
        if not username:
            return []
        self.safe_value(username)

        # business_discovery is the documented endpoint for reading another public
        # Business/Creator profile. It returns only what Instagram chooses to expose.
        fields = (
            "business_discovery.username("
            + username
            + "){username,name,biography,website,followers_count,media_count,profile_picture_url,"
            + f"media.limit({MAX_POSTS})"
            + "{id,caption,permalink,timestamp,media_type,like_count,comments_count}}"
        )
        response = await ctx.http.get(
            f"{GRAPH_API}/{business_id}?fields={fields}&access_token={token}"
        )
        if response.status_code != 200:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []

        discovery = (payload or {}).get("business_discovery")
        if not isinstance(discovery, dict):
            return []
        return self._map(discovery, username)

    def _map(self, discovery: dict[str, Any], username: str) -> list[Observation]:
        handle = str(discovery.get("username") or username)
        observations = [
            self.profile_observation(
                username=handle,
                url=f"https://www.instagram.com/{handle}/",
                display_name=str(discovery.get("name") or "") or None,
                bio=str(discovery.get("biography") or "") or None,
                website=str(discovery.get("website") or "") or None,
                avatar=str(discovery.get("profile_picture_url") or "") or None,
                # The credential is never echoed back into stored evidence.
                raw={k: v for k, v in discovery.items() if k != "media"},
                extra={
                    "followers": discovery.get("followers_count"),
                    "media_count": discovery.get("media_count"),
                    "source": "instagram_graph_api",
                },
            )
        ]

        media = (discovery.get("media") or {}).get("data") or []
        for item in media[:MAX_POSTS]:
            if not isinstance(item, dict) or not item.get("permalink"):
                continue
            observations.append(
                self.post_observation(
                    author=handle,
                    post_id=str(item.get("id")),
                    url=str(item["permalink"]),
                    caption=str(item.get("caption") or "") or None,
                    posted_at=None,
                    raw=item,
                    extra={
                        "media_type": item.get("media_type"),
                        "likes": item.get("like_count"),
                        "comments_count": item.get("comments_count"),
                        "timestamp": item.get("timestamp"),
                        "source": "instagram_graph_api",
                    },
                )
            )
        return observations


PROVIDERS = [InstagramProvider]
