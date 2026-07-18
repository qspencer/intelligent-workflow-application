"""Organization lifecycle (ROLES_PLAN S3): create/rename/list + user moves.

Deletion is deliberately absent (§8: orgs are rename-only until one needs
deleting) — pinned by the 405 test.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.main import create_app
from workflow_platform.persistence import User, in_memory_repositories

_ADMIN = {"X-Dev-User": "root", "X-Dev-Groups": "admins"}
_ORG_ADMIN = {"X-Dev-User": "oa", "X-Dev-Groups": "org-admins"}
_ORG_USER = {"X-Dev-User": "ou", "X-Dev-Groups": "org-users"}


def _app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    asyncio.run(repos.users.save(User(iss="dev", sub="oa", org_id="default")))
    return TestClient(create_app(repositories=repos)), repos


def test_create_rename_list(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _app(monkeypatch)
    created = client.post("/api/organizations", json={"name": "Acme Corp"}, headers=_ADMIN)
    assert created.status_code == 201
    assert created.json()["id"] == "acme-corp"  # slugified

    dup = client.post("/api/organizations", json={"name": "x", "id": "acme-corp"}, headers=_ADMIN)
    assert dup.status_code == 409

    renamed = client.patch(
        "/api/organizations/acme-corp", json={"name": "Acme Inc"}, headers=_ADMIN
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "Acme Inc"

    ids = {o["id"] for o in client.get("/api/organizations", headers=_ADMIN).json()}
    assert ids == {"default", "acme-corp"}


def test_writes_are_administrator_only(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _app(monkeypatch)
    assert (
        client.post("/api/organizations", json={"name": "x"}, headers=_ORG_ADMIN).status_code == 403
    )
    assert (
        client.patch(
            "/api/organizations/default", json={"name": "x"}, headers=_ORG_ADMIN
        ).status_code
        == 403
    )
    assert client.get("/api/organizations", headers=_ORG_USER).status_code == 403
    # Org Admins read their own org only.
    own = client.get("/api/organizations", headers=_ORG_ADMIN).json()
    assert [o["id"] for o in own] == ["default"]


def test_deletion_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _app(monkeypatch)
    assert client.delete("/api/organizations/default", headers=_ADMIN).status_code == 405


def test_administrator_moves_user_between_orgs(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _app(monkeypatch)
    client.post("/api/organizations", json={"name": "acme"}, headers=_ADMIN)
    uid = client.post(
        "/api/users",
        json={"email": "u@y.z", "password": "longenough", "roles": ["Organization User"]},
        headers=_ADMIN,
    ).json()["id"]

    moved = client.patch(f"/api/users/{uid}", json={"org_id": "acme"}, headers=_ADMIN)
    assert moved.status_code == 200 and moved.json()["org_id"] == "acme"
    ghost = client.patch(f"/api/users/{uid}", json={"org_id": "ghost"}, headers=_ADMIN)
    assert ghost.status_code == 400
    # Org Admins cannot move users.
    denied = client.patch(f"/api/users/{uid}", json={"org_id": "default"}, headers=_ORG_ADMIN)
    assert denied.status_code in (403, 404)  # 404: acme user invisible to default Org Admin


def test_move_guard_protects_old_orgs_last_org_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _app(monkeypatch)
    client.post("/api/organizations", json={"name": "acme"}, headers=_ADMIN)
    only = client.post(
        "/api/users",
        json={
            "email": "oa@acme.co",
            "password": "longenough",
            "roles": ["Organization Administrator"],
            "org_id": "acme",
        },
        headers=_ADMIN,
    ).json()["id"]
    # Moving acme's only Org Admin out would leave acme unadministered.
    blocked = client.patch(f"/api/users/{only}", json={"org_id": "default"}, headers=_ADMIN)
    assert blocked.status_code == 409
