"""Optional adapter for the Maigret CLI.

Maigret is not vendored: if the binary is present the adapter shells out to it (argv
only, no shell) and translates its JSON report into observations. If it is absent the
provider reports ``unavailable`` and the orchestrator plans no jobs for it.

Maigret's value here is breadth of username enumeration plus the identifiers it extracts
from the profiles it finds — those become new targets for recursive discovery.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import Assertion, MatchStrength, ProviderType, TargetType
from app.providers.base import OSINTProvider, ProviderContext
from app.providers.external_tool import ToolUnavailable, find_binary, run_tool
from app.providers.types import (
    EdgeHint,
    Observation,
    ProviderCapabilities,
    ProviderHealth,
    ProviderRateLimit,
    Target,
)

#: Maigret "ids" keys worth re-feeding as targets.
DERIVABLE = {
    "email": "email",
    "username": "username",
    "url": "url",
    "website": "domain",
    "fullname": "full_name",
}


class MaigretProvider(OSINTProvider):
    name = "maigret"
    provider_type = ProviderType.USERNAME

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            accepts=[TargetType.USERNAME],
            emits=["social_account", "url", "email", "username"],
            rate_limit=ProviderRateLimit(rpm=4, concurrency=1, timeout_seconds=300, max_retries=0),
            reliability=0.85,
            cost="free",
            description="Optional Maigret CLI adapter (install `maigret` to enable).",
        )

    async def health_check(self) -> ProviderHealth:
        from app.core.config import get_settings

        binary = get_settings().maigret_binary
        if find_binary(binary) is None:
            return ProviderHealth.unavailable(
                self.name, f"`{binary}` not found on PATH — `pip install maigret` to enable"
            )
        return ProviderHealth.ok(self.name, f"using {find_binary(binary)}")

    async def search(self, target: Target, ctx: ProviderContext) -> list[Observation]:
        handle = self.safe_value(target.normalized)
        binary = str(ctx.get("binary") or ctx.settings.maigret_binary)
        top_sites = int(ctx.get("top_sites") or 300)

        try:
            result = await run_tool(
                binary,
                [handle, "--json", "simple", "--folderoutput", ".", "--no-progressbar",
                 "--timeout", "10", "--top-sites", str(top_sites)],
                timeout=ctx.settings.external_tool_timeout_seconds,
            )
        except ToolUnavailable:
            return []

        payload = result.json_payload()
        reports: list[dict[str, Any]] = [
            r for r in result.files if isinstance(r, dict)
        ]
        if isinstance(payload, dict):
            reports.append(payload)
        if not reports:
            return []

        observations: list[Observation] = []
        seen: set[str] = set()
        for report in reports:
            for site_name, entry in report.items():
                if not isinstance(entry, dict):
                    continue
                observation = self._to_observation(site_name, entry, handle, seen)
                if observation is not None:
                    observations.append(observation)
        return observations

    def _to_observation(
        self, site_name: str, entry: dict[str, Any], handle: str, seen: set[str]
    ) -> Observation | None:
        status = entry.get("status")
        status_text = ""
        if isinstance(status, dict):
            status_text = str(status.get("status", "")).lower()
        elif isinstance(status, str):
            status_text = status.lower()
        if "claimed" not in status_text:
            return None

        url = str(entry.get("url_user") or entry.get("url") or "")
        if not url.startswith(("http://", "https://")) or url in seen:
            return None
        seen.add(url)

        ids: dict[str, Any] = {}
        if isinstance(status, dict) and isinstance(status.get("ids"), dict):
            ids = status["ids"]

        edges: list[EdgeHint] = []
        derived: list[tuple[str, str]] = []
        for key, value in ids.items():
            target_kind = DERIVABLE.get(key.lower())
            if not target_kind or not isinstance(value, str) or not value.strip():
                continue
            if target_kind in ("email", "url"):
                edges.append(
                    EdgeHint(
                        type="MENTIONS" if target_kind == "email" else "LINKS_TO",
                        target_kind=target_kind,
                        target_value=value.strip(),
                        why=f"Maigret extracted this {key} from the public {site_name} profile.",
                    )
                )
            derived.append((target_kind, value.strip()))

        return self.observation(
            kind="social_account",
            value=str(ids.get("username") or handle),
            url=url,
            label=f"{site_name}/{ids.get('username') or handle}",
            match=MatchStrength.CLAIMED_LINK,
            assertion=Assertion.OBSERVED,
            data={
                "platform": site_name,
                "username": ids.get("username") or handle,
                "display_name": ids.get("fullname"),
                "bio": ids.get("bio") or ids.get("about"),
                "email": ids.get("email"),
                "website": ids.get("website"),
                "tool": "maigret",
            },
            raw={"site": site_name, **entry},
            excerpt=f"Maigret reports a claimed profile on {site_name}.",
            edges=edges,
            derived_targets=derived,
        )


PROVIDERS = [MaigretProvider]
