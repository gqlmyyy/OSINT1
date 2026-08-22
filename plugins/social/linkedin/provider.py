"""LinkedIn: declared only, until an official API credential is configured.

LinkedIn's public profile pages sit behind a login wall for almost all practical
browsing, and LinkedIn's terms explicitly prohibit automated scraping — including of
whatever fragments render for logged-out visitors. This project does not defeat login
walls or scrape against a platform's terms, so the only lawful route to this data is
LinkedIn's own Partner APIs, which require an approved partnership and are not wired up
here. This provider exists so that LinkedIn appears, honestly, as ``Requires API`` in the
Sources tab and the platform dashboard, rather than being silently absent. There is no
scraping path, and none will be added without that official access.

Configure with the source's ``access_token`` config, or ``LINKEDIN_ACCESS_TOKEN`` in the
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
    "LinkedIn's public pages sit behind a login wall and its terms prohibit automated "
    "scraping, so this platform does not attempt it. Only LinkedIn's official Partner "
    "APIs are supported, and access to them is not yet wired up here. Set "
    "LINKEDIN_ACCESS_TOKEN once you have an approved partnership."
)


class LinkedInProvider(SocialProvider):
    name = "linkedin"
    platform = "LinkedIn"
    provider_type = ProviderType.SOCIAL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME, TargetType.URL],
            emits=["social_account", "post"],
            requires_api_key=True,
            rate_limit=ProviderRateLimit(rpm=10, concurrency=1, timeout_seconds=20),
            reliability=0.9,
            cost="free",
            description=(
                "Public LinkedIn pages via LinkedIn's official Partner APIs. Not yet "
                "configured — declared here so LinkedIn shows as 'Requires API' rather "
                "than being silently missing."
            ),
        )

    @staticmethod
    def _token(ctx: ProviderContext | None = None) -> str | None:
        token = ctx.get("access_token") if ctx is not None else None
        return str(token) if token else os.environ.get("LINKEDIN_ACCESS_TOKEN")

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth.unavailable(self.name, UNCONFIGURED_DETAIL)

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        return []


PROVIDERS = [LinkedInProvider]
