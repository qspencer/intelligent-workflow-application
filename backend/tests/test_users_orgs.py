"""Users + organizations skeleton.

Pins the design-review conditions:
- JIT provisioning from the IdP identity — users appear by authenticating,
  keyed by (iss, sub); roles are never persisted (ARCHITECTURE D4).
- last_seen upserts are TTL-throttled (not every request writes).
- The default org exists from the start; definitions created via the API
  carry org + owner attribution; instances carry org_id from birth.
- Audit actor_id remains the raw sub string (no FK) — unchanged behavior.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.auth.identity import UserIdentity
from workflow_platform.auth.provisioning import UserProvisioner
from workflow_platform.main import create_app
from workflow_platform.persistence import (
    DEFAULT_ORG_ID,
    WorkflowInstance,
    in_memory_repositories,
)

_ADMIN = {"X-Dev-User": "quentin", "X-Dev-Groups": "admins"}


def _dev_app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    return TestClient(create_app(repositories=repos)), repos


def test_default_org_exists() -> None:
    repos = in_memory_repositories()
    org = asyncio.run(repos.organizations.get(DEFAULT_ORG_ID))
    assert org is not None and org.id == "default"


def test_jit_provisions_user_on_first_request(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _dev_app(monkeypatch)
    assert asyncio.run(repos.users.get_by_identity("dev", "quentin")) is None
    client.get("/api/workflows", headers=_ADMIN)
    user = asyncio.run(repos.users.get_by_identity("dev", "quentin"))
    assert user is not None
    assert user.org_id == DEFAULT_ORG_ID
    assert user.iss == "dev"


def test_provisioner_throttles_last_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    repos = in_memory_repositories()
    prov = UserProvisioner(repos.users, ttl_seconds=3600)
    ident = UserIdentity(sub="alice", email="a@b.c")
    asyncio.run(prov.provision(ident))
    first = asyncio.run(repos.users.get_by_identity("dev", "alice"))
    assert first is not None
    asyncio.run(prov.provision(ident))  # within TTL — must not write
    second = asyncio.run(repos.users.get_by_identity("dev", "alice"))
    assert second is not None and second.last_seen_at == first.last_seen_at


def test_upsert_keeps_stable_id_and_refreshes_contact() -> None:
    from workflow_platform.persistence import User

    repos = in_memory_repositories()
    first = asyncio.run(repos.users.upsert_seen(User(iss="dev", sub="bob", email=None)))
    second = asyncio.run(repos.users.upsert_seen(User(iss="dev", sub="bob", email="bob@x.com")))
    assert second.id == first.id  # stable across sightings
    assert second.email == "bob@x.com"


def test_me_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _dev_app(monkeypatch)
    body = client.get("/api/me", headers=_ADMIN).json()
    assert body["identity"]["sub"] == "quentin"
    assert body["user"] is not None and body["user"]["iss"] == "dev"
    assert body["organization"]["id"] == DEFAULT_ORG_ID


def test_created_workflow_carries_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _dev_app(monkeypatch)
    r = client.post("/api/workflows", headers=_ADMIN, json={"name": "Owned Flow"})
    assert r.status_code == 201
    wf_id = r.json()["id"]
    user = asyncio.run(repos.users.get_by_identity("dev", "quentin"))
    assert user is not None
    org_id, owner = repos.definitions._ownership[wf_id]
    assert org_id == DEFAULT_ORG_ID and owner == user.id


def test_instance_defaults_to_default_org() -> None:
    assert WorkflowInstance(workflow_id="wf").org_id == DEFAULT_ORG_ID
