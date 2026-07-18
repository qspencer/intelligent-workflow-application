"""ROLES_PLAN §7 — the S2 tenant-isolation acceptance criteria, test-pinned.

Two orgs (default + acme), one user in each, resources in both. The criteria:
1. Org A never reads org B's workflows/instances/steps/audit/cost — lists
   filter, direct access 404s (not 403 — no existence leaks).
2. Org A cannot run/retry/kill/fork/dry-run org B resources.
4. Organization Viewer: 403 on every spend/mutation surface, 200 on reads.
5. Administrator cross-org mutations carry `org_bypass: true`.
6. The WS stream never delivers org B events to an org A subscriber;
   instance-less system events reach Administrators only.
6b. Escalations listing/resolution is org-scoped.
(3 — user-management guards — is pinned in test_users_api.py; 7 — the 0005
data migration — in test_role_migration_mapping below.)
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.events import EventBus
from workflow_platform.main import create_app
from workflow_platform.persistence import (
    AuditEntry,
    Organization,
    StepExecution,
    User,
    WorkflowInstance,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition

_ADMIN = {"X-Dev-User": "root", "X-Dev-Groups": "admins"}
_ACME_USER = {"X-Dev-User": "acme-u", "X-Dev-Groups": "org-users"}
_ACME_VIEWER = {"X-Dev-User": "acme-v", "X-Dev-Groups": "org-viewers"}
_DEFAULT_USER = {"X-Dev-User": "def-u", "X-Dev-Groups": "org-users"}


def _definition(wf_id: str) -> Any:
    return load_definition(
        {
            "id": wf_id,
            "name": wf_id,
            "trigger": {"type": "manual"},
            "steps": [{"id": "s1", "type": "deterministic", "function": "noop"}],
            "edges": [],
        }
    )


def _two_org_app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any, EventBus]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    events = EventBus()

    async def _seed() -> None:
        await repos.organizations.save(Organization(id="acme", name="acme"))
        for sub, org in [("acme-u", "acme"), ("acme-v", "acme"), ("def-u", "default")]:
            await repos.users.save(User(iss="dev", sub=sub, org_id=org))
        await repos.definitions.save(_definition("wf-default"), org_id="default")
        await repos.definitions.save(_definition("wf-acme"), org_id="acme")
        for iid, wf, org in [("i-default", "wf-default", "default"), ("i-acme", "wf-acme", "acme")]:
            await repos.instances.create(
                WorkflowInstance(id=iid, workflow_id=wf, org_id=org, state="failed")
            )
            await repos.steps.create(
                StepExecution(
                    id=f"{iid}-s1",
                    instance_id=iid,
                    step_id="s1",
                    state="completed",
                    output={"cost_usd": 0.5, "model": "m", "usage": {"total_tokens": 10}},
                )
            )
            await repos.audit.append(
                AuditEntry(
                    actor_type="engine",
                    actor_id="x",
                    action="workflow_started",
                    workflow_instance_id=iid,
                )
            )
        # An instance-less system entry (platform-operator data).
        await repos.audit.append(
            AuditEntry(actor_type="monitoring", actor_id="m", action="alert_high_queue_depth")
        )

    asyncio.run(_seed())
    return TestClient(create_app(repositories=repos, events=events)), repos, events


# --- Criterion 1: reads are invisible across orgs ---


def test_cross_org_reads_are_invisible(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = _two_org_app(monkeypatch)

    workflows = {w["id"] for w in client.get("/api/workflows", headers=_ACME_USER).json()}
    assert workflows == {"wf-acme"}
    assert client.get("/api/workflows/wf-default", headers=_ACME_USER).status_code == 404
    assert client.get("/api/workflows/wf-default/export", headers=_ACME_USER).status_code == 404
    assert (
        client.get("/api/workflows/wf-default/cost-estimate", headers=_ACME_USER).status_code == 404
    )
    assert (
        client.get("/api/workflows/wf-default/capabilities", headers=_ACME_USER).status_code == 404
    )

    instances = {i["id"] for i in client.get("/api/workflow-instances", headers=_ACME_USER).json()}
    assert instances == {"i-acme"}
    assert client.get("/api/workflow-instances/i-default", headers=_ACME_USER).status_code == 404
    assert (
        client.get(
            "/api/workflow-instances/i-default/steps/s1/explain", headers=_ACME_USER
        ).status_code
        == 404
    )

    audit_instances = {
        e["workflow_instance_id"] for e in client.get("/api/audit", headers=_ACME_USER).json()
    }
    assert audit_instances == {"i-acme"}
    assert (
        client.get(
            "/api/audit", params={"instance_id": "i-default"}, headers=_ACME_USER
        ).status_code
        == 404
    )

    cost_keys = {
        r["workflow_id"] for r in client.get("/api/cost/by-workflow", headers=_ACME_USER).json()
    }
    assert cost_keys == {"wf-acme"}

    counts = client.get("/api/workflows/instance-counts", headers=_ACME_USER).json()
    assert set(counts) == {"wf-acme"}


def test_admin_sees_everything_including_system_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = _two_org_app(monkeypatch)
    workflows = {w["id"] for w in client.get("/api/workflows", headers=_ADMIN).json()}
    assert workflows == {"wf-default", "wf-acme"}
    actions = {e["action"] for e in client.get("/api/audit", headers=_ADMIN).json()}
    assert "alert_high_queue_depth" in actions  # instance-less: operator-only
    acme_actions = {e["action"] for e in client.get("/api/audit", headers=_ACME_USER).json()}
    assert "alert_high_queue_depth" not in acme_actions


# --- Criterion 2: mutations are invisible across orgs ---


def test_cross_org_mutations_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = _two_org_app(monkeypatch)
    for method, path, body in [
        ("post", "/api/workflows/wf-default/run", {}),
        ("post", "/api/workflows/wf-default/dry-run", {}),
        ("post", "/api/workflow-instances/i-default/retry", None),
        ("post", "/api/workflow-instances/i-default/kill", None),
        ("post", "/api/workflow-instances/i-default/fork", {"from_step_id": "s1"}),
        ("delete", "/api/workflow-instances/i-default", None),
        ("delete", "/api/workflows/wf-default", None),
    ]:
        r = getattr(client, method)(
            path, headers=_ACME_USER, **({"json": body} if body is not None else {})
        )
        assert r.status_code == 404, (path, r.status_code, r.text)


def test_bulk_delete_scoped_to_own_org(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos, _ = _two_org_app(monkeypatch)
    r = client.delete("/api/workflow-instances", params={"state": "failed"}, headers=_ACME_USER)
    assert r.json()["deleted_instances"] == 1  # only i-acme
    assert asyncio.run(repos.instances.get("i-default")) is not None


# --- Criterion 4: Organization Viewer read/write matrix ---


def test_org_viewer_reads_but_never_spends(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = _two_org_app(monkeypatch)
    for path in [
        "/api/workflows",
        "/api/workflows/wf-acme",
        "/api/workflow-instances",
        "/api/workflow-instances/i-acme",
        "/api/audit",
        "/api/cost/by-workflow",
        "/api/workflows/wf-acme/cost-estimate",
        "/api/escalations",
    ]:
        assert client.get(path, headers=_ACME_VIEWER).status_code == 200, path
    for method, path in [
        ("post", "/api/workflows/wf-acme/run"),
        ("post", "/api/workflows/wf-acme/dry-run"),
        ("post", "/api/workflow-instances/i-acme/retry"),
        ("post", "/api/workflow-instances/i-acme/kill"),
        ("post", "/api/workflows"),
        ("post", "/api/workflows/import"),
        ("delete", "/api/workflows/wf-acme"),
    ]:
        r = getattr(client, method)(path, headers=_ACME_VIEWER)
        assert r.status_code == 403, (path, r.status_code)


# --- Criterion 5: Administrator bypass is audited ---


def test_admin_cross_org_mutation_audits_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos, _ = _two_org_app(monkeypatch)
    # root's JIT row lands in the default org; killing an acme instance is
    # a cross-org act.
    r = client.post("/api/workflow-instances/i-acme/retry", headers=_ADMIN)
    assert r.status_code in (200, 503)  # no engine bound → 503 after the check
    entries = asyncio.run(repos.audit.list_recent(limit=50))
    bypasses = [e for e in entries if e.detail.get("org_bypass") is True]
    assert bypasses, "expected an org_bypass audit entry for the cross-org retry"
    assert bypasses[-1].actor_id == "root"


# --- Criterion 6: WS delivery is org-filtered ---


def test_ws_never_delivers_foreign_org_events(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, events = _two_org_app(monkeypatch)

    async def _publish_all() -> None:
        await events.publish({"action": "a-acme", "org_id": "acme"})
        await events.publish({"action": "a-default", "org_id": "default"})
        await events.publish({"action": "a-system"})  # instance-less

    with client.websocket_connect("/ws/events?user=acme-u&groups=org-users") as ws:
        asyncio.run(_publish_all())
        first = ws.receive_json()
        assert first["action"] == "a-acme"  # default-org + system events skipped

    with client.websocket_connect("/ws/events?user=root&groups=admins") as ws:
        asyncio.run(_publish_all())
        got = [ws.receive_json()["action"] for _ in range(3)]
        assert got == ["a-acme", "a-default", "a-system"]


# --- Criterion 6b: escalations are org-scoped ---


def test_escalations_scoped_by_instance_org(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos, _ = _two_org_app(monkeypatch)

    async def _seed_escalations() -> None:
        for iid in ("i-default", "i-acme"):
            await repos.audit.append(
                AuditEntry(
                    actor_type="agent",
                    actor_id="agent:x",
                    action="escalation_requested",
                    workflow_instance_id=iid,
                    detail={"reason": "stuck"},
                )
            )

    asyncio.run(_seed_escalations())
    acme_esc = client.get("/api/escalations", headers=_ACME_USER).json()
    assert {e["instance_id"] for e in acme_esc} == {"i-acme"}
    default_ids = [
        e["id"]
        for e in client.get("/api/escalations", headers=_ADMIN).json()
        if e["instance_id"] == "i-default"
    ]
    resolve = client.post(
        f"/api/escalations/{default_ids[0]}/resolve", json={"resolution": "no"}, headers=_ACME_USER
    )
    assert resolve.status_code == 404


# --- Criterion 7: the 0005 mapping retires every old role string ---


def test_role_migration_mapping_is_total() -> None:
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "20260718_2330_0005_role_vocabulary.py"
    )
    spec = importlib.util.spec_from_file_location("mig0005", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old_roles = {"Admin", "Workflow Designer", "Operator", "Viewer", "Auditor"}
    assert set(module._FORWARD) == old_roles
    from workflow_platform.auth import Role

    assert set(module._FORWARD.values()) <= {r.value for r in Role}
