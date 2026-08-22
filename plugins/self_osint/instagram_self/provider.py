"""Instagram self-audit: reads *your own* authorised account, and nothing else.

This is the collection half of the "Add Your Instagram Account" feature. It is a
deliberately separate provider from ``plugins/social/instagram``:

* that one uses ``business_discovery`` with an operator-wide credential to look at
  *other* public business profiles;
* this one uses a **per-user** OAuth token, obtained through Meta's Instagram Login
  flow, and calls ``/me`` — the only account it can ever read is the one whose owner
  granted consent.

Keeping them apart is what makes the boundary auditable. There is no code path here that
takes an arbitrary username and returns data: the account read is whichever account the
supplied token belongs to, as decided by Meta, not by our caller.

**The token is never a target and never persisted here.** It arrives in the provider
context for the duration of one call, is used as a request parameter, and is excluded
from every observation's ``raw`` payload. The context is built per run by
``app.selfosint.service`` from the requesting user's own encrypted credential.

No scraping path exists in this file. If a capability is not in the official API it is
reported as unavailable (see ``app.selfosint.instagram_capabilities``), never worked
around.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from app.core.enums import ProviderType, TargetType
from app.providers.base import ProviderContext
from app.providers.social import MAX_COMMENTS_PER_POST, MAX_POSTS, SocialProvider
from app.providers.types import (
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)
from app.selfosint.instagram_capabilities import SCOPE_COMMENTS
from app.selfosint.instagram_oauth import COMMENT_FIELDS, ME_FIELDS, MEDIA_FIELDS

#: How many of the account's own posts to fetch comments for. Comments are the most
#: voluminous and most personal data in the audit, so this is deliberately smaller than
#: the post limit: enough to demonstrate exposure, not a mirror of the account.
COMMENT_POST_LIMIT = 10

UNCONFIGURED_DETAIL = (
    "This provider reads a single user's own Instagram account using that user's OAuth "
    "token. It is not configured globally and is never run against arbitrary targets: it "
    "is invoked by the Self-OSINT service with the requesting user's own credential. "
    "Connect an account under Self-OSINT to use it."
)


class InstagramSelfProvider(SocialProvider):
    name = "instagram_self"
    platform = "Instagram"
    provider_type = ProviderType.SOCIAL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME],
            emits=["social_account", "post", "hashtag", "website", "url"],
            requires_api_key=True,
            rate_limit=ProviderRateLimit(rpm=20, concurrency=1, timeout_seconds=30),
            reliability=0.99,  # first-party data straight from the account owner's API
            cost="free",
            recursive=False,  # the self-audit service decides what to pivot on
            # NB: caching is switched off at the *runner* (use_cache=False in
            # app.selfosint.service), not here. The provider cache is keyed by
            # provider+target only, so two users auditing accounts with the same handle
            # would collide — authorised personal data must never share a cache entry.
            description=(
                "Reads your own Instagram account through Meta's official Instagram "
                "Login API, for a self-audit of what you are publicly exposing. Cannot "
                "read any other account."
            ),
        )

    async def health_check(self) -> ProviderHealth:
        # Deliberately never "ok" at the registry level: this provider has no global
        # credential to check. Its real availability is per user, and the Self-OSINT
        # endpoints report that.
        return ProviderHealth.unavailable(self.name, UNCONFIGURED_DETAIL)

    # -- collection ------------------------------------------------------------

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        token = ctx.get("access_token")
        if not token:
            # Without a per-user credential there is nothing to read and nothing to
            # guess at. Returning empty keeps a stray registry-wide scan inert.
            return []
        graph_base = str(ctx.get("graph_base") or "https://graph.instagram.com/v23.0")
        granted = set(ctx.get("scopes") or [])

        profile = await self._fetch_profile(graph_base, str(token), ctx)
        if profile is None:
            return []

        handle = str(profile.get("username") or target.normalized.lstrip("@"))
        observations = [self._profile_observation(profile, handle)]

        media = await self._fetch_media(graph_base, str(token), ctx)
        for item in media[:MAX_POSTS]:
            observation = self._post_observation(item, handle)
            if observation is not None:
                observations.append(observation)

        if SCOPE_COMMENTS in granted:
            observations.extend(
                await self._fetch_comments(graph_base, str(token), media, handle, ctx)
            )
        return observations

    async def _fetch_profile(
        self, graph_base: str, token: str, ctx: ProviderContext
    ) -> dict[str, Any] | None:
        query = urlencode({"fields": ME_FIELDS, "access_token": token})
        response = await ctx.http.get(f"{graph_base}/me?{query}")
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) and payload.get("username") else None

    async def _fetch_media(
        self, graph_base: str, token: str, ctx: ProviderContext
    ) -> list[dict[str, Any]]:
        query = urlencode(
            {"fields": MEDIA_FIELDS, "limit": str(MAX_POSTS), "access_token": token}
        )
        response = await ctx.http.get(f"{graph_base}/me/media?{query}")
        if response.status_code != 200:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        data = (payload or {}).get("data") if isinstance(payload, dict) else None
        return [item for item in (data or []) if isinstance(item, dict)]

    async def _fetch_comments(
        self,
        graph_base: str,
        token: str,
        media: list[dict[str, Any]],
        handle: str,
        ctx: ProviderContext,
    ) -> list[Observation]:
        observations: list[Observation] = []
        for item in media[:COMMENT_POST_LIMIT]:
            media_id = str(item.get("id") or "")
            permalink = str(item.get("permalink") or "")
            if not media_id or not permalink:
                continue
            query = urlencode({"fields": COMMENT_FIELDS, "access_token": token})
            response = await ctx.http.get(f"{graph_base}/{media_id}/comments?{query}")
            if response.status_code != 200:
                # A single post's comments being unavailable is not a run failure.
                continue
            try:
                payload = response.json()
            except ValueError:
                continue
            data = (payload or {}).get("data") if isinstance(payload, dict) else None
            for comment in (data or [])[:MAX_COMMENTS_PER_POST]:
                if not isinstance(comment, dict):
                    continue
                author = str(comment.get("username") or "").lstrip("@")
                text = str(comment.get("text") or "")
                if not author or author == handle:
                    continue  # the account replying to itself is not an interaction
                observations.append(
                    self.comment_observation(
                        author=author,
                        text=text,
                        post_key=media_id,
                        post_url=permalink,
                        comment_id=str(comment.get("id") or "") or None,
                        url=permalink,
                        commented_at=_parse_time(comment.get("timestamp")),
                        raw={"id": comment.get("id"), "timestamp": comment.get("timestamp")},
                    )
                )
        return observations

    # -- mapping ---------------------------------------------------------------

    def _profile_observation(self, profile: dict[str, Any], handle: str) -> Observation:
        return self.profile_observation(
            username=handle,
            url=f"https://www.instagram.com/{handle}/",
            display_name=str(profile.get("name") or "") or None,
            bio=str(profile.get("biography") or "") or None,
            website=str(profile.get("website") or "") or None,
            avatar=str(profile.get("profile_picture_url") or "") or None,
            # `raw` is copied into stored evidence: rebuild it field by field from the
            # documented response rather than passing the payload through, so a future
            # API change cannot start persisting something unexpected.
            raw={
                "user_id": profile.get("user_id"),
                "username": profile.get("username"),
                "account_type": profile.get("account_type"),
                "media_count": profile.get("media_count"),
            },
            extra={
                "followers": profile.get("followers_count"),
                "following": profile.get("follows_count"),
                "media_count": profile.get("media_count"),
                "account_type": profile.get("account_type"),
                "external_id": profile.get("user_id"),
                "source": "instagram_login_api",
                "authorized_self": True,
            },
        )

    def _post_observation(self, item: dict[str, Any], handle: str) -> Observation | None:
        permalink = str(item.get("permalink") or "")
        if not permalink:
            return None
        return self.post_observation(
            author=handle,
            post_id=str(item.get("id") or ""),
            url=permalink,
            caption=str(item.get("caption") or "") or None,
            posted_at=_parse_time(item.get("timestamp")),
            raw={"id": item.get("id"), "timestamp": item.get("timestamp")},
            extra={
                "media_type": item.get("media_type"),
                "likes": item.get("like_count"),
                "comments_count": item.get("comments_count"),
                "source": "instagram_login_api",
                "authorized_self": True,
            },
        )


def _parse_time(value: Any) -> datetime | None:
    """Instagram returns ISO-8601 with a ``+0000``-style offset."""
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


PROVIDERS = [InstagramSelfProvider]
