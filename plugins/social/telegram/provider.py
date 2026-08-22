"""Telegram: public channels, via Telegram's own public preview page.

``t.me/s/<channel>`` is served by Telegram itself, without login, specifically so a
public channel's content can be previewed and embedded outside the app — the same
mechanism behind Telegram's "view in Telegram" web widgets. It is not a third-party
mirror and not a workaround: it is the page Telegram serves to anyone who asks for a
public channel by name.

What this provider does **not** do, because the brief and this project's own rules
forbid it: no joining a channel, no reading private or invite-only channels or groups
(they are not reachable at this URL and this provider makes no attempt to reach them by
another route), and no login. Group chats and channel *discussion* threads (which are
regular Telegram groups, not the public preview) are out of scope for the same reason —
they usually require membership to read, and this provider does not attempt to bypass
that.
"""

from __future__ import annotations

import html
import re
from datetime import datetime

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

CHANNEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
URL_CHANNEL_RE = re.compile(r"^https?://t\.me/(?:s/)?([A-Za-z][A-Za-z0-9_]{4,31})/?", re.I)

TITLE_RE = re.compile(
    r'tgme_channel_info_header_title[^>]*>\s*<span[^>]*>(.*?)</span>', re.DOTALL
)
DESC_RE = re.compile(r'tgme_channel_info_description">(.*?)</div>', re.DOTALL)
IMAGE_RE = re.compile(r'tgme_page_photo_image.*?<img[^>]*src="([^"]+)"', re.DOTALL)
SUBSCRIBERS_RE = re.compile(
    r'counter_value">([\d.,\s]+[KM]?)</span>\s*<span class="counter_type">subscribers'
)

#: Marks the start of each message block; the block itself runs until the next one
#: starts (or the document ends), which is more robust than trying to match an entire
#: message's nested markup in one expression.
MESSAGE_START_RE = re.compile(r'data-post="([^"]+)"')
MESSAGE_TEXT_RE = re.compile(r'tgme_widget_message_text[^>]*>(.*?)</div>', re.DOTALL)
MESSAGE_TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(fragment: str | None) -> str:
    if not fragment:
        return ""
    return html.unescape(TAG_RE.sub("", fragment)).strip()


def parse_channel(value: str) -> str | None:
    """``t.me/channel`` / ``@channel`` / ``channel`` -> the bare channel name."""
    match = URL_CHANNEL_RE.match(value.strip())
    if match:
        return match.group(1)
    candidate = value.strip().lstrip("@")
    return candidate if CHANNEL_RE.match(candidate) else None


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class TelegramProvider(SocialProvider):
    name = "telegram"
    platform = "Telegram"
    provider_type = ProviderType.SOCIAL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME, TargetType.URL],
            emits=["social_account", "post", "hashtag", "url"],
            requires_api_key=False,
            rate_limit=ProviderRateLimit(rpm=20, concurrency=2, timeout_seconds=20),
            reliability=0.85,  # HTML parsing of a page not meant as a formal API
            cost="free",
            description=(
                "Public Telegram channels via Telegram's own public preview page "
                "(t.me/s/<channel>). Private channels, groups and discussions are not "
                "reachable through this provider and are never attempted."
            ),
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name, "Telegram's public channel preview, no login")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        channel = parse_channel(target.normalized)
        if not channel:
            return []
        self.safe_value(channel)

        response = await ctx.http.get(f"https://t.me/s/{channel}")
        if response.status_code != 200:
            return []
        body = response.text
        if "tgme_channel_info" not in body:
            # Not a channel page at all (e.g. a private/invite link, or it doesn't
            # exist) — Telegram redirects those elsewhere or returns a different page.
            return []

        title_match = TITLE_RE.search(body)
        desc_match = DESC_RE.search(body)
        image_match = IMAGE_RE.search(body)
        subscribers_match = SUBSCRIBERS_RE.search(body)

        observations = [
            self.profile_observation(
                username=channel,
                url=f"https://t.me/{channel}",
                display_name=html_to_text(title_match.group(1)) if title_match else channel,
                bio=html_to_text(desc_match.group(1)) if desc_match else None,
                avatar=image_match.group(1) if image_match else None,
                raw={"preview_url": f"https://t.me/s/{channel}"},
                extra={"subscribers": subscribers_match.group(1) if subscribers_match else None},
            )
        ]

        starts = list(MESSAGE_START_RE.finditer(body))
        seen_posts: set[str] = set()
        for index, start in enumerate(starts):
            post_ref = start.group(1)
            if post_ref in seen_posts or len(seen_posts) >= MAX_POSTS:
                continue
            post_channel, _, post_id = post_ref.partition("/")
            if post_channel != channel:
                continue  # a forwarded/quoted message from elsewhere, not this channel's own post
            seen_posts.add(post_ref)

            segment_end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
            segment = body[start.end() : segment_end]
            text_match = MESSAGE_TEXT_RE.search(segment)
            time_match = MESSAGE_TIME_RE.search(segment)

            observations.append(
                self.post_observation(
                    author=channel,
                    post_id=post_id,
                    url=f"https://t.me/{channel}/{post_id}",
                    caption=html_to_text(text_match.group(1)) if text_match else None,
                    posted_at=parse_time(time_match.group(1)) if time_match else None,
                    raw={"post_ref": post_ref},
                )
            )
        return observations


PROVIDERS = [TelegramProvider]
