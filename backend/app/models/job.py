from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, JSONVariant
from app.core.enums import JobStatus
from app.models.base import Timestamped, UUIDPk


class Job(UUIDPk, Timestamped, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status", "investigation_id", "status"),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_value: Mapped[str] = mapped_column(String(512), nullable=False)
    depth: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED, nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict, nullable=False)


class ProviderRun(UUIDPk, Timestamped, Base):
    __tablename__ = "provider_runs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(default=0, nullable=False)
    observation_count: Mapped[int] = mapped_column(default=0, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
