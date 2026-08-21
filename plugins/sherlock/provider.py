"""Optional adapter for the Sherlock CLI (argv only, never a shell)."""

from __future__ import annotations

from urllib.parse import urlsplit

from app.core.enums import Assertion, MatchStrength, ProviderType, TargetType
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.external_tool import ToolUnavailable, find_binary, run_tool
from app.providers.types import (
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)


class SherlockProvider(OSINTProvider):
    name = "sherlock"
    provider_type = ProviderType.USERNAME

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME],
            emits=["social_account"],
            rate_limit=ProviderRateLimit(rpm=4, concurrency=1, timeout_seconds=300, max_retries=0),
            reliability=0.8,
            cost="free",
            description="Optional Sherlock CLI adapter (install `sherlock-project` to enable).",
        )

    async def health_check(self) -> ProviderHealth:
        from app.core.config import get_settings

        binary = get_settings().sherlock_binary
        if find_binary(binary) is None:
            return ProviderHealth.unavailable(
                self.name,
                f"`{binary}` not found on PATH — `pip install sherlock-project` to enable",
            )
        return ProviderHealth.ok(self.name, f"using {find_binary(binary)}")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        handle = self.safe_value(target.normalized)
        binary = str(ctx.get("binary") or ctx.settings.sherlock_binary)
        try:
            result = await run_tool(
                binary,
                [handle, "--print-found", "--no-color", "--timeout", "10", "--folderoutput", "."],
                timeout=ctx.settings.external_tool_timeout_seconds,
            )
        except ToolUnavailable:
            return []

        observations: list[Observation] = []
        seen: set[str] = set()
        for line in result.stdout.splitlines():
            url = _extract_url(line)
            if not url or url in seen:
                continue
            seen.add(url)
            platform = _platform_of(line, url)
            observations.append(
                self.observation(
                    kind="social_account",
                    value=handle,
                    url=url,
                    label=f"{platform}/{handle}",
                    # Sherlock reports presence, not profile content: keep the claim modest.
                    match=MatchStrength.PATTERN_MATCH,
                    assertion=Assertion.UNVERIFIED,
                    data={"platform": platform, "username": handle, "tool": "sherlock"},
                    raw={"line": line.strip(), "url": url},
                    excerpt=f"Sherlock reported a profile page for this handle on {platform}.",
                )
            )
        return observations


def _extract_url(line: str) -> str | None:
    for token in line.split():
        if token.startswith(("http://", "https://")):
            return token.strip().rstrip(".,)")
    return None


def _platform_of(line: str, url: str) -> str:
    marker = line.split("]")[-1].split(":")[0].strip()
    if marker and len(marker) < 40 and not marker.startswith("http"):
        return marker
    host = urlsplit(url).hostname or "unknown"
    return host.removeprefix("www.").split(".")[0].title()


PROVIDERS = [SherlockProvider]
