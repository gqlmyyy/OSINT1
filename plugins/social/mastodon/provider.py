"""Mastodon: profiles, public posts, replies, mentions and hashtags.

Mastodon publishes a documented, unauthenticated REST API for exactly the data this
layer needs, and its terms permit reading it. That makes it the reference implementation
for the social pipeline: every stage — profile, posts, captions, replies, mentions,
hashtags, interaction counting — is exercised end to end against a real source.

Only public data is read. Followers-only and direct posts are never requested, and the
provider does not authenticate, so it cannot see them even in principle.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

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

DEFAULT_INSTANCE = "mastodon.social"
#: Mastodon serves post bodies as HTML; strip tags to get the text the author wrote.
TAG_RE = re.compile(r"<[^>]+>")
BREAK_RE = re.compile(r"</p><p>|<br\s*/?>", re.IGNORECASE)


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    return TAG_RE.sub("", BREAK_RE.sub("\n", html)).replace("&amp;", "&").strip()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class MastodonProvider(SocialProvider):
    name = "mastodon"
    platform = "Mastodon"
    provider_type = ProviderType.SOCIAL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME, TargetType.URL],
            emits=["social_account", "post", "hashtag", "website", "email", "url", "location"],
            requires_api_key=False,
            rate_limit=ProviderRateLimit(rpm=30, concurrency=3, timeout_seconds=30),
            reliability=0.93,
            cost="free",
            description=(
                "Public Mastodon profiles, posts, replies, mentions and hashtags via the "
                "instance's documented unauthenticated API."
            ),
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name, "public REST API, no credentials required")

    def _instance(self, ctx: ProviderContext) -> str:
        return str(ctx.get("instance") or DEFAULT_INSTANCE).strip().lower()

    @staticmethod
    def _split_handle(value: str) -> tuple[str, str | None]:
        """``@user@instance.tld`` / ``user@instance.tld`` / ``user`` -> (user, instance)."""
        handle = value.strip().lstrip("@")
        if handle.count("@") == 1:
            user, _, host = handle.partition("@")
            return user, host or None
        return handle, None

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        if target.type is TargetType.URL:
            parsed = self._from_url(target.normalized)
            if parsed is None:
                return []
            username, instance = parsed
        else:
            username, instance = self._split_handle(target.normalized)
        instance = instance or self._instance(ctx)

        self.safe_value(username)
        self.safe_value(instance)
        base = f"https://{instance}/api/v1"

        account = await self._fetch_account(base, username, ctx)
        if account is None:
            return []

        observations = [self._profile(account, instance)]
        statuses = await self._fetch_statuses(base, str(account["id"]), ctx)
        for status in statuses:
            observations.extend(await self._ingest_status(base, status, username, instance, ctx))
        return observations

    # -- fetching --------------------------------------------------------------

    @staticmethod
    def _from_url(url: str) -> tuple[str, str] | None:
        match = re.match(r"https?://([^/]+)/@([A-Za-z0-9_.]+)", url)
        return (match.group(2), match.group(1)) if match else None

    async def _fetch_account(
        self, base: str, username: str, ctx: ProviderContext
    ) -> dict[str, Any] | None:
        response = await ctx.http.get(f"{base}/accounts/lookup?acct={username}")
        if response.status_code != 200:
            return None
        try:
            account = response.json()
        except ValueError:
            return None
        return account if isinstance(account, dict) and account.get("id") else None

    async def _fetch_statuses(
        self, base: str, account_id: str, ctx: ProviderContext
    ) -> list[dict[str, Any]]:
        response = await ctx.http.get(
            f"{base}/accounts/{account_id}/statuses"
            f"?limit={MAX_POSTS}&exclude_reblogs=true&exclude_replies=false"
        )
        if response.status_code != 200:
            return []
        try:
            statuses = response.json()
        except ValueError:
            return []
        return [s for s in statuses if isinstance(s, dict)][:MAX_POSTS] if isinstance(
            statuses, list
        ) else []

    async def _fetch_replies(
        self, base: str, status_id: str, ctx: ProviderContext
    ) -> list[dict[str, Any]]:
        """Public replies to a post, which is Mastodon's equivalent of comments."""
        response = await ctx.http.get(f"{base}/statuses/{status_id}/context")
        if response.status_code != 200:
            return []
        try:
            context = response.json()
        except ValueError:
            return []
        descendants = context.get("descendants", []) if isinstance(context, dict) else []
        return [d for d in descendants if isinstance(d, dict)][:MAX_COMMENTS_PER_POST]

    # -- mapping ---------------------------------------------------------------

    def _profile(self, account: dict[str, Any], instance: str) -> Observation:
        fields = account.get("fields") or []
        website = None
        for field in fields:
            value = html_to_text(str(field.get("value", "")))
            if value.startswith("http"):
                website = value
                break

        return self.profile_observation(
            username=str(account.get("acct") or account.get("username")),
            url=str(account.get("url") or f"https://{instance}/@{account.get('username')}"),
            display_name=str(account.get("display_name") or "") or None,
            bio=html_to_text(account.get("note")),
            website=website,
            avatar=str(account.get("avatar_static") or account.get("avatar") or "") or None,
            raw=account,
            extra={
                "instance": instance,
                "followers": account.get("followers_count"),
                "following": account.get("following_count"),
                "statuses": account.get("statuses_count"),
                "created_at": account.get("created_at"),
                "external_id": account.get("id"),
                "bot": account.get("bot"),
            },
            observed_at=parse_time(account.get("created_at")) or datetime.now(tz=UTC),
        )

    async def _ingest_status(
        self,
        base: str,
        status: dict[str, Any],
        author: str,
        instance: str,
        ctx: ProviderContext,
    ) -> list[Observation]:
        # Only genuinely public posts. Anything scoped narrower is skipped outright.
        if status.get("visibility") not in ("public", "unlisted"):
            return []

        status_id = str(status.get("id"))
        url = str(status.get("url") or f"https://{instance}/@{author}/{status_id}")
        caption = html_to_text(status.get("content"))
        posted_at = parse_time(status.get("created_at"))

        observations = [
            self.post_observation(
                author=author,
                post_id=status_id,
                url=url,
                caption=caption,
                posted_at=posted_at,
                raw={k: v for k, v in status.items() if k not in ("account", "media_attachments")},
                extra={
                    "instance": instance,
                    "replies": status.get("replies_count"),
                    "reblogs": status.get("reblogs_count"),
                    "favourites": status.get("favourites_count"),
                    "language": status.get("language"),
                    "visibility": status.get("visibility"),
                },
            )
        ]

        if int(status.get("replies_count") or 0) > 0:
            for reply in await self._fetch_replies(base, status_id, ctx):
                if reply.get("visibility") not in ("public", "unlisted"):
                    continue
                account = reply.get("account") or {}
                handle = str(account.get("acct") or "").strip()
                if not handle or handle == author:
                    continue
                observations.append(
                    self.comment_observation(
                        author=handle,
                        text=html_to_text(reply.get("content")),
                        post_key=status_id,
                        post_url=url,
                        comment_id=str(reply.get("id")),
                        url=str(reply.get("url") or url),
                        commented_at=parse_time(reply.get("created_at")),
                        raw={
                            "id": reply.get("id"),
                            "acct": handle,
                            "created_at": reply.get("created_at"),
                            "in_reply_to_id": reply.get("in_reply_to_id"),
                        },
                    )
                )
        return observations


PROVIDERS = [MastodonProvider]
