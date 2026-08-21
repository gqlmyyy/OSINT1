"""Fixtures shared by plugin tests: a mocked transport wired through the real guard."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep plugin tests hermetic without weakening the guard.

    Name resolution is stubbed to a fixed public address, so scheme/port/private-range
    validation and redirect checking still run on every request — only the DNS round
    trip is removed.
    """
    import app.security.ssrf as ssrf

    monkeypatch.setattr(ssrf, "_resolve", lambda host, port: [(2, "93.184.216.34")])


@pytest.fixture
def make_ctx() -> Callable[..., object]:
    """Build a ProviderContext whose HTTP client is the real SafeAsyncClient.

    Only the transport is mocked, so every plugin test still exercises URL validation
    and redirect handling rather than bypassing them.
    """
    from app.core.config import get_settings
    from app.providers.base import ProviderContext
    from app.security.ssrf import SafeAsyncClient

    clients: list[SafeAsyncClient] = []

    def factory(handler: Callable[[httpx.Request], httpx.Response], **config: object) -> ProviderContext:
        client = SafeAsyncClient(get_settings(), transport=httpx.MockTransport(handler))
        clients.append(client)
        return ProviderContext(client, config=dict(config))

    yield factory

    import asyncio

    for client in clients:
        with_close = client.aclose()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(with_close)
        else:
            asyncio.ensure_future(with_close)  # noqa: RUF006


@pytest.fixture
def load_provider() -> Callable[[str], object]:
    def loader(name: str) -> object:
        from app.core.config import get_settings
        from app.providers.registry import ProviderRegistry

        registry = ProviderRegistry().discover(get_settings().plugins_path)
        entry = registry.get(name)
        assert entry is not None, f"plugin {name} did not load: {registry.errors}"
        return entry.provider

    return loader
