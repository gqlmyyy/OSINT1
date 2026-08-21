from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import GUID, Base, JSONVariant
from app.core.enums import InvestigationStage, InvestigationStatus
from app.models.base import Timestamped, UUIDPk


class Investigation(UUIDPk, Timestamped, Base):
    __tablename__ = "investigations"

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=InvestigationStatus.DRAFT, nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(24), default=InvestigationStage.IDLE, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tags: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict, nullable=False)

    targets: Mapped[list[Target]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan", lazy="selectin"
    )


class Target(UUIDPk, Timestamped, Base):
    __tablename__ = "targets"
    __table_args__ = (
        UniqueConstraint("investigation_id", "type", "normalized", name="uq_target_normalized"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized: Mapped[str] = mapped_column(String(512), nullable=False, index=True)

    investigation: Mapped[Investigation] = relationship(back_populates="targets")


class Note(UUIDPk, Timestamped, Base):
    __tablename__ = "notes"

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="CASCADE"), nullable=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)


class Tag(UUIDPk, Timestamped, Base):
    __tablename__ = "tags"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
