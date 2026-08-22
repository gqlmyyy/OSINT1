"""What the Instagram Login API does and does not expose about your own account.

This table is the feature's honesty mechanism. A self-audit that silently renders an
empty "Followers" section teaches the user something false — that nobody follows them,
or that we checked and found nothing. In reality Meta simply does not return follower
*lists* to this API at all.

So every section of the report resolves to one of:

``AVAILABLE``            the API returned this, and we have it
``REQUIRES_PERMISSION``  the API offers it, but the user did not grant the scope
``NOT_AVAILABLE``        the official API does not expose it to any caller, at any scope
``NOT_EXPOSED``          the API offers it and the account simply has nothing there
``EXTERNAL_SOURCE``      not from Instagram at all — found by public-source providers

``NOT_AVAILABLE`` entries are the ones that matter most: each is a place where a scraper
would go and this platform will not. They are stated as a limitation of the lawful route,
with the reason, rather than quietly omitted.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import Availability

#: Scope granting profile + media reads under Instagram Login.
SCOPE_BASIC = "instagram_business_basic"
#: Scope granting reads of comments on the user's own media.
SCOPE_COMMENTS = "instagram_business_manage_comments"
SCOPE_INSIGHTS = "instagram_business_manage_insights"
SCOPE_MESSAGES = "instagram_business_manage_messages"


@dataclass(frozen=True)
class Capability:
    """One row of the exposure matrix shown in the UI."""

    key: str
    label: str
    #: Availability when every scope this needs has been granted.
    availability: Availability
    #: Scope required, when the limitation is consent rather than the API itself.
    requires_scope: str | None = None
    #: Why, in plain language. Shown verbatim next to the badge.
    note: str = ""

    def resolve(self, granted: set[str]) -> Availability:
        """Availability for a specific connection's granted scopes."""
        if self.requires_scope and self.requires_scope not in granted:
            return Availability.REQUIRES_PERMISSION
        return self.availability


#: Ordered so the UI can render it as-is.
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="profile",
        label="Profile: username, name, biography, website, account type",
        availability=Availability.AVAILABLE,
        requires_scope=SCOPE_BASIC,
        note="Returned by the /me endpoint for the account you authorised.",
    ),
    Capability(
        key="profile_picture",
        label="Profile picture URL",
        availability=Availability.AVAILABLE,
        requires_scope=SCOPE_BASIC,
        note="A public CDN URL for your avatar.",
    ),
    Capability(
        key="counts",
        label="Follower, following and media counts",
        availability=Availability.AVAILABLE,
        requires_scope=SCOPE_BASIC,
        note="Aggregate numbers only.",
    ),
    Capability(
        key="media",
        label="Your media: captions, permalinks, timestamps, type",
        availability=Availability.AVAILABLE,
        requires_scope=SCOPE_BASIC,
        note="The posts on the account you authorised.",
    ),
    Capability(
        key="media_engagement",
        label="Like and comment counts on your media",
        availability=Availability.AVAILABLE,
        requires_scope=SCOPE_BASIC,
        note="Counts per post, not the identities behind them.",
    ),
    Capability(
        key="comments",
        label="Comments left on your media",
        availability=Availability.AVAILABLE,
        requires_scope=SCOPE_COMMENTS,
        note=(
            "Reading commenter usernames and text needs the comments permission. Without "
            "it this section stays empty and no comment data is collected."
        ),
    ),
    Capability(
        key="follower_list",
        label="The list of accounts that follow you",
        availability=Availability.NOT_AVAILABLE,
        note=(
            "Meta exposes follower and following *counts* but never the lists, to any "
            "caller at any permission level. Obtaining them would require scraping, "
            "which this platform does not do."
        ),
    ),
    Capability(
        key="following_list",
        label="The list of accounts you follow",
        availability=Availability.NOT_AVAILABLE,
        note="Same limitation as the follower list: counts only, never the list itself.",
    ),
    Capability(
        key="profile_viewers",
        label="Who viewed your profile",
        availability=Availability.NOT_AVAILABLE,
        note=(
            "Instagram does not collect or expose this for personal profiles, and no API "
            "returns it. Any tool claiming otherwise is fabricating it."
        ),
    ),
    Capability(
        key="email_phone",
        label="Your account's registered email address and phone number",
        availability=Availability.NOT_AVAILABLE,
        note=(
            "The Instagram Login API does not return the account holder's contact "
            "details. Any contact address in this report came from your public bio or "
            "an external public source, never from Instagram's API."
        ),
    ),
    Capability(
        key="location_history",
        label="Location history / places you have posted from",
        availability=Availability.NOT_AVAILABLE,
        note=(
            "Media location tagging is not returned by the Instagram Login API. Location "
            "findings in this report come from text you published, not from GPS data."
        ),
    ),
    Capability(
        key="direct_messages",
        label="Direct messages",
        availability=Availability.NOT_AVAILABLE,
        note=(
            "Out of scope for a privacy audit by deliberate choice. The messaging API "
            "exists for businesses replying to their own inbox; this feature never "
            "requests it and never reads private conversations."
        ),
    ),
    Capability(
        key="stories",
        label="Stories and story viewers",
        availability=Availability.NOT_AVAILABLE,
        note=(
            "Story insights are limited and viewer identities are never exposed by the "
            "API."
        ),
    ),
    Capability(
        key="other_accounts",
        label="Any Instagram account other than the one you authorised",
        availability=Availability.NOT_AVAILABLE,
        note=(
            "Connecting your account grants this application access to your account "
            "only. It confers no ability to read anyone else's Instagram data."
        ),
    ),
)

CAPABILITIES_BY_KEY: dict[str, Capability] = {c.key: c for c in CAPABILITIES}


def resolve_all(granted_scopes: list[str] | set[str] | None) -> list[dict[str, str]]:
    """Render the matrix for one connection's granted scopes."""
    granted = set(granted_scopes or [])
    return [
        {
            "key": c.key,
            "label": c.label,
            "availability": str(c.resolve(granted)),
            "note": c.note,
            "requires_scope": c.requires_scope or "",
        }
        for c in CAPABILITIES
    ]
