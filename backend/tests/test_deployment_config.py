"""Settings parsing and container-probe configuration.

Both classes of bug here were invisible to the unit suite and only appeared when the
stack actually ran: a comma-separated env var that crashed startup, and healthchecks
aimed at addresses or protocols the service does not serve.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app.core.config import Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


# -- settings parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:8080,http://localhost:5173", ["http://localhost:8080", "http://localhost:5173"]),
        ("http://localhost:8080", ["http://localhost:8080"]),
        ("http://a.test, http://b.test ", ["http://a.test", "http://b.test"]),
    ],
)
def test_cors_origins_accepts_the_documented_comma_separated_form(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    """.env.example and docker-compose.yml both use CSV; pydantic-settings would
    otherwise try json.loads() on it first and raise SettingsError before startup."""
    monkeypatch.setenv("CORS_ORIGINS", raw)
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    get_settings.cache_clear()
    try:
        assert Settings().cors_origins == expected
    finally:
        get_settings.cache_clear()


def test_allowed_egress_ports_accepts_the_documented_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_EGRESS_PORTS", "80,443,8443")
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    get_settings.cache_clear()
    try:
        assert Settings().allowed_egress_ports == [80, 443, 8443]
    finally:
        get_settings.cache_clear()


def test_env_example_values_actually_load() -> None:
    """Following the README must not produce a backend that refuses to boot."""
    example = REPO_ROOT / ".env.example"
    values: dict[str, str] = {}
    for line in example.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()

    assert "CORS_ORIGINS" in values, "guard assumes the file documents this key"
    settings = Settings(
        secret_key="x" * 40,
        cors_origins=values["CORS_ORIGINS"],
        allowed_egress_ports=values["ALLOWED_EGRESS_PORTS"],
    )
    assert settings.cors_origins and all(o.startswith("http") for o in settings.cors_origins)
    assert settings.allowed_egress_ports == [80, 443]


# -- container probes ---------------------------------------------------------


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())


def test_worker_does_not_inherit_the_backend_http_probe() -> None:
    """The worker shares the backend image but serves no HTTP; without its own probe
    it inherits one it can never satisfy and is reported unhealthy forever."""
    worker = _compose()["services"]["worker"]
    assert "healthcheck" in worker, "the worker needs a probe suited to a queue consumer"
    test = " ".join(worker["healthcheck"]["test"])
    assert "arq" in test and "--check" in test, f"expected arq's own liveness check, got {test!r}"
    assert "8000" not in test, "a worker serves no HTTP port"


def test_worker_waits_for_the_broker_it_needs() -> None:
    depends = _compose()["services"]["worker"]["depends_on"]
    assert depends["redis"]["condition"] == "service_healthy"
    assert depends["postgres"]["condition"] == "service_healthy"


@pytest.mark.parametrize(
    ("dockerfile", "port"),
    [(Path("frontend/Dockerfile"), "80"), (Path("backend/Dockerfile"), "8000")],
)
def test_healthchecks_address_the_loopback_unambiguously(dockerfile: Path, port: str) -> None:
    """"localhost" can resolve to ::1 on an IPv6-capable host. Neither service listens
    on IPv6, so the probe fails with ECONNREFUSED while the service is serving fine."""
    text = (REPO_ROOT / dockerfile).read_text()
    healthcheck = re.search(r"^HEALTHCHECK.*?(?=\n[A-Z]|\Z)", text, re.MULTILINE | re.DOTALL)
    assert healthcheck, f"{dockerfile} has no HEALTHCHECK"
    body = healthcheck.group(0)

    assert "127.0.0.1" in body, f"{dockerfile} probe must use an explicit IPv4 literal"
    assert not re.search(r"//localhost[:/]", body), (
        f"{dockerfile} probe still resolves the ambiguous name 'localhost'"
    )
    assert port in body


def test_backend_image_needs_no_extra_package_to_probe_itself() -> None:
    """The probe uses the interpreter already in the image, so the build does not
    depend on reaching a Debian mirror — restricted networks can still build it."""
    text = (REPO_ROOT / "backend/Dockerfile").read_text()
    assert "apt-get install" not in text
    assert "curl" not in text
