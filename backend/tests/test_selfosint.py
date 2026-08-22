"""Self-OSINT API: OAuth security, data isolation, token hygiene, lifecycle.

The properties under test here are the ones that make it safe to hold somebody's
Instagram credential at all. Network calls to Meta are stubbed at the OAuth-client
boundary; everything below that — state validation, encryption, ownership, deletion — is
the real implementation running against a real database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio

from app.core.enums import TokenState
from app.security import crypto

TOKEN_A = "IGQV-token-for-user-a"
TOKEN_B = "IGQV-token-for-user-b"

BASE = "/api/v1/self/instagram"


@pytest.fixture(autouse=True)
def _reset_login_throttle() -> Any:
    """Clear the per-username login throttle between tests.

    Test isolation, not a relaxation: the throttle is process-global and each test in
    this module logs the same two usernames in, so without this the tenth test onward
    would be rejected by a working security control rather than by the code under test.
    """
    from app.api.routes.auth import _login_bucket

    _login_bucket._buckets.clear()
    yield
    _login_bucket._buckets.clear()


@pytest.fixture(autouse=True)
def _configure_instagram() -> Any:
    """Give the deployment Meta app credentials for the duration of a test."""
    from app.core.config import get_settings

    settings = get_settings()
    before = (settings.instagram_app_id, settings.instagram_app_secret)
    settings.instagram_app_id = "test-app-id"
    settings.instagram_app_secret = "test-app-secret"
    yield
    settings.instagram_app_id, settings.instagram_app_secret = before


@pytest.fixture
def stub_oauth(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Stub the Meta boundary. Everything below it stays real."""
    from app.selfosint import instagram_oauth as oauth_module

    state: dict[str, Any] = {"token": TOKEN_A, "account_id": "ig-1", "username": "example_user"}

    async def fake_exchange(self: Any, code: str) -> Any:
        if code == "bad-code":
            raise oauth_module.InstagramAuthRejected("Instagram rejected the code exchange.")
        return oauth_module.TokenGrant(
            token=crypto.Secret(state["token"]),
            account_id=state["account_id"],
            scopes=["instagram_business_basic"],
            expires_at=datetime.now(tz=UTC) + timedelta(days=60),
        )

    async def fake_verify(self: Any, token: crypto.Secret) -> dict[str, Any]:
        if state.get("verify_raises"):
            raise state["verify_raises"]
        return {
            "user_id": state["account_id"],
            "username": state["username"],
            "account_type": "BUSINESS",
            "biography": "hello",
        }

    monkeypatch.setattr(oauth_module.InstagramOAuthClient, "exchange_code", fake_exchange)
    monkeypatch.setattr(oauth_module.InstagramOAuthClient, "verify", fake_verify)
    return state


async def register(client: Any, username: str) -> str:
    """Register a user and return their bearer token."""
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{username}@example.com",
            "username": username,
            "password": "correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201, response.text
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200, login.text
    return str(login.json()["access_token"])


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def two_users(client: Any) -> Any:
    """Two independent users on the same deployment."""
    return {"a": auth(await register(client, "usera")), "b": auth(await register(client, "userb"))}


async def connect(client: Any, headers: dict[str, str]) -> Any:
    """Drive a full connect flow and return the callback response."""
    start = await client.post(f"{BASE}/connect", headers=headers)
    assert start.status_code == 200, start.text
    state = start.json()["authorize_url"].split("state=")[1].split("&")[0]
    return await client.post(
        f"{BASE}/callback", json={"code": "good-code", "state": state}, headers=headers
    )


# -- authentication -----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/connect"),
        ("post", "/callback"),
        ("get", "/account"),
        ("post", "/sync"),
        ("get", "/report"),
        ("delete", "/disconnect"),
        ("delete", "/data"),
    ],
)
async def test_every_endpoint_requires_authentication(
    client: Any, method: str, path: str
) -> None:
    # httpx's get/delete take no body, so only send one where the verb allows it.
    kwargs = {"json": {}} if method == "post" else {}
    response = await getattr(client, method)(f"{BASE}{path}", **kwargs)
    assert response.status_code == 401, f"{method} {path} was reachable unauthenticated"


# -- OAuth state / CSRF -------------------------------------------------------


