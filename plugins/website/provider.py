"""URL / website intelligence: title, declared links, published contacts, technology hints.

Parsing is done with a bounded regex pass over the first N bytes rather than a full DOM
parse: the input is untrusted, and this keeps the attack surface and the memory ceiling
small. Only public pages are fetched, through the SSRF-guarded client.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from app.core.enums import Assertion, MatchStrength, ProviderType, TargetType
from app.evidence.normalizer import EMAIL_RE, normalize_domain, NormalizationError
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.types import (
    EdgeHint,
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
GENERATOR_RE = re.compile(
    r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']{1,120})["\']', re.IGNORECASE
)
DESCRIPTION_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{1,400})["\']', re.IGNORECASE
)
HREF_RE = re.compile(r'href=["\']([^"\'<>\s]{1,500})["\']', re.IGNORECASE)
MAILTO_RE = re.compile(r'mailto:([^"\'<>\s?]{3,320})', re.IGNORECASE)
EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")

#: host -> (platform, path-position of the handle)
SOCIAL_HOSTS: dict[str, str] = {
    "github.com": "GitHub",
    "gitlab.com": "GitLab",
    "twitter.com": "X",
    "x.com": "X",
    "reddit.com": "Reddit",
    "www.reddit.com": "Reddit",
    "instagram.com": "Instagram",
    "linkedin.com": "LinkedIn",
    "www.linkedin.com": "LinkedIn",
    "t.me": "Telegram",
    "telegram.me": "Telegram",
    "mastodon.social": "Mastodon",
    "youtube.com": "YouTube",
    "medium.com": "Medium",
    "keybase.io": "Keybase",
    "news.ycombinator.com": "HackerNews",
    "stackoverflow.com": "StackOverflow",
}

TECH_MARKERS: list[tuple[str, str]] = [
    ("wp-content", "WordPress"),
    ("/_next/", "Next.js"),
    ("__NUXT__", "Nuxt"),
    ("data-drupal", "Drupal"),
    ("cdn.shopify.com", "Shopify"),
    ("gatsby", "Gatsby"),
    ("hugo", "Hugo"),
    ("jekyll", "Jekyll"),
]

MAX_LINKS = 200
MAX_HTML = 512 * 1024


class WebsiteProvider(OSINTProvider):
    name = "website"
    provider_type = ProviderType.SOCIAL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.URL, TargetType.DOMAIN],
            emits=["website", "url", "email", "social_account", "technology"],
            rate_limit=ProviderRateLimit(rpm=40, concurrency=4, timeout_seconds=20),
            reliability=0.9,
            cost="free",
            description="Fetches a public page and extracts its title, links and contacts.",
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name, "SSRF-guarded fetch of public pages")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        url = (
            target.normalized
            if target.type is TargetType.URL
            else f"https://{target.normalized}"
        )
        response = await ctx.http.get(url)
        if response.status_code >= 400:
            return []

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            return []
        html = response.text[:MAX_HTML]
        final_url = str(response.url)
        try:
            host = normalize_domain(final_url)
        except NormalizationError:
            return []

        title = _first(TITLE_RE, html)
        generator = _first(GENERATOR_RE, html)
        description = _first(DESCRIPTION_RE, html)
        emails = _emails(html)
        links = _links(html, final_url)
        socials = _socials(links)
        technologies = _technologies(html, generator)

        edges: list[EdgeHint] = []
        derived: list[tuple[str, str]] = []

        for address in emails[:10]:
            edges.append(
                EdgeHint(
                    type="MENTIONS",
                    target_kind="email",
                    target_value=address,
                    why="Address published in the page source.",
                )
            )
            derived.append(("email", address))

        for platform, handle, link in socials[:25]:
            edges.append(
                EdgeHint(
                    type="LINKS_TO",
                    target_kind="social_account",
                    target_value=handle,
                    why=f"The page links to this public {platform} profile.",
                    target_attributes={"platform": platform, "username": handle, "url": link},
                )
            )
            derived.append(("username", handle))

        for tech in technologies[:10]:
            edges.append(
                EdgeHint(
                    type="ASSOCIATED_WITH",
                    target_kind="technology",
                    target_value=tech,
                    why="Detected from markup or the generator meta tag.",
                )
            )

        return [
            self.observation(
                kind="website",
                value=host,
                url=final_url,
                label=title or host,
                match=MatchStrength.EXACT_ID,
                data={
                    "title": title,
                    "description": description,
                    "generator": generator,
                    "status_code": response.status_code,
                    "final_url": final_url,
                    "emails": emails[:10],
                    "technologies": technologies[:10],
                    "outbound_links": len(links),
                },
                raw={
                    "status_code": response.status_code,
                    "headers": {
                        k: v
                        for k, v in response.headers.items()
                        if k.lower() in ("server", "content-type", "x-powered-by", "last-modified")
                    },
                    "title": title,
                    "links_sample": links[:40],
                },
                excerpt=(title or description or html[:200]).strip()[:400],
                edges=edges,
                derived_targets=derived,
            )
        ]


def _first(pattern: re.Pattern[str], html: str) -> str | None:
    match = pattern.search(html)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip()[:400] or None


def _emails(html: str) -> list[str]:
    found = {m.lower() for m in MAILTO_RE.findall(html)}
    found |= {m.lower() for m in EMAIL_IN_TEXT.findall(html)}
    return sorted(a for a in found if EMAIL_RE.match(a) and not a.endswith((".png", ".jpg")))


def _links(html: str, base: str) -> list[str]:
    out: list[str] = []
    for href in HREF_RE.findall(html)[: MAX_LINKS * 3]:
        if href.startswith(("mailto:", "javascript:", "tel:", "#", "data:")):
            continue
        absolute = href if href.startswith(("http://", "https://")) else urljoin(base, href)
        if absolute.startswith(("http://", "https://")) and absolute not in out:
            out.append(absolute)
        if len(out) >= MAX_LINKS:
            break
    return out


def _socials(links: list[str]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for link in links:
        try:
            host = normalize_domain(link)
        except NormalizationError:
            continue
        platform = SOCIAL_HOSTS.get(host) or SOCIAL_HOSTS.get(f"www.{host}")
        if not platform:
            continue
        parts = [p for p in link.split("?")[0].split("#")[0].split("/")[3:] if p]
        if not parts:
            continue
        handle = parts[-1] if parts[0] in ("user", "u", "in", "profile", "@") else parts[0]
        handle = handle.lstrip("@").strip()
        if not handle or len(handle) > 64 or handle in ("about", "login", "signup", "explore"):
            continue
        if (platform, handle.lower()) in seen:
            continue
        seen.add((platform, handle.lower()))
        out.append((platform, handle, link))
    return out


def _technologies(html: str, generator: str | None) -> list[str]:
    lowered = html.lower()
    found = {name for marker, name in TECH_MARKERS if marker.lower() in lowered}
    if generator:
        found.add(generator.split()[0])
    return sorted(found)


PROVIDERS = [WebsiteProvider]
