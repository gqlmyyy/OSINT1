from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

os.environ.setdefault("ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-tests-1234")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ALLOW_PRIVATE_NETWORKS", "false")
os.environ.setdefault("REDIS_URL", "")

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def _settings() -> None:
    from app.core.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    settings.plugins_path = REPO_ROOT / "plugins"


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[object]:
    """A fresh in-memory database per test, using one shared connection."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    import app.core.db as db_module
    import app.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)

    original_engine, original_maker = db_module._engine, db_module._sessionmaker
    db_module._engine = engine
    db_module._sessionmaker = None
    try:
        yield engine
    finally:
        db_module._engine, db_module._sessionmaker = original_engine, original_maker
        await engine.dispose()


@pytest_asyncio.fixture
async def session(engine: object) -> AsyncIterator[object]:
    from app.core.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def user(session: object) -> object:
    from app.core.enums import Role
    from app.models import User
    from app.security.auth import hash_password

    row = User(
        id=uuid.uuid4(),
        email="analyst@example.com",
        username="analyst",
        password_hash=hash_password("correct-horse-battery-staple"),
        role=str(Role.ADMIN),
    )
    session.add(row)
    await session.commit()
    return row


@pytest_asyncio.fixture
async def investigation(session: object, user: object) -> object:
    from app.models import Investigation

    row = Investigation(
        id=uuid.uuid4(), name="test case", owner_id=user.id, tags=[], config={}
    )
    session.add(row)
    await session.commit()
    return row


@pytest.fixture(autouse=True)
def _reset_shared_state() -> None:
    from app.jobs.events import set_event_bus
    from app.providers.cache import MemoryCache, set_cache
    from app.providers.ratelimit import reset_limiters
    from app.providers.registry import set_registry

    set_cache(MemoryCache())
    set_event_bus(None)
    set_registry(None)
    reset_limiters()
    yield
    set_cache(None)
    set_event_bus(None)
    set_registry(None)


@pytest_asyncio.fixture
async def client(engine: object) -> AsyncIterator[object]:
    """HTTP client bound to the app, with lifespan skipped (fixtures own the schema)."""
    import httpx
    from app.main import create_app

    app = create_app(with_lifespan=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture
async def auth_client(client: object) -> AsyncIterator[object]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@example.com",
            "username": "owner",
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201, response.text
    tokens = await client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "correct-horse-battery-staple"},
    )
    assert tokens.status_code == 200, tokens.text
    client.headers["Authorization"] = f"Bearer {tokens.json()['access_token']}"
    yield client
