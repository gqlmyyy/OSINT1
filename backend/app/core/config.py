"""Application settings.

Production safety is enforced here, not in documentation: a deployment that leaves the
default secret in place, or that enables private-network egress, refuses to boot.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
# Not a credential: the sentinel value the production validator refuses to boot with.
DEFAULT_SECRET = "change-me-in-production-change-me-in-production"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # -- runtime ---------------------------------------------------------------
    env: Literal["development", "test", "production"] = "development"
    debug: bool = False
    app_name: str = "GraphIntel OSINT"
    api_prefix: str = "/api/v1"
    user_agent: str = "GraphIntel-OSINT/0.1 (+https://github.com/gqlmyyy/OSINT1)"

    # -- storage ---------------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./graphintel.db"
    db_echo: bool = False
    redis_url: str | None = None

    # -- security --------------------------------------------------------------
    secret_key: str = DEFAULT_SECRET
    access_token_ttl_minutes: int = 60
    refresh_token_ttl_days: int = 14
    allow_registration: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    api_rate_limit_per_minute: int = 240

    # -- egress / SSRF guard ---------------------------------------------------
    allow_private_networks: bool = False
    allowed_egress_ports: list[int] = Field(default_factory=lambda: [80, 443])
    http_timeout_seconds: float = 15.0
    http_max_redirects: int = 5
    http_max_bytes: int = 4 * 1024 * 1024

    # -- providers -------------------------------------------------------------
    plugins_path: Path = REPO_ROOT / "plugins"
    provider_cache_ttl_seconds: int = 6 * 3600
    github_token: str | None = None
    searx_base_url: str | None = None
    maigret_binary: str = "maigret"
    sherlock_binary: str = "sherlock"
    holehe_binary: str = "holehe"
    external_tool_timeout_seconds: float = 180.0

    # -- investigation budgets (spec 19) ---------------------------------------
    max_depth: int = 3
    max_entities: int = 500
    max_jobs: int = 100

    # -- graph -----------------------------------------------------------------
    graph_cluster_threshold: int = 400
    graph_max_nodes: int = 5000

    # -- correlation -----------------------------------------------------------
    correlation_min_score: float = 0.5

    # -- ai (optional, spec 28-29) ---------------------------------------------
    ai_enabled: bool = False
    llm_provider: Literal["ollama", "none"] = "none"
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5"
    llm_timeout_seconds: float = 120.0

    @field_validator("cors_origins", "allowed_egress_ports", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            return parts
        return value

    @field_validator("allowed_egress_ports", mode="after")
    @classmethod
    def _ports_are_ints(cls, value: list[int]) -> list[int]:
        return [int(p) for p in value]

    @model_validator(mode="after")
    def _production_safety(self) -> Settings:
        if self.env == "production":
            if self.secret_key == DEFAULT_SECRET or len(self.secret_key) < 32:
                raise ValueError(
                    "SECRET_KEY must be set to a unique value of at least 32 characters "
                    "when ENV=production"
                )
            if self.allow_private_networks:
                raise ValueError(
                    "ALLOW_PRIVATE_NETWORKS must stay false in production: it disables the "
                    "SSRF guard's private-range protection"
                )
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS may not be '*' in production")
        return self

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "").replace("+aiosqlite", "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def new_secret() -> str:
    return secrets.token_urlsafe(48)
