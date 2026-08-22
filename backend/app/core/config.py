"""Application settings.

Production safety is enforced here, not in documentation: a deployment that leaves the
default secret in place, or that enables private-network egress, refuses to boot.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    # NoDecode keeps pydantic-settings from running json.loads() on the raw value before
    # our validator sees it. Without it a comma-separated CORS_ORIGINS -- the format
    # .env.example and docker-compose.yml both use -- raises SettingsError at startup.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    api_rate_limit_per_minute: int = 240
    #: Url-safe base64 of exactly 32 bytes. Optional: when unset, the data-encryption key
    #: is derived from SECRET_KEY via HKDF under a purpose-specific info string (see
    #: app/security/crypto.py). Set it explicitly to rotate credential encryption
    #: independently of JWT signing.
    token_encryption_key: str | None = None

    # -- egress / SSRF guard ---------------------------------------------------
    allow_private_networks: bool = False
    allowed_egress_ports: Annotated[list[int], NoDecode] = Field(
        default_factory=lambda: [80, 443]
    )
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

    # -- self-OSINT: Instagram Login OAuth (per user, never a shared account) ---
    # These identify *your Meta app*, not any account: each user authorises their own
    # Instagram account through it and gets their own token. See README "Self-OSINT".
    instagram_app_id: str | None = None
    instagram_app_secret: str | None = None
    #: Must exactly match a redirect URI registered on the Meta app.
    instagram_redirect_uri: str = "http://localhost:8080/self-osint/instagram/callback"
    #: Hosts and version are configurable because Meta moves both; pinning them in code
    #: would make a documented API change into a code change.
    instagram_oauth_authorize_url: str = "https://www.instagram.com/oauth/authorize"
    # The suppression below is because this is the public token *endpoint* URL, which
    # the linter flags on the "token" in its name; it is not a secret.
    instagram_oauth_token_url: str = "https://api.instagram.com/oauth/access_token"  # noqa: S105
    instagram_graph_host: str = "https://graph.instagram.com"
    instagram_api_version: str = "v23.0"
    #: Instagram Login scopes. `instagram_business_basic` covers profile + media;
    #: comments need `instagram_business_manage_comments`. Basic Display's old
    #: user_profile/user_media scopes are dead (API shut down 2024-12-04).
    instagram_scopes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["instagram_business_basic"]
    )
    #: OAuth authorisation requests expire quickly; a state that outlives its flow is a
    #: replay window.
    oauth_state_ttl_seconds: int = 600
    #: Manual re-sync floor, per user. Instagram is not polled continuously.
    self_osint_sync_cooldown_seconds: int = 120

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

    @field_validator("cors_origins", "allowed_egress_ports", "instagram_scopes", mode="before")
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
    def instagram_oauth_configured(self) -> bool:
        """Whether an operator has supplied Meta app credentials for the OAuth flow."""
        return bool(self.instagram_app_id and self.instagram_app_secret)

    @property
    def instagram_graph_base(self) -> str:
        return f"{self.instagram_graph_host.rstrip('/')}/{self.instagram_api_version}"

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
