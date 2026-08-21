"""Async database engine, session factory and portable column types.

The type decorators keep the schema identical in meaning on PostgreSQL (production) and
SQLite (test suite), so tests exercise the real models without needing a server.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from sqlalchemy import CHAR, Dialect, MetaData, types
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class GUID(types.TypeDecorator[uuid.UUID]):
    """UUID column: native ``uuid`` on PostgreSQL, ``CHAR(36)`` elsewhere."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


JSONVariant = types.JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    metadata: ClassVar[MetaData] = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONVariant,
        list[str]: JSONVariant,
    }


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        kwargs: dict[str, Any] = {"echo": settings.db_echo, "future": True}
        if not settings.database_url.startswith("sqlite"):
            kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)
        _engine = create_async_engine(settings.database_url, **kwargs)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one transaction-scoped session per request."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    """Create the schema directly. Used by tests and by the demo/dev bootstrap."""
    import app.models  # noqa: F401  (registers mappers)

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def reset_engine_for_tests() -> None:
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
