"""Image geolocation: GPS EXIF extraction from a publicly reachable image.

This is not a discovery provider in the usual sense — it takes an image URL surfaced by
another provider (a Gravatar, an avatar, a photo linked from a bio or post) and asks one
question of it: does this file carry GPS EXIF, and if so, where?

Most large platforms strip EXIF on upload, and the result says so explicitly rather than
presenting an empty answer as if the check had failed — an unqualified "no data" reads
as "the tool couldn't find it" when the true story is "the platform removed it before we
ever saw the file."

No facial or landmark recognition is performed here or anywhere in this codebase — see
README "Legal and ethical use". What this provider reports is metadata the file already
carried, nothing inferred from its visual content.
"""

from __future__ import annotations

from app.core.enums import Assertion, MatchStrength, ProviderType, TargetType
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.types import (
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)
from app.security.ssrf import ResponseTooLarge
from app.social.exif import UnsafeImage, extract_gps_exif

#: A capture more than this many years old is still reported, but the confidence
#: reflects that a photo's age tells us nothing about whether the subject is still
#: associated with that location today.
MAX_FETCH_BYTES = 12 * 1024 * 1024


class ImageGeoProvider(OSINTProvider):
    name = "image_geo"
    provider_type = ProviderType.IMAGE

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.URL],
            emits=["image"],
            requires_api_key=False,
            rate_limit=ProviderRateLimit(rpm=30, concurrency=3, timeout_seconds=20),
            reliability=0.97,  # EXIF GPS, when present, is a direct camera-written fact
            cost="free",
            recursive=False,  # an image never derives new targets on its own
            cache_ttl_seconds=7 * 24 * 3600,  # a file's EXIF does not change
            description=(
                "Reads GPS and capture metadata from an image's own EXIF data. Most "
                "platforms strip this on upload; that is reported explicitly, not as "
                "an empty or failed result."
            ),
        )

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.ok(self.name, "local EXIF parsing, no external service")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        url = target.normalized
        try:
            response = await ctx.http.get(url)
        except ResponseTooLarge:
            # The SSRF guard's own byte cap (HTTP_MAX_BYTES, 4MB by default) trips
            # before this provider's own ceiling ever gets a chance to run. Either way
            # the operator sees one explained observation, not a raw job failure.
            return [self._unavailable(url, "image exceeds the configured download size limit")]
        if response.status_code != 200:
            return []
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            return []
        data = response.content
        if len(data) > MAX_FETCH_BYTES:
            # Defense in depth: reachable only if an operator raises HTTP_MAX_BYTES
            # above this provider's own ceiling.
            return [self._unavailable(url, f"image exceeds the {MAX_FETCH_BYTES} byte fetch cap")]

        try:
            result = extract_gps_exif(data)
        except UnsafeImage as exc:
            return [self._unavailable(url, str(exc))]

        from app.evidence.normalizer import sha256_hex

        digest = sha256_hex(data)
        base_data = {
            "sha256": digest,
            "source_url": url,
            "width": result.width,
            "height": result.height,
            **result.as_dict(),
        }

        if not result.has_exif:
            base_data["note"] = (
                "No EXIF data at all in this file. Most social platforms strip EXIF "
                "on upload — this is expected, not a failure of the check."
            )
        elif not result.has_gps:
            base_data["note"] = (
                "EXIF metadata is present but carries no GPS tags. The device either "
                "had location services off, or the platform removed GPS specifically "
                "while keeping other EXIF fields."
            )

        return [
            self.observation(
                kind="image",
                value=url,
                url=url,
                label=f"Image ({digest[:12]}…)",
                match=MatchStrength.EXACT_ID if result.has_gps else MatchStrength.WEAK_HEURISTIC,
                # GPS EXIF is a fact the camera wrote into the file: observed. The
                # *absence* of GPS is also directly observed, not inferred — we saw
                # the file and it has none.
                assertion=Assertion.OBSERVED,
                confidence=0.97 if result.has_gps else 0.6,
                data=base_data,
                raw={"content_type": content_type, "bytes": len(data)},
                excerpt=(
                    f"GPS EXIF present: {result.coordinates.latitude:.5f}, "
                    f"{result.coordinates.longitude:.5f}"
                    if result.has_gps and result.coordinates
                    else base_data.get("note", "")
                ),
            )
        ]

    def _unavailable(self, url: str, reason: str) -> Observation:
        return self.observation(
            kind="image",
            value=url,
            url=url,
            label="Image (could not be safely analyzed)",
            match=MatchStrength.WEAK_HEURISTIC,
            assertion=Assertion.UNVERIFIED,
            confidence=0.1,
            data={"source_url": url, "has_exif": False, "has_gps": False, "error": reason},
            excerpt=reason,
        )

PROVIDERS = [ImageGeoProvider]
