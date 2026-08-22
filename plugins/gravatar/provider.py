"""Gravatar provider: does a public avatar exist for this address?

Gravatar identifies accounts by the MD5 of the lowercased address — that is the service's
own published lookup scheme, not a security decision on our part. Only the public profile
endpoint is queried; the address itself is never transmitted.
"""

from __future__ import annotations

from app.core.enums import MatchStrength, ProviderType, TargetType
from app.evidence.normalizer import md5_hex, sha256_hex
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.types import (
    EdgeHint,
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)

GRAVATAR = "https://www.gravatar.com"


def _perceptual_hash(data: bytes) -> str | None:
    """Best-effort perceptual hash.

    A default or malformed avatar must not fail the whole lookup: the byte hash and the
    profile data are still worth having, so an undecodable image simply yields no
    perceptual fingerprint rather than an error.
    """
    from app.identity.avatar import UnsafeImage, perceptual_hash

    try:
        return perceptual_hash(data)
    except UnsafeImage:
        return None


class GravatarProvider(OSINTProvider):
    name = "gravatar"
    provider_type = ProviderType.IMAGE

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.EMAIL],
            emits=["avatar", "social_account", "url"],
            rate_limit=ProviderRateLimit(rpm=60, concurrency=4, timeout_seconds=15),
            reliability=0.85,
            cost="free",
            description="Public Gravatar profile and avatar for an email address.",
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name, "public profile endpoint")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        digest = md5_hex(target.normalized)
        avatar_url = f"{GRAVATAR}/avatar/{digest}?d=404"
        response = await ctx.http.get(avatar_url)
        if response.status_code != 200:
            return []

        # A stable hash of the actual image bytes: this is what SameAvatarSignal compares.
        image_hash = sha256_hex(response.content)
        # And a perceptual hash, which survives the resizing and re-compression every
        # platform applies on upload. The byte hash is exact evidence; this one is a
        # "same picture" candidate. See app/identity/avatar.py.
        image_phash = _perceptual_hash(response.content)
        observations = [
            self.observation(
                kind="avatar",
                value=f"{GRAVATAR}/avatar/{digest}",
                url=f"{GRAVATAR}/avatar/{digest}",
                match=MatchStrength.EXACT_ID,
                data={
                    "sha256": image_hash,
                    "avatar_phash": image_phash,
                    "gravatar_hash": digest,
                    "content_type": response.headers.get("content-type", ""),
                    "bytes": len(response.content),
                },
                raw={"gravatar_hash": digest, "sha256": image_hash},
                excerpt="A public Gravatar image exists for the hashed address.",
                edges=[
                    EdgeHint(
                        type="ASSOCIATED_WITH",
                        target_kind="email",
                        target_value=target.normalized,
                        why="Gravatar publishes an avatar for this address.",
                    )
                ],
            )
        ]

        profile = await ctx.http.get(f"{GRAVATAR}/{digest}.json")
        if profile.status_code == 200:
            try:
                entries = profile.json().get("entry", [])
            except ValueError:
                entries = []
            for entry in entries[:1]:
                accounts = entry.get("accounts", []) or []
                edges = [
                    EdgeHint(
                        type="LINKS_TO",
                        target_kind="url",
                        target_value=str(account.get("url")),
                        why=f"Gravatar profile links a public {account.get('shortname')} account.",
                    )
                    for account in accounts
                    if str(account.get("url", "")).startswith(("http://", "https://"))
                ]
                observations.append(
                    self.observation(
                        kind="social_account",
                        value=str(entry.get("preferredUsername") or digest),
                        url=str(entry.get("profileUrl") or f"{GRAVATAR}/{digest}"),
                        label=f"Gravatar/{entry.get('preferredUsername', digest[:8])}",
                        match=MatchStrength.CLAIMED_LINK,
                        data={
                            "platform": "Gravatar",
                            "username": entry.get("preferredUsername"),
                            "display_name": entry.get("displayName"),
                            "avatar_hash": image_hash,
                            "avatar_phash": image_phash,
                            "bio": (entry.get("aboutMe") or None),
                        },
                        raw=entry,
                        excerpt=str(entry.get("aboutMe") or "")[:400],
                        edges=edges,
                    )
                )
        return observations


PROVIDERS = [GravatarProvider]
