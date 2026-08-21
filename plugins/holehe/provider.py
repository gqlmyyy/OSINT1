"""Optional adapter for the Holehe CLI.

Holehe asks public *registration* endpoints whether an address is already in use — the
same answer a signup form gives any visitor. It performs no login attempt and recovers no
private data. Results are recorded as `unverified`: "an account exists here" is a claim by
the remote site, not something we independently observed.
"""

from __future__ import annotations

import re

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

ANSI = re.compile(r"\x1b\[[0-9;]*m")
FOUND = re.compile(r"^\s*\[\+\]\s*([A-Za-z0-9 ._\-]+)")


class HoleheProvider(OSINTProvider):
    name = "holehe"
    provider_type = ProviderType.EMAIL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.EMAIL],
            emits=["social_account"],
            rate_limit=ProviderRateLimit(rpm=3, concurrency=1, timeout_seconds=300, max_retries=0),
            reliability=0.7,
            cost="free",
            recursive=False,
            description="Optional Holehe CLI adapter (install `holehe` to enable).",
        )

    async def health_check(self) -> ProviderHealth:
        from app.core.config import get_settings

        binary = get_settings().holehe_binary
        if find_binary(binary) is None:
            return ProviderHealth.unavailable(
                self.name, f"`{binary}` not found on PATH — `pip install holehe` to enable"
            )
        return ProviderHealth.ok(self.name, f"using {find_binary(binary)}")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        address = self.safe_value(target.normalized)
        binary = str(ctx.get("binary") or ctx.settings.holehe_binary)
        try:
            result = await run_tool(
                binary,
                [address, "--only-used", "--no-color"],
                timeout=ctx.settings.external_tool_timeout_seconds,
            )
        except ToolUnavailable:
            return []

        observations: list[Observation] = []
        seen: set[str] = set()
        for raw_line in result.stdout.splitlines():
            line = ANSI.sub("", raw_line)
            match = FOUND.match(line)
            if not match:
                continue
            platform = match.group(1).strip()
            if not platform or platform.lower() in seen:
                continue
            seen.add(platform.lower())
            observations.append(
                self.observation(
                    kind="social_account",
                    value=f"{address}@{platform.lower()}",
                    label=f"{platform} (account exists)",
                    match=MatchStrength.WEAK_HEURISTIC,
                    assertion=Assertion.UNVERIFIED,
                    data={
                        "platform": platform,
                        "username": address,
                        "email": address,
                        "tool": "holehe",
                        "basis": "public registration endpoint reports the address is in use",
                    },
                    raw={"line": line.strip()},
                    excerpt=(
                        f"{platform}'s public signup check reports this address is already "
                        "registered. No profile content was retrieved."
                    ),
                )
            )
        return observations


PROVIDERS = [HoleheProvider]
