"""The Meta OAuth boundary: correct endpoints, correct flow, no token leakage.

Runs a mocked transport through the real SSRF-guarded client, so URL validation still
executes on every outbound request.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.security.crypto import Secret
from app.selfosint.instagram_oauth import (
    InstagramAuthRejected,
    InstagramNotConfigured,
    InstagramOAuthClient,
    InstagramOAuthError,
    revocation_instructions,
)

SHORT_TOKEN = "IG-short-lived-token"
LONG_TOKEN = "IG-long-lived-token"


def settings(**overrides: Any) -> Settings:
    base = {
        "env": "test",
        "secret_key": "test-secret-key-that-is-long-enough-for-checks-1234",
        "instagram_app_id": "app-123",
        "instagram_app_secret": "shhh-app-secret",
        "instagram_redirect_uri": "https://app.example.com/self-osint/instagram/callback",
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these hermetic without weakening the guard: only DNS is stubbed."""
    import app.security.ssrf as ssrf

    monkeypatch.setattr(ssrf, "_resolve", lambda host, port: [(2, "93.184.216.34")])


def client_for(handler: Any, config: Settings | None = None) -> InstagramOAuthClient:
    from app.security.ssrf import SafeAsyncClient

    resolved = config or settings()
    http = SafeAsyncClient(resolved, transport=httpx.MockTransport(handler))
    return InstagramOAuthClient(resolved, http=http)


# -- authorize URL -------------------------------------------------------------


def test_authorize_url_targets_the_instagram_login_endpoint() -> None:
    url = InstagramOAuthClient(settings()).authorize_url(state="st-1")
    assert url.startswith("https://www.instagram.com/oauth/authorize?")
    assert "client_id=app-123" in url
    assert "response_type=code" in url
    assert "state=st-1" in url
    assert "scope=instagram_business_basic" in url


def test_authorize_url_never_contains_the_app_secret() -> None:
    """The secret belongs in the back-channel exchange only, never in a browser URL."""
    url = InstagramOAuthClient(settings()).authorize_url(state="st-1")
    assert "shhh-app-secret" not in url


def test_authorize_url_requires_configuration() -> None:
    with pytest.raises(InstagramNotConfigured):
        InstagramOAuthClient(settings(instagram_app_id=None)).authorize_url(state="s")


# -- code exchange -------------------------------------------------------------


def _exchange_handler(
    *, short_status: int = 200, long_status: int = 200, record: list[Any] | None = None
) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        if request.url.path.endswith("/oauth/access_token"):
            return httpx.Response(
                short_status,
                json={
                    "access_token": SHORT_TOKEN,
                    "user_id": "17841400000000000",
                    "permissions": "instagram_business_basic",
                },
            )
        if request.url.path.endswith("/access_token"):
            return httpx.Response(
                long_status, json={"access_token": LONG_TOKEN, "expires_in": 5184000}
            )
        return httpx.Response(404)

    return handler


async def test_exchange_upgrades_to_a_long_lived_token() -> None:
    grant = await client_for(_exchange_handler()).exchange_code("auth-code")
    assert grant.token.reveal() == LONG_TOKEN
    assert grant.account_id == "17841400000000000"
    assert grant.scopes == ["instagram_business_basic"]
    assert grant.expires_at is not None


async def test_exchange_posts_the_secret_in_the_body_not_the_url() -> None:
    """Credentials in a query string end up in proxy and server logs."""
    requests: list[httpx.Request] = []
    await client_for(_exchange_handler(record=requests)).exchange_code("auth-code")

    token_request = requests[0]
    assert token_request.method == "POST"
    assert "shhh-app-secret" not in str(token_request.url)
    assert b"client_secret=shhh-app-secret" in token_request.content


async def test_a_failed_long_lived_exchange_keeps_the_short_token() -> None:
    """Degrade rather than fail the whole connection."""
    grant = await client_for(_exchange_handler(long_status=500)).exchange_code("c")
    assert grant.token.reveal() == SHORT_TOKEN
    assert grant.expires_at is not None


async def test_a_rejected_code_raises_auth_rejected() -> None:
    with pytest.raises(InstagramAuthRejected):
        await client_for(_exchange_handler(short_status=400)).exchange_code("bad")


async def test_a_response_without_a_token_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"user_id": "1"})

    with pytest.raises(InstagramAuthRejected):
        await client_for(handler).exchange_code("c")


async def test_non_json_responses_are_reported_not_crashed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    with pytest.raises(InstagramOAuthError):
        await client_for(handler).exchange_code("c")


# -- verify / refresh ----------------------------------------------------------


async def test_verify_returns_the_account() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"username": "example_user", "user_id": "1"})

    profile = await client_for(handler).verify(Secret(LONG_TOKEN))
    assert profile["username"] == "example_user"


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_verify_maps_auth_failures_to_reauthorization(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "Invalid OAuth token"}})

    with pytest.raises(InstagramAuthRejected):
        await client_for(handler).verify(Secret(LONG_TOKEN))


async def test_verify_distinguishes_an_outage_from_a_bad_token() -> None:
    """A 500 must not make us tell the user their authorisation is broken."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    with pytest.raises(InstagramOAuthError) as excinfo:
        await client_for(handler).verify(Secret(LONG_TOKEN))
    assert not isinstance(excinfo.value, InstagramAuthRejected)


async def test_refresh_extends_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": "IG-refreshed", "expires_in": 5184000}
        )

    token, expires_at = await client_for(handler).refresh(Secret(LONG_TOKEN))
    assert token.reveal() == "IG-refreshed"
    assert expires_at is not None


# -- error messages ------------------------------------------------------------


async def test_error_messages_never_echo_the_token_back() -> None:
    """Meta echoes the failing request in some errors; we must not surface it."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Invalid OAuth access token",
                    "fbtrace_id": "abc",
                    "request": f"access_token={LONG_TOKEN}",
                }
            },
        )

    with pytest.raises(InstagramAuthRejected) as excinfo:
        await client_for(handler).verify(Secret(LONG_TOKEN))
    assert LONG_TOKEN not in str(excinfo.value)


def test_token_grant_redaction_has_no_token_field() -> None:
    from datetime import UTC, datetime

    from app.selfosint.instagram_oauth import TokenGrant

    grant = TokenGrant(
        token=Secret(LONG_TOKEN),
        account_id="1",
        scopes=["instagram_business_basic"],
        expires_at=datetime.now(tz=UTC),
    )
    redacted = grant.redacted()
    assert LONG_TOKEN not in str(redacted)
    assert "token" not in redacted


def test_revocation_instructions_are_honest_about_the_limitation() -> None:
    text = revocation_instructions()
    assert "does not provide an API to revoke" in text
    assert "manage_access" in text
