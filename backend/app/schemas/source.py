from __future__ import annotations

from typing import Any

from pydantic import Field

from app.schemas.common import StrictModel


class RateLimitOut(StrictModel):
    rpm: int
    concurrency: int
    timeout_seconds: float


class SourceOut(StrictModel):
    name: str
    type: str
    enabled: bool
    requires_api_key: bool
    configured: bool
    rate_limit: RateLimitOut
    reliability: float
    cost: str
    coverage: dict[str, Any]
    accepts: list[str]
    emits: list[str]
    recursive: bool
    description: str
    health: str
    health_detail: str


class SourceUpdate(StrictModel):
    enabled: bool | None = None
    rpm: int | None = Field(default=None, ge=1, le=6000)
    concurrency: int | None = Field(default=None, ge=1, le=64)
    timeout_seconds: float | None = Field(default=None, ge=1.0, le=300.0)
    config: dict[str, Any] | None = None
