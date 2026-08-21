from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, JSONVariant
from app.models.base import Timestamped, UUIDPk


class Source(UUIDPk, Timestamped, Base):
    """Operator-editable runtime state for a discovered provider (spec 11)."""

    __tablename__ = "sources"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_api_key: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rpm: Mapped[int] = mapped_column(default=60, nullable=False)
    concurrency: Mapped[int] = mapped_column(default=5, nullable=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=15.0, nullable=False)
    reliability: Mapped[float] = mapped_column(Float, default=0.8, nullable=False)
    cost: Mapped[str] = mapped_column(String(16), default="free", nullable=False)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict, nullable=False)
