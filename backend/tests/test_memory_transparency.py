"""VERACIUM_041_ADOPTION_PLAN §2: the memory transparency surface."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories

ADMIN = {"X-Dev-User": "admin", "X-Dev-Groups": "admins"}
ORG_ADMIN = {"X-Dev-User": "oa", "X-Dev-Groups": "org-admins"}
ORG_USER = {"X-Dev-User": "ou", "X-Dev-Groups": "org-users"}
VIEWER = {"X-Dev-User": "v", "X-Dev-Groups": "org-viewers"}


class FakeLearnedMemory:
    """Stands in for LearnedMemoryService: two orgs + legacy ids."""

    def __init__(self) -> None:
        self.introspect_calls: list[tuple[str, str]] = []

    async def list_memory_namespaces(self) -> dict[str, Any]:
        return {
            "namespaces": [
                {"org_id": "default", "account": "q@x.com", "edges": 40, "episodes": 12},
                {"org_id": "acme", "account": "a@acme.com", "edges": 7, "episodes": 2},
            ],
            "unrecognized_ids": 3,
        }

    async def introspect_namespace(self, namespace: str, mode: str = "summary") -> dict[str, Any]:
        self.introspect_calls.append((namespace, mode))
        if mode == "categories":
            return {
                "relations": {"third_party_claim": 30},
                "facts": {"x": ["<script>alert(1)</script> claim"]},
            }
        return {"relations": {"third_party_claim": 30}}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_MODE", "dev")
    repos = in_memory_repositories()
    app = create_app(repositories=repos, start_triggers=False)
    return TestClient(app)


def _wire_fake(client: TestClient) -> FakeLearnedMemory:
    fake = FakeLearnedMemory()
    app: Any = client.app
    engine = app.state.engine
    assert engine is not None
    engine.learned_memory = fake
    return fake


def test_summary_scoping_and_unrecognized_ids(client: TestClient) -> None:
    fake = _wire_fake(client)

    admin_view = client.get("/api/memory/summary", headers=ADMIN).json()
    assert {n["org_id"] for n in admin_view["namespaces"]} == {"default", "acme"}
    assert admin_view["unrecognized_ids"] == 3

    org_view = client.get("/api/memory/summary", headers=ORG_ADMIN).json()
    assert {n["org_id"] for n in org_view["namespaces"]} == {"default"}
    assert "unrecognized_ids" not in org_view

    assert client.get("/api/memory/summary", headers=ORG_USER).status_code == 403
    assert client.get("/api/memory/summary", headers=VIEWER).status_code == 403
    assert fake.introspect_calls == []


def test_introspect_scoping_audit_and_bypass(client: TestClient) -> None:
    _wire_fake(client)

    ok = client.get("/api/memory/summary/default/q@x.com", headers=ORG_ADMIN)
    assert ok.status_code == 200
    assert ok.json()["namespace"] == "org:default:user:q@x.com"

    # Cross-org for a non-Administrator: 404, not 403 (no existence leak).
    assert client.get("/api/memory/summary/acme/a@acme.com", headers=ORG_ADMIN).status_code == 404
    # Garbage: 404, never 500 (schemathesis will fuzz this shape).
    assert client.get("/api/memory/summary/default/garbage", headers=ADMIN).status_code == 404
    assert client.get("/api/memory/summary/nope/x@y.com", headers=ADMIN).status_code == 404

    # Administrator cross-org works and is audited with org_bypass.
    cross = client.get("/api/memory/summary/acme/a@acme.com", headers=ADMIN)
    assert cross.status_code == 200
    audit = client.get("/api/audit?limit=10", headers=ADMIN).json()
    introspected = [e for e in audit if e["action"] == "memory_introspected"]
    assert introspected, "introspect reads must be audited"
    assert any(e["detail"].get("org_bypass") for e in introspected)


def test_categories_mode_and_bad_mode(client: TestClient) -> None:
    fake = _wire_fake(client)
    resp = client.get("/api/memory/summary/default/q@x.com?mode=categories", headers=ADMIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is False
    # Facts arrive verbatim — including script-looking mail content — as
    # DATA; rendering safety is the frontend's text-node-only contract.
    assert "<script>" in str(body["facts"])
    assert ("org:default:user:q@x.com", "categories") in fake.introspect_calls

    assert (
        client.get("/api/memory/summary/default/q@x.com?mode=wat", headers=ADMIN).status_code == 400
    )
