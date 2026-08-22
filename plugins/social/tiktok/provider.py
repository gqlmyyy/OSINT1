"""TikTok: declared only, until an official API credential is configured.

TikTok's public web pages are rendered client-side behind bot-detection that this
project will not attempt to defeat, and unauthenticated scraping of them sits outside
what TikTok's terms permit. TikTok's official Research API and Display API are the only
lawful route to this data, and neither is wired up here yet — this provider exists so
that TikTok appears, honestly, as ``Requires API`` in the Sources tab and the platform
dashboard, rather than being silently absent. There is no scraping path, and none will be
added without that official credential.

Configure with the source's ``access_token`` config, or ``TIKTOK_ACCESS_TOKEN`` in the
environment, once official API access is wired in; until then this stays unavailable.
"""

from __future__ import annotations

import os

from app.core.enums import ProviderType, TargetType
from app.providers.base import ProviderContext
from app.providers.social import SocialProvider
from app.providers.types import (
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)

UNCONFIGURED_DETAIL = (
    "TikTok's public pages are not readable without a browser session, and this "
    "platform does not work around access controls. Only TikTok's official Research "
    "API / Display API is supported, and it is not yet wired up here. Set "
    "TIKTOK_ACCESS_TOKEN once you have applied for and received that access."
)


class TikTokProvider(SocialProvider):
    name = "tiktok"
    platform = "TikTok"
    provider_type = ProviderType.SOCIAL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME, TargetType.URL],
            emits=["social_account", "post", "hashtag"],
            requires_api_key=True,
            rate_limit=ProviderRateLimit(rpm=10, concurrency=1, timeout_seconds=20),
            reliability=0.9,
            cost="free",
            description=(
                "Public TikTok profiles and posts via TikTok's official Research/"
                "Display API. Not yet configured — declared here so TikTok shows as "
                "'Requires API' rather than being silently missing."
            ),
        )

    @staticmethod
    def _token(ctx: ProviderContext | None = None) -> str | None:
        token = ctx.get("access_token") if ctx is not None else None
        return str(token) if token else os.environ.get("TIKTOK_ACCESS_TOKEN")

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.unavailable(self.name, UNCONFIGURED_DETAIL)

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        return []


PROVIDERS = [TikTokProvider]
