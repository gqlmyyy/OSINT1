"""Alembic environment.

The URL always comes from application settings, so migrations can never be pointed at a
different database than the app by an out-of-date ini file.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.models  # noqa: F401  (registers every mapper)
from alembic import context
from app.core.config import get_settings
from app.core.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Render project-specific types with an explicit prefix.

    Without this, ``JSONB(astext_type=Text())`` and our ``GUID`` decorator autogenerate
    as bare names that are not importable in the migration module.
    """
    if type_ == "type":
        from sqlalchemy.dialects.postgresql import JSONB

        from app.core.db import GUID

        if isinstance(obj, GUID):
            autogen_context.imports.add("from app.core.db import GUID")  # type: ignore[attr-defined]
            return "GUID()"
        if isinstance(obj, JSONB):
            autogen_context.imports.add(  # type: ignore[attr-defined]
                "from sqlalchemy.dialects import postgresql"
            )
            return "postgresql.JSONB(astext_type=sa.Text())"
    return False


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_item=render_item,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
