"""End-to-end API behaviour, including the authorization matrix."""

from __future__ import annotations

import pytest

BASE = "/api/v1"
PASSWORD = "correct-horse-battery-staple"


async def test_health_is_public(client) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["providers"] >= 10


async def test_first_user_becomes_admin_and_can_log_in(client) -> None:
    created = await client.post(
        f"{BASE}/auth/register",
        json={"email": "a@example.com", "username": "first", "password": PASSWORD},
    )
    assert created.status_code == 201
    assert created.json()["role"] == "admin"

    second = await client.post(
        f"{BASE}/auth/register",
        json={"email": "b@example.com", "username": "second", "password": PASSWORD},
    )
    assert second.json()["role"] == "analyst"

    tokens = await client.post(
        f"{BASE}/auth/login", json={"username": "first", "password": PASSWORD}
    )
    assert tokens.status_code == 200
    assert set(tokens.json()) >= {"access_token", "refresh_token", "expires_in"}


async def test_login_failure_is_uniform(client) -> None:
    await client.post(
        f"{BASE}/auth/register",
        json={"email": "a@example.com", "username": "first", "password": PASSWORD},
    )
    missing = await client.post(
        f"{BASE}/auth/login", json={"username": "nobody", "password": PASSWORD}
    )
    wrong = await client.post(
        f"{BASE}/auth/login", json={"username": "first", "password": "wrong-password-here"}
    )
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json(), "the error must not reveal whether the account exists"


async def test_weak_passwords_are_rejected(client) -> None:
    response = await client.post(
        f"{BASE}/auth/register",
        json={"email": "a@example.com", "username": "u", "password": "short"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", f"{BASE}/investigations"),
        ("post", f"{BASE}/investigations"),
        ("get", f"{BASE}/sources"),
        ("get", f"{BASE}/auth/me"),
        ("post", f"{BASE}/demo/seed"),
    ],
)
async def test_protected_routes_require_a_token(client, method: str, path: str) -> None:
    kwargs = {"json": {}} if method == "post" else {}
    response = await getattr(client, method)(path, **kwargs)
    assert response.status_code == 401


async def test_unknown_fields_are_rejected(auth_client) -> None:
    response = await auth_client.post(
        f"{BASE}/investigations", json={"name": "x", "unexpected": "value"}
    )
    assert response.status_code == 422


async def test_investigation_lifecycle(auth_client) -> None:
    created = await auth_client.post(
        f"{BASE}/investigations",
        json={"name": "example_user research", "tags": ["demo"],
              "targets": [{"value": "example_user"}, {"value": "example.com"}]},
    )
    assert created.status_code == 201, created.text
    investigation = created.json()
    assert {t["type"] for t in investigation["targets"]} == {"username", "domain"}
    investigation_id = investigation["id"]

    listed = await auth_client.get(f"{BASE}/investigations")
    assert listed.json()["total"] == 1

    patched = await auth_client.patch(
        f"{BASE}/investigations/{investigation_id}", json={"description": "updated"}
    )
    assert patched.json()["description"] == "updated"

    for path in ("entities", "relationships", "graph", "timeline", "matches", "progress"):
        response = await auth_client.get(f"{BASE}/investigations/{investigation_id}/{path}")
        assert response.status_code == 200, f"{path}: {response.text}"


async def test_targets_are_idempotent_and_normalized(auth_client) -> None:
    created = await auth_client.post(f"{BASE}/investigations", json={"name": "case"})
    investigation_id = created.json()["id"]

    first = await auth_client.post(
        f"{BASE}/investigations/{investigation_id}/targets",
        json={"targets": [{"value": "Example_User"}]},
    )
    second = await auth_client.post(
        f"{BASE}/investigations/{investigation_id}/targets",
        json={"targets": [{"value": "  @example_user "}]},
    )
    assert first.json()[0]["id"] == second.json()[0]["id"]
    assert first.json()[0]["normalized"] == "example_user"


async def test_invalid_target_is_rejected_with_a_reason(auth_client) -> None:
    created = await auth_client.post(f"{BASE}/investigations", json={"name": "case"})
    response = await auth_client.post(
        f"{BASE}/investigations/{created.json()['id']}/targets",
        json={"targets": [{"value": "  ", "type": "email"}]},
    )
    assert response.status_code == 422


async def test_scan_requires_targets(auth_client) -> None:
    created = await auth_client.post(f"{BASE}/investigations", json={"name": "empty"})
    response = await auth_client.post(
        f"{BASE}/investigations/{created.json()['id']}/scan", json={}
    )
    assert response.status_code == 409


async def test_scan_rejects_unknown_providers(auth_client) -> None:
    created = await auth_client.post(
        f"{BASE}/investigations", json={"name": "c", "targets": [{"value": "example_user"}]}
    )
    response = await auth_client.post(
        f"{BASE}/investigations/{created.json()['id']}/scan",
        json={"providers": ["definitely-not-a-provider"]},
    )
    assert response.status_code == 422


