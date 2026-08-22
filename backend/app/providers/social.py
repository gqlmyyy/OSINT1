"""Base class for social-platform providers.

Every platform models the same shapes — a profile, posts, comments, the handles and tags
inside their text — so the mapping from those shapes onto observations and edges lives
here once. A platform provider then only has to fetch and hand over plain dictionaries,
which is what keeps each one small enough to audit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.enums import Assertion, MatchStrength, ProviderType
from app.providers.base import OSINTProvider
from app.providers.types import EdgeHint, Observation
from app.social.text import extract

#: Caps applied by every social provider. Collecting more than this yields diminishing
#: intelligence value and disproportionate storage, and the brief's data-minimisation
#: rule (spec 30) says not to keep what you do not need.
MAX_POSTS = 40
MAX_COMMENTS_PER_POST = 60
MAX_CAPTION_CHARS = 2000
MAX_COMMENT_CHARS = 600


class SocialProvider(OSINTProvider):
    """Shared shape-mapping for platform providers.

    Subclasses implement :meth:`search` and call the ``profile_observation`` /
    ``post_observation`` / ``comment_observation`` helpers.
    """

    provider_type: ProviderType = ProviderType.SOCIAL
    #: Human-facing platform name, used in canonical keys and labels.
    platform: str = "unknown"

    # -- profile ---------------------------------------------------------------

    def profile_observation(
        self,
        *,
        username: str,
        url: str,
        display_name: str | None = None,
        bio: str | None = None,
        website: str | None = None,
        avatar: str | None = None,
        avatar_hash: str | None = None,
        #: Perceptual hash of the avatar image, when the provider actually fetched it.
        #: Most social providers only see the avatar *URL* and leave this unset; see
        #: app/identity/avatar.py for what it is used for.
        avatar_phash: str | None = None,
        location: str | None = None,
        raw: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
    ) -> Observation:
        """A public profile, plus everything pivotable inside its bio."""
        found = extract(bio)
        edges: list[EdgeHint] = []
        derived: list[tuple[str, str]] = []

        if website:
            edges.append(
                EdgeHint(
                    type="LINKS_TO",
                    target_kind="website",
                    target_value=website,
                    why=f"The public {self.platform} profile declares this website.",
                )
            )
            derived.append(("domain", website))

        for handle in found.mentions:
            edges.append(
                EdgeHint(
                    type="MENTIONS",
                    target_kind="social_account",
                    target_value=handle,
                    why=f"Mentioned in the public {self.platform} bio.",
                    target_attributes={"platform": self.platform, "username": handle},
                )
            )
            derived.append(("username", handle))

        for tag in found.hashtags:
            edges.append(
                EdgeHint(
                    type="USES_HASHTAG",
                    target_kind="hashtag",
                    target_value=tag,
                    why=f"Used in the public {self.platform} bio.",
                )
            )

        for address in found.emails:
            edges.append(
                EdgeHint(
                    type="MENTIONS",
                    target_kind="email",
                    target_value=address,
                    why=f"Published in the public {self.platform} bio.",
                )
            )
            derived.append(("email", address))

        for link in found.urls:
            derived.append(("domain", link))

        if location:
            edges.append(
                EdgeHint(
                    type="ASSOCIATED_WITH",
                    target_kind="location",
                    target_value=location,
                    why=f"Location stated on the public {self.platform} profile.",
                )
            )

        observation = self.observation(
            kind="social_account",
            value=username,
            url=url,
            label=f"{self.platform}/{username}",
            match=MatchStrength.EXACT_ID,
            data={
                "platform": self.platform,
                "username": username,
                "display_name": display_name,
                "bio": (bio or "")[:MAX_CAPTION_CHARS] or None,
                "website": website,
                "avatar": avatar,
                "avatar_hash": avatar_hash,
                "avatar_phash": avatar_phash,
                "location": location,
                **(extra or {}),
            },
            raw=raw or {},
            excerpt=(bio or "")[:400],
            edges=edges,
            derived_targets=derived,
        )
        if observed_at:
            observation.observed_at = observed_at
        return observation

    # -- posts -----------------------------------------------------------------

    def post_observation(
        self,
        *,
        author: str,
        post_id: str,
        url: str,
        caption: str | None = None,
        posted_at: datetime | None = None,
        raw: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Observation:
        """One public post, with the handles, tags and links its caption carries."""
        found = extract(caption)
        edges: list[EdgeHint] = [
            EdgeHint(
                type="AUTHORED",
                target_kind="social_account",
                target_value=author,
                why=f"Published by this account on {self.platform}.",
                target_attributes={"platform": self.platform, "username": author},
                # account --AUTHORED--> post, not the other way round.
                reverse=True,
            )
        ]
        derived: list[tuple[str, str]] = []

        for handle in found.mentions:
            edges.append(
                EdgeHint(
                    type="MENTIONS",
                    target_kind="social_account",
                    target_value=handle,
                    why="Mentioned in the public post caption.",
                    target_attributes={"platform": self.platform, "username": handle},
                )
            )
            derived.append(("username", handle))

        for tag in found.hashtags:
            edges.append(
                EdgeHint(
                    type="USES_HASHTAG",
                    target_kind="hashtag",
                    target_value=tag,
                    why="Used in the public post caption.",
                )
            )

        for link in found.urls:
            edges.append(
                EdgeHint(
                    type="LINKS_TO",
                    target_kind="url",
                    target_value=link,
                    why="Linked from the public post caption.",
                )
            )
            derived.append(("domain", link))

        observation = self.observation(
            kind="post",
            value=url,
            url=url,
            match=MatchStrength.EXACT_ID,
            data={
                "platform": self.platform,
                "post_id": post_id,
                "author": author,
                # Truncated on purpose: enough to read the finding in context, not a
                # mirror of the platform's content.
                "caption": (caption or "")[:MAX_CAPTION_CHARS] or None,
                "hashtags": found.hashtags,
                "mentions": found.mentions,
                **(extra or {}),
            },
            raw=raw or {},
            excerpt=(caption or "")[:400],
            edges=edges,
            derived_targets=derived,
        )
        if posted_at:
            observation.observed_at = posted_at
        return observation

    # -- comments --------------------------------------------------------------

    def comment_observation(
        self,
        *,
        author: str,
        text: str,
        post_key: str,
        post_url: str,
        comment_id: str | None = None,
        url: str | None = None,
        commented_at: datetime | None = None,
        raw: dict[str, Any] | None = None,
    ) -> Observation:
        """One public comment. The commenter becomes an account; the comment an edge."""
        found = extract(text)
        edges: list[EdgeHint] = [
            EdgeHint(
                type="COMMENTED_ON",
                target_kind="post",
                target_value=post_url,
                why=f"Public comment on this {self.platform} post.",
                target_attributes={"platform": self.platform, "post_id": post_key},
            )
        ]
        derived: list[tuple[str, str]] = [("username", author)]

        for handle in found.mentions:
            edges.append(
                EdgeHint(
                    type="MENTIONS",
                    target_kind="social_account",
                    target_value=handle,
                    why="Mentioned inside a public comment.",
                    target_attributes={"platform": self.platform, "username": handle},
                )
            )
            derived.append(("username", handle))

        observation = self.observation(
            kind="social_account",
            value=author,
            url=url or post_url,
            label=f"{self.platform}/{author}",
            # The commenter's account demonstrably exists — the platform served it — but
            # a comment tells us nothing about who is behind it.
            match=MatchStrength.CLAIMED_LINK,
            assertion=Assertion.OBSERVED,
            data={
                "platform": self.platform,
                "username": author,
                "comment_id": comment_id,
                "comment_text": text[:MAX_COMMENT_CHARS],
                "post_id": post_key,
                "post_url": post_url,
                "role": "commenter",
            },
            raw=raw or {},
            excerpt=text[:400],
            edges=edges,
            derived_targets=derived,
        )
        if commented_at:
            observation.observed_at = commented_at
        return observation