async def test_connect_returns_an_official_meta_authorize_url(
    client: Any, two_users: Any
) -> None:
    response = await client.post(f"{BASE}/connect", headers=two_users["a"])
    assert response.status_code == 200
    url = response.json()["authorize_url"]
    assert url.startswith("https://www.instagram.com/oauth/authorize")
    assert "client_id=test-app-id" in url
    assert "response_type=code" in url
    assert "state=" in url
    # The dead Basic Display scopes must never appear.
    assert "user_profile" not in url and "user_media" not in url


async def test_callback_rejects_an_unknown_state(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    response = await client.post(
        f"{BASE}/callback",
        json={"code": "good-code", "state": "never-issued"},
        headers=two_users["a"],
    )
    assert response.status_code == 400
    assert "not valid" in response.json()["detail"]


async def test_callback_rejects_a_state_issued_to_another_user(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    """The CSRF property: a stolen state is useless without that user's session."""
    start = await client.post(f"{BASE}/connect", headers=two_users["a"])
    state = start.json()["authorize_url"].split("state=")[1].split("&")[0]

    response = await client.post(
        f"{BASE}/callback",
        json={"code": "good-code", "state": state},
        headers=two_users["b"],  # user B presents user A's state
    )
    assert response.status_code == 400
    account_b = await client.get(f"{BASE}/account", headers=two_users["b"])
    assert account_b.json()["connected"] is False


async def test_state_is_single_use(client: Any, two_users: Any, stub_oauth: Any) -> None:
    start = await client.post(f"{BASE}/connect", headers=two_users["a"])
    state = start.json()["authorize_url"].split("state=")[1].split("&")[0]
    body = {"code": "good-code", "state": state}

    first = await client.post(f"{BASE}/callback", json=body, headers=two_users["a"])
    assert first.status_code == 200
    replay = await client.post(f"{BASE}/callback", json=body, headers=two_users["a"])
    assert replay.status_code == 400


async def test_expired_state_is_rejected(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import OAuthState

    start = await client.post(f"{BASE}/connect", headers=two_users["a"])
    state = start.json()["authorize_url"].split("state=")[1].split("&")[0]

    record = (await session.execute(select(OAuthState))).scalars().first()
    record.expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    await session.commit()

    response = await client.post(
        f"{BASE}/callback", json={"code": "good-code", "state": state}, headers=two_users["a"]
    )
    assert response.status_code == 400


async def test_the_raw_state_is_not_stored(
    client: Any, two_users: Any, session: Any
) -> None:
    """Only a hash is persisted, so a database read cannot be replayed as a callback."""
    from sqlalchemy import select

    from app.models import OAuthState

    start = await client.post(f"{BASE}/connect", headers=two_users["a"])
    state = start.json()["authorize_url"].split("state=")[1].split("&")[0]

    record = (await session.execute(select(OAuthState))).scalars().first()
    assert record.state_hash != state
    assert state not in record.state_hash
    assert len(record.state_hash) == 64


async def test_rejected_code_exchange_is_reported_as_a_gateway_error(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    start = await client.post(f"{BASE}/connect", headers=two_users["a"])
    state = start.json()["authorize_url"].split("state=")[1].split("&")[0]
    response = await client.post(
        f"{BASE}/callback", json={"code": "bad-code", "state": state}, headers=two_users["a"]
    )
    assert response.status_code == 502


async def test_connect_is_unavailable_when_the_operator_has_no_meta_app(
    client: Any, two_users: Any
) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    settings.instagram_app_id = None
    response = await client.post(f"{BASE}/connect", headers=two_users["a"])
    assert response.status_code == 503
    assert "INSTAGRAM_APP_ID" in response.json()["detail"]


# -- token storage ------------------------------------------------------------


async def test_the_token_is_encrypted_at_rest(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import LinkedAccount

    assert (await connect(client, two_users["a"])).status_code == 200

    account = (await session.execute(select(LinkedAccount))).scalars().one()
    assert account.encrypted_token is not None
    assert TOKEN_A not in account.encrypted_token
    assert account.encrypted_token.startswith("v1.")
    # And it really is the token, readable only with the right binding context.
    opened = crypto.decrypt(
        account.encrypted_token, aad=crypto.account_aad(account.user_id, "instagram")
    )
    assert opened.reveal() == TOKEN_A


async def test_a_stored_token_cannot_be_read_with_another_users_context(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import LinkedAccount

    await connect(client, two_users["a"])
    account = (await session.execute(select(LinkedAccount))).scalars().one()
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(
            account.encrypted_token, aad=crypto.account_aad(uuid.uuid4(), "instagram")
        )


async def test_no_endpoint_ever_returns_the_token(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    """Sweep every response body in the feature for the token value."""
    await connect(client, two_users["a"])
    await client.post(f"{BASE}/sync", headers=two_users["a"])

    bodies = [
        (await client.get(f"{BASE}/account", headers=two_users["a"])).text,
        (await client.get(f"{BASE}/report", headers=two_users["a"])).text,
        (await client.post(f"{BASE}/sync?force=true", headers=two_users["a"])).text,
        (await client.delete(f"{BASE}/disconnect", headers=two_users["a"])).text,
    ]
    for body in bodies:
        assert TOKEN_A not in body
        assert "encrypted_token" not in body
        assert "access_token" not in body


async def test_the_token_never_reaches_the_logs(
    client: Any, two_users: Any, stub_oauth: Any, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    await connect(client, two_users["a"])
    await client.post(f"{BASE}/sync", headers=two_users["a"])

    assert TOKEN_A not in caplog.text


async def test_the_token_is_not_written_to_the_audit_log(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import AuditLog

    await connect(client, two_users["a"])
    rows = list((await session.execute(select(AuditLog))).scalars())
    actions = {row.action for row in rows}
    assert "self_osint.connected" in actions
    for row in rows:
        assert TOKEN_A not in str(row.meta)
        assert TOKEN_A not in row.target


# -- data isolation / IDOR ----------------------------------------------------


async def test_a_user_cannot_see_another_users_account(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    await connect(client, two_users["a"])

    response = await client.get(f"{BASE}/account", headers=two_users["b"])
    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert response.json()["username"] is None


async def test_a_user_cannot_read_another_users_report(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    await connect(client, two_users["a"])
    await client.post(f"{BASE}/sync", headers=two_users["a"])

    response = await client.get(f"{BASE}/report", headers=two_users["b"])
    assert response.status_code == 404


async def test_a_user_cannot_trigger_another_users_sync(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    await connect(client, two_users["a"])
    response = await client.post(f"{BASE}/sync", headers=two_users["b"])
    assert response.status_code == 404


async def test_a_user_cannot_disconnect_another_users_account(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import LinkedAccount

    await connect(client, two_users["a"])

    response = await client.delete(f"{BASE}/disconnect", headers=two_users["b"])
    assert response.status_code == 404

    account = (await session.execute(select(LinkedAccount))).scalars().one()
    assert account.encrypted_token is not None, "user A's credential was destroyed by user B"


async def test_a_user_cannot_delete_another_users_data(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import Investigation, LinkedAccount

    await connect(client, two_users["a"])
    await client.post(f"{BASE}/sync", headers=two_users["a"])

    response = await client.delete(f"{BASE}/data", headers=two_users["b"])
    assert response.status_code == 200  # B deletes B's data: there is none
    assert response.json()["removed"]["investigations"] == 0

    # A's connection and audit investigation are untouched.
    assert (await session.execute(select(LinkedAccount))).scalars().one() is not None
    investigations = list((await session.execute(select(Investigation))).scalars())
    assert len(investigations) == 1


async def test_two_users_hold_independent_connections(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    """The multi-user requirement: separate tokens, separate accounts, no sharing."""
    from sqlalchemy import select

    from app.models import LinkedAccount

    await connect(client, two_users["a"])
    stub_oauth["token"] = TOKEN_B
    stub_oauth["account_id"] = "ig-2"
    stub_oauth["username"] = "other_user"
    await connect(client, two_users["b"])

    accounts = list((await session.execute(select(LinkedAccount))).scalars())
    assert len(accounts) == 2
    assert {a.provider_account_id for a in accounts} == {"ig-1", "ig-2"}
    assert accounts[0].encrypted_token != accounts[1].encrypted_token

    for account in accounts:
        opened = crypto.decrypt(
            account.encrypted_token, aad=crypto.account_aad(account.user_id, "instagram")
        )
        expected = TOKEN_A if account.provider_account_id == "ig-1" else TOKEN_B
        assert opened.reveal() == expected

    a_view = (await client.get(f"{BASE}/account", headers=two_users["a"])).json()
    b_view = (await client.get(f"{BASE}/account", headers=two_users["b"])).json()
    assert a_view["provider_account_id"] == "ig-1"
    assert b_view["provider_account_id"] == "ig-2"
    assert a_view["investigation_id"] != b_view["investigation_id"]


async def test_the_client_cannot_assert_ownership_of_an_arbitrary_account(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    """No endpoint accepts an Instagram account id as input, so none can be spoofed."""
    from app.main import create_app

    app = create_app(with_lifespan=False)
    for path, operations in app.openapi()["paths"].items():
        if "/self/instagram" not in path:
            continue
        assert "{" not in path, f"{path} takes a path parameter that could be tampered with"
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                assert parameter["name"] not in {
                    "user_id",
                    "account_id",
                    "provider_account_id",
                    "investigation_id",
                }, f"{method} {path} accepts {parameter['name']} from the client"


# -- lifecycle ----------------------------------------------------------------


async def test_connect_creates_an_investigation_owned_by_that_user(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import Investigation, LinkedAccount, User

    await connect(client, two_users["a"])
    account = (await session.execute(select(LinkedAccount))).scalars().one()
    investigation = await session.get(Investigation, account.investigation_id)
    user = await session.get(User, account.user_id)

    assert investigation is not None
    assert investigation.owner_id == user.id
    assert investigation.config.get("self_osint") is True


async def test_sync_collects_and_reports(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    await connect(client, two_users["a"])
    response = await client.post(f"{BASE}/sync", headers=two_users["a"])
    assert response.status_code == 200, response.text
    assert response.json()["synced"] is True
    assert response.json()["last_synced_at"] is not None

    account = (await client.get(f"{BASE}/account", headers=two_users["a"])).json()
    assert account["username"] == "example_user"
    assert account["token_state"] == "active"


async def test_sync_is_rate_limited(client: Any, two_users: Any, stub_oauth: Any) -> None:
    """Instagram is not polled continuously; a rapid re-sync is refused."""
    await connect(client, two_users["a"])
    assert (await client.post(f"{BASE}/sync", headers=two_users["a"])).status_code == 200

    second = await client.post(f"{BASE}/sync", headers=two_users["a"])
    assert second.status_code == 429
    assert second.headers.get("Retry-After")
    # `force` is the documented escape hatch for a deliberate manual refresh.
    assert (
        await client.post(f"{BASE}/sync?force=true", headers=two_users["a"])
    ).status_code == 200


async def test_sync_without_a_connection_is_a_404(client: Any, two_users: Any) -> None:
    assert (await client.post(f"{BASE}/sync", headers=two_users["a"])).status_code == 404


async def test_a_rejected_token_moves_to_reauthorization_required(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    from app.selfosint.instagram_oauth import InstagramAuthRejected

    await connect(client, two_users["a"])
    stub_oauth["verify_raises"] = InstagramAuthRejected("Instagram rejected the check.")

    response = await client.post(f"{BASE}/sync", headers=two_users["a"])
    assert response.status_code == 409

    account = (await client.get(f"{BASE}/account", headers=two_users["a"])).json()
    assert account["token_state"] == str(TokenState.REAUTHORIZATION_REQUIRED)
    assert account["token_state_detail"]


async def test_an_expired_token_is_reported_as_expired(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import LinkedAccount

    await connect(client, two_users["a"])
    account = (await session.execute(select(LinkedAccount))).scalars().one()
    account.expires_at = datetime.now(tz=UTC) - timedelta(days=1)
    await session.commit()

    response = await client.post(f"{BASE}/sync", headers=two_users["a"])
    assert response.status_code == 409
    account_view = (await client.get(f"{BASE}/account", headers=two_users["a"])).json()
    assert account_view["token_state"] == str(TokenState.EXPIRED)


async def test_an_undecryptable_credential_is_marked_invalid(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import LinkedAccount

    await connect(client, two_users["a"])
    account = (await session.execute(select(LinkedAccount))).scalars().one()
    account.encrypted_token = "v1.AAAAAAAAAAAAAAAA.BBBBBBBBBBBBBBBBBBBB"
    await session.commit()

    response = await client.post(f"{BASE}/sync", headers=two_users["a"])
    assert response.status_code == 409
    view = (await client.get(f"{BASE}/account", headers=two_users["a"])).json()
    assert view["token_state"] == str(TokenState.INVALID)


async def test_disconnect_destroys_the_credential_but_keeps_the_data(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import Investigation, LinkedAccount, LinkedAccountRevocation

    await connect(client, two_users["a"])
    await client.post(f"{BASE}/sync", headers=two_users["a"])

    response = await client.delete(f"{BASE}/disconnect", headers=two_users["a"])
    assert response.status_code == 200
    body = response.json()
    assert body["disconnected"] is True
    # Honest about what we cannot do: Instagram Login has no server-side revoke.
    assert body["remote_revocation"] is False
    assert "manage_access" in body["instructions"]

    account = (await session.execute(select(LinkedAccount))).scalars().one()
    assert account.encrypted_token is None
    assert account.token_state == str(TokenState.REVOKED)
    assert account.scopes == []

    # Disconnect is not deletion: the audit investigation survives.
    assert len(list((await session.execute(select(Investigation))).scalars())) == 1
    tombstones = list((await session.execute(select(LinkedAccountRevocation))).scalars())
    assert [t.reason for t in tombstones] == ["disconnected"]


async def test_sync_after_disconnect_is_refused(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    await connect(client, two_users["a"])
    await client.delete(f"{BASE}/disconnect", headers=two_users["a"])
    response = await client.post(f"{BASE}/sync?force=true", headers=two_users["a"])
    assert response.status_code == 409


async def test_delete_removes_everything_collected(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import (
        Entity,
        Investigation,
        LinkedAccount,
        LinkedAccountRevocation,
        Observation,
    )

    await connect(client, two_users["a"])
    await client.post(f"{BASE}/sync", headers=two_users["a"])

    response = await client.delete(f"{BASE}/data", headers=two_users["a"])
    assert response.status_code == 200
    assert response.json()["deleted"] is True

    assert list((await session.execute(select(LinkedAccount))).scalars()) == []
    assert list((await session.execute(select(Investigation))).scalars()) == []
    assert list((await session.execute(select(Entity))).scalars()) == []
    assert list((await session.execute(select(Observation))).scalars()) == []

    # The security record of what happened survives deliberately.
    tombstones = list((await session.execute(select(LinkedAccountRevocation))).scalars())
    assert "data_deleted" in {t.reason for t in tombstones}


async def test_delete_keeps_audit_logs(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import AuditLog

    await connect(client, two_users["a"])
    await client.delete(f"{BASE}/data", headers=two_users["a"])

    actions = {
        row.action for row in (await session.execute(select(AuditLog))).scalars()
    }
    assert {"self_osint.connected", "self_osint.data_deleted"} <= actions


async def test_reconnecting_replaces_rather_than_duplicates(
    client: Any, two_users: Any, stub_oauth: Any, session: Any
) -> None:
    from sqlalchemy import select

    from app.models import LinkedAccount

    await connect(client, two_users["a"])
    stub_oauth["token"] = "IGQV-rotated-token"
    await connect(client, two_users["a"])

    accounts = list((await session.execute(select(LinkedAccount))).scalars())
    assert len(accounts) == 1
    opened = crypto.decrypt(
        accounts[0].encrypted_token, aad=crypto.account_aad(accounts[0].user_id, "instagram")
    )
    assert opened.reveal() == "IGQV-rotated-token"


async def test_the_account_endpoint_advertises_api_limitations(
    client: Any, two_users: Any, stub_oauth: Any
) -> None:
    """The honesty requirement: unavailable capabilities are stated, not omitted."""
    await connect(client, two_users["a"])
    capabilities = (await client.get(f"{BASE}/account", headers=two_users["a"])).json()[
        "capabilities"
    ]
    by_key = {c["key"]: c for c in capabilities}

    assert by_key["profile"]["availability"] == "available"
    assert by_key["follower_list"]["availability"] == "not_available"
    assert by_key["email_phone"]["availability"] == "not_available"
    assert by_key["other_accounts"]["availability"] == "not_available"
    # Comments were not granted in this connection, so they are a permission gap and
    # must not be reported as an API limitation.
    assert by_key["comments"]["availability"] == "requires_permission"
    assert all(c["note"] for c in capabilities)
