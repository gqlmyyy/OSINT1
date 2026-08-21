"""Extraction of mentions, hashtags and links from public free text.

Shared by every social provider so a bio, a caption and a comment are all parsed the
same way — the alternative is each platform inventing its own slightly different rules
and the graph filling with near-duplicate entities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.evidence.canonical import normalize_hashtag
from app.evidence.normalizer import EMAIL_RE, normalize_username

#: Handles. Deliberately conservative: at least two characters, no trailing punctuation,
#: and an e-mail's local part is never treated as a mention (`a@b.com` is not `@b`).
MENTION_RE = re.compile(r"(?<![\w@.])@([A-Za-z0-9._]{2,30})(?![\w.@])")

#: Hashtags, including the unicode word characters used by non-Latin scripts.
HASHTAG_RE = re.compile(r"(?<![\w&])#(\w{2,60})", re.UNICODE)

URL_RE = re.compile(r"https?://[^\s<>\"'\])}]+", re.IGNORECASE)
EMAIL_IN_TEXT_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")

#: Handles that carry no intelligence value: platform boilerplate that would otherwise
#: attach to every account and pollute the graph with meaningless hub nodes.
NOISE_HANDLES = frozenset(
    {"here", "everyone", "channel", "all", "admin", "support", "info", "contact", "me"}
)

MAX_TEXT = 20_000


@dataclass
class ExtractedText:
    mentions: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.mentions or self.hashtags or self.urls or self.emails)


def extract(text: str | None) -> ExtractedText:
    """Pull the pivotable identifiers out of one piece of public text."""
    if not text:
        return ExtractedText()
    body = text[:MAX_TEXT]

    emails = sorted({m.lower() for m in EMAIL_IN_TEXT_RE.findall(body) if EMAIL_RE.match(m)})
    # Remove addresses before looking for mentions, so the domain half of an e-mail is
    # never mistaken for a handle.
    without_emails = EMAIL_IN_TEXT_RE.sub(" ", body)

    mentions = sorted(
        {
            normalize_username(handle)
            for handle in MENTION_RE.findall(without_emails)
            if normalize_username(handle) not in NOISE_HANDLES
        }
    )
    hashtags = sorted({normalize_hashtag(tag) for tag in HASHTAG_RE.findall(body)})
    urls = sorted({url.rstrip(".,);:") for url in URL_RE.findall(body)})

    return ExtractedText(mentions=mentions, hashtags=hashtags, urls=urls, emails=emails)
