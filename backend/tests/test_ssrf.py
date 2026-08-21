"""Security tests for the egress guard — one per attack row in the threat model."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.security.ssrf import (
    ResponseTooLarge,
    SafeAsyncClient,
    SSRFBlocked,
    resolve_and_validate,
    validate_url,
)

SETTINGS = Settings(
    env="test", secret_key="x" * 40, allow_private_networks=False, http_max_bytes=1024
)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/",
        "redis://example.com:6379/",
        "http://example.com:6379/",
        "http://example.com:22/",
    ],
)
def test_blocked_schemes_and_ports(url: str) -> None:
    with pytest.raises(SSRFBlocked):
        validate_url(url, SETTINGS)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://192.168.0.5/",
        "http://172.16.0.1/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://100.64.1.1/",
    ],
)
def test_private_ranges_blocked(url: str) -> None:
    with pytest.raises(SSRFBlocked):
        resolve_and_validate(url, SETTINGS)


@pytest.mark.parametrize(
    "url", ["http://169.254.169.254/latest/meta-data/", "http://metadata.google.internal/"]
)
def test_metadata_endpoint_blocked(url: str) -> None:
    with pytest.raises(SSRFBlocked):
        resolve_and_validate(url, SETTINGS)


def test_localhost_hostname_blocked() -> None:
    with pytest.raises(SSRFBlocked):
        validate_url("http://localhost/admin", SETTINGS)


def test_credentials_in_url_blocked() -> None:
    with pytest.raises(SSRFBlocked):
        validate_url("http://user:password@example.com/", SETTINGS)


def test_public_address_allowed() -> None:
    target = resolve_and_validate("https://93.184.216.34/", SETTINGS)
    assert target.ip == "93.184.216.34"
    assert target.port == 443


async def test_redirect_to_private_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:80/admin"})
        return httpx.Response(200, text="should never be reached")

    client = SafeAsyncClient(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(SSRFBlocked):
        await client.get("https://93.184.216.34/start")
    await client.aclose()


async def test_redirect_chain_capped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://93.184.216.34/loop"})

    client = SafeAsyncClient(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(SSRFBlocked, match="too many redirects"):
        await client.get("https://93.184.216.34/loop")
    await client.aclose()


async def test_response_size_capped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"A" * 5000)

    client = SafeAsyncClient(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(ResponseTooLarge):
        await client.get("https://93.184.216.34/big")
    await client.aclose()


async def test_ip_is_pinned_after_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding: the connection must go to the address we validated, not a re-lookup."""
    import app.security.ssrf as ssrf

    lookups: list[str] = []

    def fake_resolve(host: str, port: int) -> list[tuple[int, str]]:
        lookups.append(host)
        # First answer public, any later answer private: a rebinding attack.
        return [(2, "93.184.216.34" if len(lookups) == 1 else "127.0.0.1")]

    monkeypatch.setattr(ssrf, "_resolve", fake_resolve)

    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("host")))
        return httpx.Response(200, text="ok")

    # A pinning client is built by SafeAsyncClient only when it owns its transport, so
    # assert on the request the pinning path produces directly.
    client = SafeAsyncClient(SETTINGS)
    client._pin_connections = True
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = await client.get("https://rebind.example.com/")
    await client.aclose()

    assert response.status_code == 200
    assert len(lookups) == 1, "the host must be resolved exactly once"
    assert seen[0][0] == "93.184.216.34", "the request must target the pinned IP"
    assert seen[0][1] == "rebind.example.com", "the original Host header must be preserved"


def test_private_networks_flag_is_refused_in_production() -> None:
    with pytest.raises(ValueError, match="ALLOW_PRIVATE_NETWORKS"):
        Settings(env="production", secret_key="y" * 40, allow_private_networks=True)


def test_default_secret_refused_in_production() -> None:
    from app.core.config import DEFAULT_SECRET

    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(env="production", secret_key=DEFAULT_SECRET)
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(env="production", secret_key="too-short")


def test_wildcard_cors_refused_in_production() -> None:
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        Settings(env="production", secret_key="z" * 40, cors_origins=["*"])


def test_pinning_is_disabled_behind_a_mandated_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A forward proxy resolves names itself; pinning would break CONNECT for no gain."""
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert SafeAsyncClient(SETTINGS)._pin_connections is True

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    assert SafeAsyncClient(SETTINGS)._pin_connections is False


async def test_validation_still_runs_when_pinning_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disabling pinning must not disable the policy checks that matter most."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        return httpx.Response(200, text="should never be reached")

    client = SafeAsyncClient(SETTINGS, transport=httpx.MockTransport(handler))
    with pytest.raises(SSRFBlocked):
        await client.get("http://127.0.0.1/")
    with pytest.raises(SSRFBlocked):
        await client.get("https://93.184.216.34/start")
    await client.aclose()
