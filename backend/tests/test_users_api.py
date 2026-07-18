"""User management API (docs/AUTH_PLAN.md §6): Admin-gated CRUD over
local-auth users, with the last-admin guard and revocation-on-change."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.auth.local import hash_token
from workflow_platform.main import create_app
from workflow_platform.persistence import AuthSession, in_memory_repositories
from workflow_platform.persistence.models import _utcnow

_ADMIN = {"X-Dev-User": "root", "X-Dev-Groups": "admins"}
_VIEWER = {"X-Dev-User": "eve", "X-Dev-Groups": "viewers"}


def _dev_app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    return TestClient(create_app(repositories=repos)), repos


def _create(client: TestClient, email: str, roles: list[str], password: str = "longenough") -> Any:
    return client.post(
        "/api/users",
        json={"email": email, "password": password, "roles": roles},
        headers=_ADMIN,
    )


def test_create_list_and_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _dev_app(monkeypatch)
    created = _create(client, "Alice@Example.com", ["Admin"])
    assert created.status_code == 201
    body = created.json()
    assert body["email"] == "alice@example.com"  # canonicalized
    assert body["iss"] == "local" and body["sub"] == body["id"]
    assert body["has_password"] is True
    assert "password_hash" not in body

    listed = client.get("/api/users", headers=_ADMIN)
    assert listed.status_code == 200
    # The dev identity making the requests is JIT-provisioned alongside.
    locals_ = [u for u in listed.json() if u["iss"] == "local"]
    assert [u["email"] for u in locals_] == ["alice@example.com"]


def test_admin_gate_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _dev_app(monkeypatch)
    assert client.get("/api/users", headers=_VIEWER).status_code == 403
    assert _create(client, "x@y.z", ["Superuser"]).status_code == 400
    assert _create(client, "x@y.z", ["Viewer"], password="short").status_code == 400
    assert _create(client, "not-an-email", ["Viewer"]).status_code == 400
    assert _create(client, "dup@y.z", ["Viewer"]).status_code == 201
    assert _create(client, "DUP@y.z", ["Viewer"]).status_code == 409


def test_patch_revokes_sessions_on_deactivate(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _dev_app(monkeypatch)
    user_id = _create(client, "op@y.z", ["Operator"]).json()["id"]
    asyncio.run(
        repos.auth_sessions.create(
            AuthSession(
                user_id=user_id,
                token_hash=hash_token("tok"),
                expires_at=_utcnow() + timedelta(hours=1),
            )
        )
    )
    updated = client.patch(f"/api/users/{user_id}", json={"is_active": False}, headers=_ADMIN)
    assert updated.status_code == 200 and updated.json()["is_active"] is False
    assert asyncio.run(repos.auth_sessions.get_by_token_hash(hash_token("tok"))) is None


def test_last_active_admin_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _dev_app(monkeypatch)
    only_admin = _create(client, "boss@y.z", ["Admin"]).json()["id"]
    # Demoting or deactivating the only active Admin is refused.
    demote = client.patch(f"/api/users/{only_admin}", json={"roles": ["Viewer"]}, headers=_ADMIN)
    assert demote.status_code == 409
    deactivate = client.patch(f"/api/users/{only_admin}", json={"is_active": False}, headers=_ADMIN)
    assert deactivate.status_code == 409
    # With a second active Admin it proceeds.
    _create(client, "boss2@y.z", ["Admin"])
    assert (
        client.patch(f"/api/users/{only_admin}", json={"roles": ["Viewer"]}, headers=_ADMIN)
    ).status_code == 200


def test_sso_rows_reject_credential_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _dev_app(monkeypatch)
    client.get("/api/workflows", headers=_ADMIN)  # JIT-provision the dev identity
    users = client.get("/api/users", headers=_ADMIN).json()
    dev_row = next(u for u in users if u["iss"] == "dev")
    denied = client.patch(f"/api/users/{dev_row['id']}", json={"roles": ["Admin"]}, headers=_ADMIN)
    assert denied.status_code == 400
    # Contact-field edits are fine for any row.
    renamed = client.patch(
        f"/api/users/{dev_row['id']}", json={"display_name": "Quentin"}, headers=_ADMIN
    )
    assert renamed.status_code == 200 and renamed.json()["display_name"] == "Quentin"
