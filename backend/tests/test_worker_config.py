"""The worker's Redis wiring, tested the way arq actually reads it.

The bug these guard against: ``redis_settings`` was a property on a metaclass. Attribute
access answered correctly, so every manual check (``WorkerSettings.redis_settings``,
``get_settings().redis_url``, ``RedisSettings.from_dsn(...)``) looked right — but
``arq.worker.get_kwargs()`` reads ``settings_cls.__dict__``, where a metaclass property
does not appear. arq silently dropped it and built ``Worker()`` with the default
``RedisSettings()``: localhost:6379.

So asserting that REDIS_URL is set proves nothing. These tests assert on the object arq
itself extracts.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from arq.connections import RedisSettings
from arq.worker import Worker, get_kwargs

from app.core.config import get_settings

DOCKER_URL = "redis://redis:6379/0"


def _reload_worker(monkeypatch: pytest.MonkeyPatch, redis_url: str | None) -> Any:
    """Re-import the worker module with a given REDIS_URL, as a fresh process would."""
    if redis_url is None:
        monkeypatch.delenv("REDIS_URL", raising=False)
    else:
        monkeypatch.setenv("REDIS_URL", redis_url)
    get_settings.cache_clear()
    module = importlib.import_module("app.jobs.worker")
    try:
        return importlib.reload(module)
    finally:
        get_settings.cache_clear()


def test_arq_receives_the_configured_redis_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: what arq extracts must be redis:6379, never localhost."""
    worker = _reload_worker(monkeypatch, DOCKER_URL)
    extracted = get_kwargs(worker.WorkerSettings)

    assert "redis_settings" in extracted, (
        "arq dropped redis_settings — it must be a plain entry in WorkerSettings.__dict__, "
        "not resolved through a metaclass property or __getattr__"
    )
    settings = extracted["redis_settings"]
    assert isinstance(settings, RedisSettings)
    assert settings.host == "redis", f"expected the compose service name, got {settings.host!r}"
    assert settings.port == 6379
    assert settings.database == 0


def test_redis_settings_is_in_the_class_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """The precise property that made the original bug invisible."""
    worker = _reload_worker(monkeypatch, DOCKER_URL)
    assert "redis_settings" in worker.WorkerSettings.__dict__


def test_never_silently_falls_back_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker with no broker must fail loudly, not quietly target localhost."""
    default_host = RedisSettings().host
    assert default_host in ("localhost", "127.0.0.1"), "arq's default is what we must avoid"

    with pytest.raises(RuntimeError, match="REDIS_URL"):
        _reload_worker(monkeypatch, None)


def test_every_declared_setting_reaches_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the same class of bug for the other settings, not just Redis."""
    worker = _reload_worker(monkeypatch, DOCKER_URL)
    extracted = get_kwargs(worker.WorkerSettings)
    accepted = set(Worker.__init__.__annotations__) | set(
        __import__("inspect").signature(Worker).parameters
    )

    declared = {
        name
        for name in vars(worker.WorkerSettings)
        if not name.startswith("_") and name in accepted
    }
    missing = declared - set(extracted)
    assert not missing, f"arq will not see these WorkerSettings entries: {sorted(missing)}"
    assert {"functions", "on_startup", "on_shutdown", "redis_settings"} <= set(extracted)


def test_health_check_interval_is_useful_for_a_container_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`arq --check` reads a record written on this interval; arq's default is an hour."""
    worker = _reload_worker(monkeypatch, DOCKER_URL)
    interval = get_kwargs(worker.WorkerSettings)["health_check_interval"]
    assert 0 < interval <= 60, f"{interval}s is too coarse to detect a wedged worker"


@pytest.mark.parametrize(
    ("url", "host", "port", "db"),
    [
        ("redis://redis:6379/0", "redis", 6379, 0),
        ("redis://redis:6379/2", "redis", 6379, 2),
        ("redis://cache.internal:6380/1", "cache.internal", 6380, 1),
    ],
)
def test_url_is_translated_faithfully(
    monkeypatch: pytest.MonkeyPatch, url: str, host: str, port: int, db: int
) -> None:
    worker = _reload_worker(monkeypatch, url)
    settings = get_kwargs(worker.WorkerSettings)["redis_settings"]
    assert (settings.host, settings.port, settings.database) == (host, port, db)
