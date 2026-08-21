"""Observations and their immutable raw evidence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import GUID, Base, JSONVariant
from app.core.enums import Assertion
from app.models.base import Timestamped, UUIDPk, utcnow

_ASSERTIONS = ", ".join(f"'{a.value}'" for a in Assertion)


class Observation(UUIDPk, Timestamped, Base):
    __tablename__ = "observations"
    __table_args__ = (
        CheckConstraint(f"assertion IN ({_ASSERTIONS})", name="assertion_valid"),
        Index("ix_obs_entity_time", "entity_id", "observed_at"),
        Index("ix_obs_provider", "investigation_id", "provider"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    assertion: Mapped[str] = mapped_column(String(16), default=Assertion.OBSERVED, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict, nullable=False)

    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="observation", cascade="all, delete-orphan", lazy="selectin"
    )


class Evidence(UUIDPk, Timestamped, Base):
    """Raw captured material. Written once, never mutated."""

    __tablename__ = "evidence"
    __table_args__ = (Index("ix_evidence_sha", "sha256"),)

    observation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("observations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_type: Mapped[str] = mapped_column(String(64), default="application/json")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict, nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    observation: Mapped[Observation] = relationship(back_populates="evidence")