async def test_another_analysts_investigation_is_invisible(client) -> None:
    async def register_and_login(username: str) -> str:
        await client.post(
            f"{BASE}/auth/register",
            json={"email": f"{username}@example.com", "username": username, "password": PASSWORD},
        )
        tokens = await client.post(
            f"{BASE}/auth/login", json={"username": username, "password": PASSWORD}
        )
        return str(tokens.json()["access_token"])

    admin_token = await register_and_login("admin_user")
    analyst_a = await register_and_login("analyst_a")
    analyst_b = await register_and_login("analyst_b")

    created = await client.post(
        f"{BASE}/investigations",
        json={"name": "private case"},
        headers={"Authorization": f"Bearer {analyst_a}"},
    )
    investigation_id = created.json()["id"]

    denied = await client.get(
        f"{BASE}/investigations/{investigation_id}",
        headers={"Authorization": f"Bearer {analyst_b}"},
    )
    assert denied.status_code == 404, "existence of another analyst's case must not leak"

    allowed = await client.get(
        f"{BASE}/investigations/{investigation_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert allowed.status_code == 200


async def test_viewer_cannot_write_and_analyst_cannot_delete(client) -> None:
    await client.post(
        f"{BASE}/auth/register",
        json={"email": "admin@example.com", "username": "root", "password": PASSWORD},
    )
    admin = (await client.post(
        f"{BASE}/auth/login", json={"username": "root", "password": PASSWORD}
    )).json()["access_token"]

    for username, role in (("viewer1", "viewer"), ("analyst1", "analyst")):
        await client.post(
            f"{BASE}/auth/register",
            json={"email": f"{username}@example.com", "username": username,
                  "password": PASSWORD, "role": role},
        )
    viewer = (await client.post(
        f"{BASE}/auth/login", json={"username": "viewer1", "password": PASSWORD}
    )).json()["access_token"]
    analyst = (await client.post(
        f"{BASE}/auth/login", json={"username": "analyst1", "password": PASSWORD}
    )).json()["access_token"]

    denied = await client.post(
        f"{BASE}/investigations", json={"name": "nope"},
        headers={"Authorization": f"Bearer {viewer}"},
    )
    assert denied.status_code == 403

    created = await client.post(
        f"{BASE}/investigations", json={"name": "case"},
        headers={"Authorization": f"Bearer {analyst}"},
    )
    assert created.status_code == 201
    investigation_id = created.json()["id"]

    cannot_delete = await client.delete(
        f"{BASE}/investigations/{investigation_id}",
        headers={"Authorization": f"Bearer {analyst}"},
    )
    assert cannot_delete.status_code == 403

    admin_deletes = await client.delete(
        f"{BASE}/investigations/{investigation_id}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert admin_deletes.status_code == 204


async def test_source_registry_lists_providers_and_toggles_them(auth_client) -> None:
    listed = await auth_client.get(f"{BASE}/sources")
    assert listed.status_code == 200
    sources = {s["name"]: s for s in listed.json()}
    assert {"github", "dns", "whois", "username_enum"} <= set(sources)
    assert sources["github"]["accepts"]
    assert sources["maigret"]["health"] in ("ok", "unavailable")

    patched = await auth_client.patch(f"{BASE}/sources/github", json={"enabled": False, "rpm": 12})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["rate_limit"]["rpm"] == 12


async def test_source_api_keys_are_masked(auth_client) -> None:
    await auth_client.get(f"{BASE}/sources")
    patched = await auth_client.patch(
        f"{BASE}/sources/github", json={"config": {"api_key": "ghp_secret_value_here"}}
    )
    assert patched.status_code == 200
    assert "ghp_secret_value_here" not in patched.text
    assert patched.json()["coverage"]["config"]["api_key"] == "********"


async def test_security_headers_are_present(client) -> None:
    response = await client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


async def test_ai_reports_disabled_by_default(auth_client) -> None:
    response = await auth_client.get(f"{BASE}/ai/status")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


@pytest.mark.parametrize(
    ("payload", "expected_field"),
    [
        ({"email": "a@example.com", "username": "analyst", "password": "Password123"}, "password"),
        ({"email": "a@example.com", "username": "analyst", "password": "aaaaaaaaaaaa"}, "password"),
        ({"email": "a@example.com", "username": "ab", "password": PASSWORD}, "username"),
        ({"email": "a@example.com", "username": "my user", "password": PASSWORD}, "username"),
        ({"email": "admin@localhost", "username": "analyst", "password": PASSWORD}, "email"),
        (
            {"email": "a@example.com", "username": "analyst", "password": PASSWORD, "confirm": "x"},
            "confirm",
        ),
    ],
)
async def test_register_rejection_names_the_broken_rule(
    client, payload: dict[str, str], expected_field: str
) -> None:
    """The client renders `fields`; if this contract drops, users get an unactionable error.

    This is the backend half of the fix for the 422 that could not be diagnosed from the
    UI — the frontend's `messageFrom()` depends on every one of these keys being present.
    """
    response = await client.post(f"{BASE}/auth/register", json=payload)
    assert response.status_code == 422, response.text

    detail = response.json()["detail"]
    assert detail["code"] == "validation_error"
    assert detail["fields"], "a validation error must name the field that failed"

    for field in detail["fields"]:
        assert field["loc"], "each field error needs a loc the client can label"
        assert field["msg"].strip(), "each field error needs a human-readable msg"

    named = {str(field["loc"][-1]) for field in detail["fields"]}
    assert expected_field in named, f"expected {expected_field} to be named, got {named}"


async def test_valid_registration_is_accepted(client) -> None:
    """The counterpart: the rules the form now advertises really do get through."""
    response = await client.post(
        f"{BASE}/auth/register",
        json={"email": "ok@example.com", "username": "an.aly-st_1", "password": "MyStr0ngPass!2026"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["username"] == "an.aly-st_1"
