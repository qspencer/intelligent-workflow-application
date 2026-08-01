"""Grants admin API (docs/TRACE_GOVERNANCE_PLAN.md §2/§2.1, TG1): request /
approve / revoke / list, Administrator-gated, plus an end-to-end grant→raw
read through the real HTTP surface."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.main import create_app
from workflow_platform.persistence import (
    AuditEntry,
    Organization,
    User,
    WorkflowInstance,
    in_memory_repositories,
)

_ADMIN = {"X-Dev-User": "root", "X-Dev-Groups": "admins"}
_ADMIN2 = {"X-Dev-User": "root2", "X-Dev-Groups": "admins"}
_USER = {"X-Dev-User": "bob", "X-Dev-Groups": "org-users"}


def _app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any, str]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()

    async def seed() -> str:
        for sub in ("root", "root2"):
            await repos.users.save(
                User(iss="dev", sub=sub, org_id="default", roles=["Administrator"])
            )
        bob = User(iss="dev", sub="bob", org_id="default", roles=["Organization User"])
        await repos.users.save(bob)
        return bob.id

    bob_id = asyncio.run(seed())
    return TestClient(create_app(repositories=repos)), repos, bob_id


def _future() -> str:
    return (datetime.now(UTC) + timedelta(days=1)).isoformat()


def test_org_grant_lifecycle_via_api(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, bob_id = _app(monkeypatch)
    resp = client.post(
        "/api/raw-trace-grants",
        headers=_ADMIN,
        json={"principal_id": bob_id, "org_id": "default", "reason_code": "debugging"},
    )
    assert resp.status_code == 201
    grant = resp.json()
    assert grant["state"] == "active"  # a distinct issuing admin IS the authorization
    assert grant["org_id"] == "default"

    listing = client.get("/api/raw-trace-grants", headers=_ADMIN).json()
    assert any(g["id"] == grant["id"] for g in listing)

    revoked = client.post(f"/api/raw-trace-grants/{grant['id']}/revoke", headers=_ADMIN2).json()
    assert revoked["state"] == "revoked"


def test_platform_wide_needs_two_distinct_admins_via_api(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, bob_id = _app(monkeypatch)
    pending = client.post(
        "/api/raw-trace-grants",
        headers=_ADMIN,
        json={
            "principal_id": bob_id,
            "reason_code": "incident_investigation",
            "expires_at": _future(),
        },
    ).json()
    assert pending["state"] == "pending"
    # the requester cannot also approve
    same = client.post(f"/api/raw-trace-grants/{pending['id']}/approve", headers=_ADMIN, json={})
    assert same.status_code == 400
    # a distinct Administrator activates it
    ok = client.post(f"/api/raw-trace-grants/{pending['id']}/approve", headers=_ADMIN2, json={})
    assert ok.status_code == 200 and ok.json()["state"] == "active"


def test_role_gating_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, bob_id = _app(monkeypatch)
    # non-admin forbidden
    assert (
        client.post(
            "/api/raw-trace-grants",
            headers=_USER,
            json={"principal_id": bob_id, "org_id": "default", "reason_code": "debugging"},
        ).status_code
        == 403
    )
    assert client.get("/api/raw-trace-grants", headers=_USER).status_code == 403
    # unknown principal -> 404
    assert (
        client.post(
            "/api/raw-trace-grants",
            headers=_ADMIN,
            json={"principal_id": "nope", "org_id": "default", "reason_code": "debugging"},
        ).status_code
        == 404
    )
    # duplicate active -> 409
    body = {"principal_id": bob_id, "org_id": "default", "reason_code": "debugging"}
    assert client.post("/api/raw-trace-grants", headers=_ADMIN, json=body).status_code == 201
    assert client.post("/api/raw-trace-grants", headers=_ADMIN, json=body).status_code == 409


def test_end_to_end_grant_unlocks_raw_read(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos, bob_id = _app(monkeypatch)
    secret = "GRANT-E2E-SECRET-body"

    async def seed_instance() -> None:
        await repos.instances.create(
            WorkflowInstance(id="i-e2e", workflow_id="wf", org_id="default")
        )
        await repos.audit.append(
            AuditEntry(
                actor_type="agent",
                actor_id="a",
                action="tool_call",
                workflow_instance_id="i-e2e",
                detail={"name": "file_read", "input": {"body": secret}, "result": {"content": "x"}},
            )
        )

    asyncio.run(seed_instance())
    path = "/api/workflow-instances/i-e2e/audit"

    # Before a grant: bob (and even an ungranted admin) get projected.
    assert secret not in client.get(path, headers=_USER).text
    assert secret not in client.get(path, headers=_ADMIN).text

    # Grant bob raw for org 'default' through the API, then bob reads raw.
    client.post(
        "/api/raw-trace-grants",
        headers=_ADMIN,
        json={"principal_id": bob_id, "org_id": "default", "reason_code": "customer_support"},
    )
    assert secret in client.get(path, headers=_USER).text


def test_org_transfer_revokes_grants(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raw-trace grant must not survive a user's org move (criterion 11) —
    an org-scoped grant for the old org would otherwise travel with them."""
    client, repos, bob_id = _app(monkeypatch)
    asyncio.run(repos.organizations.save(Organization(id="acme", name="acme")))
    client.post(
        "/api/raw-trace-grants",
        headers=_ADMIN,
        json={"principal_id": bob_id, "org_id": "default", "reason_code": "debugging"},
    )
    assert any(
        g["principal_id"] == bob_id and g["state"] == "active"
        for g in client.get("/api/raw-trace-grants", headers=_ADMIN).json()
    )
    # Administrator moves bob to another org → his grants are revoked.
    moved = client.patch(f"/api/users/{bob_id}", headers=_ADMIN, json={"org_id": "acme"})
    assert moved.status_code == 200
    bob_grants = [
        g
        for g in client.get("/api/raw-trace-grants", headers=_ADMIN).json()
        if g["principal_id"] == bob_id
    ]
    assert bob_grants and all(g["state"] == "revoked" for g in bob_grants)
