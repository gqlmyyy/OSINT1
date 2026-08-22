"""Shared model mixins."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Normalise a value read back from the database to an aware UTC datetime.

    SQLite has no native timestamp type, so ``DateTime(timezone=True)`` round-trips as a
    *naive* datetime there while PostgreSQL returns an aware one. Comparing the two
    raises ``TypeError``, which would turn an expiry check into a 500 on exactly the
    deployments that use SQLite. Everything stored is UTC by construction (see
    :func:`utcnow`), so attaching UTC to a naive value is a correction, not a guess.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )
