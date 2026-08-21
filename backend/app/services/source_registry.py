"""Source registry: reconciles discovered plugins with operator-editable DB state."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import HealthState
from app.models import Source
from app.providers.registry import ProviderRegistry, get_registry

#: Providers whose credential lives in settings rather than in the source config.
_SETTINGS_KEYS = {"github": "github_token", "search": "searx_base_url"}


class SourceRegistryService:
    def __init__(self, session: AsyncSession, registry: ProviderRegistry | None = None) -> None:
        self.session = session
        self.registry = registry or get_registry()

    async def sync(self) -> list[Source]:
        """Ensure every discovered provider has a row, then apply DB state to the registry."""
        rows = {
            row.name: row
            for row in (await self.session.execute(select(Source))).scalars()
        }
        result: list[Source] = []
        for entry in self.registry.all():
            caps = entry.capabilities
            row = rows.get(entry.name)
            if row is None:
                row = Source(
                    name=entry.name,
                    type=str(entry.provider.provider_type),
                    enabled=True,
                    requires_api_key=caps.requires_api_key,
                    rpm=caps.rate_limit.rpm,
                    concurrency=caps.rate_limit.concurrency,
                    timeout_seconds=caps.rate_limit.timeout_seconds,
                    reliability=caps.reliability,
                    cost=caps.cost,
                    coverage={"accepts": list(caps.accepts), "emits": list(caps.emits)},
                    config={},
                )
                self.session.add(row)
            else:
                row.type = str(entry.provider.provider_type)
                row.requires_api_key = caps.requires_api_key
                row.coverage = {"accepts": list(caps.accepts), "emits": list(caps.emits)}
            self.registry.set_enabled(entry.name, row.enabled)
            self.registry.set_config(entry.name, dict(row.config))
            result.append(row)
        await self.session.flush()
        return result

    async def update(self, name: str, changes: dict[str, Any]) -> Source:
        row = (
            await self.session.execute(select(Source).where(Source.name == name))
        ).scalar_one_or_none()
        if row is None:
            raise LookupError(f"unknown source: {name}")
        for field in ("enabled", "rpm", "concurrency", "timeout_seconds"):
            if changes.get(field) is not None:
                setattr(row, field, changes[field])
        if changes.get("config") is not None:
            row.config = changes["config"]
        await self.session.flush()
        self.registry.set_enabled(name, row.enabled)
        self.registry.set_config(name, dict(row.config))
        return row

    async def listing(self) -> list[dict[str, Any]]:
        await self.sync()
        rows = {
            row.name: row for row in (await self.session.execute(select(Source))).scalars()
        }
        health = {h.name: h for h in await self.registry.health()}
        settings = get_settings()

        out: list[dict[str, Any]] = []
        for entry in self.registry.all():
            row = rows[entry.name]
            caps = entry.capabilities
            state = health.get(entry.name)
            configured = not caps.requires_api_key
            key_name = _SETTINGS_KEYS.get(entry.name)
            if caps.requires_api_key:
                configured = bool(
                    (key_name and getattr(settings, key_name, None)) or row.config.get("api_key")
                )
            out.append(
                {
                    "name": entry.name,
                    "type": str(entry.provider.provider_type),
                    "enabled": row.enabled,
                    "requires_api_key": caps.requires_api_key,
                    "configured": configured,
                    "rate_limit": {
                        "rpm": row.rpm,
                        "concurrency": row.concurrency,
                        "timeout_seconds": row.timeout_seconds,
                    },
                    "reliability": caps.reliability,
                    "cost": caps.cost,
                    # Config is echoed with any secret masked (threat model: data protection).
                    "coverage": {**row.coverage, "config": _mask(row.config)},
                    "accepts": [str(a) for a in caps.accepts],
                    "emits": list(caps.emits),
                    "recursive": caps.recursive,
                    "description": caps.description,
                    "health": str(state.state) if state else str(HealthState.UNAVAILABLE),
                    "health_detail": state.detail if state else "not checked",
                }
            )
        return out


def _mask(config: dict[str, Any]) -> dict[str, Any]:
    masked: dict[str, Any] = {}
    for key, value in config.items():
        if any(token in key.lower() for token in ("key", "token", "secret", "password")):
            masked[key] = "********" if value else ""
        else:
            masked[key] = value
    return masked
