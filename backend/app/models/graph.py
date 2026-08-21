"""Entities, identifiers and relationships — the graph itself."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import GUID, Base, JSONVariant
from app.core.enums import Assertion
from app.models.base import Timestamped, UUIDPk, utcnow

_ASSERTIONS = ", ".join(f"'{a.value}'" for a in Assertion)


class Entity(UUIDPk, Timestamped, Base):
    """A node. ``canonical_key`` is derived, never user-supplied, and is the dedup key."""

    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("investigation_id", "canonical_key", name="uq_entity_canonical"),
        Index("ix_entity_type", "investigation_id", "type"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(512), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict, nullable=False)
    sources: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    depth: Mapped[int] = mapped_column(default=0, nullable=False)

    identifiers: Mapped[list[Identifier]] = relationship(
        back_populates="entity", cascade="all, delete-orphan", lazy="selectin"
    )


class Identifier(UUIDPk, Timestamped, Base):
    """A searchable/correlatable handle attached to an entity."""

    __tablename__ = "identifiers"
    __table_args__ = (
        UniqueConstraint("entity_id", "kind", "normalized", name="uq_identifier_value"),
        Index("ix_identifier_lookup", "kind", "normalized"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized: Mapped[str] = mapped_column(String(512), nullable=False)

    entity: Mapped[Entity] = relationship(back_populates="identifiers")


class Relationship(UUIDPk, Timestamped, Base):
    """A directed edge. Never exists without evidence (spec 39)."""

    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("investigation_id", "dedupe_key", name="uq_relationship_dedupe"),
        CheckConstraint(f"assertion IN ({_ASSERTIONS})", name="assertion_valid"),
        Index("ix_rel_source", "source_entity_id"),
        Index("ix_rel_target", "target_entity_id"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_entity_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    assertion: Mapped[str] = mapped_column(String(16), default=Assertion.OBSERVED, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict, nullable=False)


class IdentityCandidate(UUIDPk, Timestamped, Base):
    """Entity-resolution output. Explicitly a *candidate*, never a verdict."""

    __tablename__ = "identity_candidates"
    __table_args__ = (
        UniqueConstraint("investigation_id", "entity_a_id", "entity_b_id", name="uq_candidate"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_a_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    entity_b_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    band: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reasons: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, default="", nullable=False)
