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
from starlette.websockets import WebSocketDisconnect

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
_ACME_ADMIN = {"X-Dev-User": "acme-a", "X-Dev-Groups": "org-admins"}
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
        for sub, org in [
            ("acme-u", "acme"),
            ("acme-a", "acme"),
            ("acme-v", "acme"),
            ("def-u", "default"),
        ]:
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
    # Org-User-reachable mutations: cross-org target is invisible (404).
    for method, path, body in [
        ("post", "/api/workflows/wf-default/run", {}),
        ("post", "/api/workflows/wf-default/dry-run", {}),
        ("post", "/api/workflow-instances/i-default/retry", None),
        ("post", "/api/workflow-instances/i-default/kill", None),
        ("post", "/api/workflow-instances/i-default/fork", {"from_step_id": "s1"}),
        ("delete", "/api/workflow-instances/i-default", None),
    ]:
        r = getattr(client, method)(
            path, headers=_ACME_USER, **({"json": body} if body is not None else {})
        )
        assert r.status_code == 404, (path, r.status_code, r.text)

    # Workflow deletion is Org-Admin+ (external review finding 5). Tested
    # with an acme Org Admin so the role gate passes and the cross-org
    # SCOPE check is what produces the 404 — not the role denial.
    r = client.delete("/api/workflows/wf-default", headers=_ACME_ADMIN)
    assert r.status_code == 404, ("delete wf-default as acme-admin", r.status_code, r.text)
    # An Org User is denied delete uniformly (role, not scope) — 403 leaks
    # nothing org-specific since it's identical for their own org.
    r = client.delete("/api/workflows/wf-default", headers=_ACME_USER)
    assert r.status_code == 403, ("org-user delete denied by role", r.status_code)


def test_bulk_delete_scoped_to_own_org(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos, _ = _two_org_app(monkeypatch)
    r = client.delete("/api/workflow-instances", params={"state": "failed"}, headers=_ACME_USER)
    # Stronger postconditions (external review finding 14): success status,
    # the acme instance actually gone, the foreign instance untouched.
    assert r.status_code == 200
    assert r.json()["deleted_instances"] == 1  # only i-acme
    assert asyncio.run(repos.instances.get("i-acme")) is None, "own-org instance deleted"
    assert asyncio.run(repos.instances.get("i-default")) is not None, "foreign untouched"


def test_positive_controls_own_org_actions_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paired positive controls (external review finding 13): a suite that
    rejected ALL mutations could pass much of the isolation set. Prove the
    OWN-org paths that must SUCCEED (or fail only for a non-authz reason)."""
    client, _, _ = _two_org_app(monkeypatch)
    # Org User reads + spends in own org: never 403/404.
    assert client.get("/api/workflows/wf-acme", headers=_ACME_USER).status_code == 200
    assert client.post("/api/workflows/wf-acme/run", headers=_ACME_USER, json={}).status_code != 403
    # Org Admin manages own-org workflow deletion (403 would mean the role
    # gate wrongly denied; 404 would mean scope wrongly hid own resource).
    assert client.delete("/api/workflows/wf-acme", headers=_ACME_ADMIN).status_code not in (
        403,
        404,
    )
    # Viewer reads own org but cannot spend.
    assert client.get("/api/workflows/wf-acme", headers=_ACME_VIEWER).status_code == 200
    assert (
        client.post("/api/workflows/wf-acme/run", headers=_ACME_VIEWER, json={}).status_code == 403
    )


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
    # root's JIT row lands in the default org; retrying an acme instance is
    # a cross-org act (external review finding 12: assert the FULL detail,
    # and fix the stale "killing" comment — the call is retry).
    r = client.post("/api/workflow-instances/i-acme/retry", headers=_ADMIN)
    assert r.status_code in (200, 503)  # no engine bound → 503 after the check
    entries = asyncio.run(repos.audit.list_recent(limit=50))
    bypasses = [e for e in entries if e.detail.get("org_bypass") is True]
    assert len(bypasses) == 1, "exactly one org_bypass entry for one cross-org act"
    entry = bypasses[0]
    assert entry.actor_id == "root"
    assert entry.action == "instance_retry_requested"
    assert entry.workflow_instance_id == "i-acme"  # the acme resource, not default
    assert entry.detail.get("org_bypass") is True

    # Negative control (finding 12 + 13): a SAME-org Administrator act must
    # NOT produce an org_bypass marker (the bypass tracer fires only when
    # the actor reaches across orgs; same-org effects are engine-audited).
    r2 = client.post("/api/workflow-instances/i-default/retry", headers=_ADMIN)
    assert r2.status_code in (200, 503)
    entries2 = asyncio.run(repos.audit.list_recent(limit=50))
    default_bypass = [
        e
        for e in entries2
        if e.workflow_instance_id == "i-default" and e.detail.get("org_bypass") is True
    ]
    assert not default_bypass, "a same-org Administrator action must never be flagged org_bypass"


# --- Criterion 6: WS delivery is org-filtered ---


def test_ws_never_delivers_foreign_org_events(monkeypatch: pytest.MonkeyPatch) -> None:
    # Forbidden events are published FIRST (external review 2026-07-31,
    # finding 8): an implementation that delivered everything unfiltered
    # would surface a-default before a-acme, so the first-message assert
    # below fails unless the receiver actually SKIPPED the two forbidden
    # events. The prior version published a-acme first and could pass on a
    # fully-broken filter.
    client, _, events = _two_org_app(monkeypatch)

    async def _publish_forbidden_then_allowed() -> None:
        await events.publish({"action": "a-default", "org_id": "default"})  # forbidden
        await events.publish({"action": "a-system"})  # instance-less, forbidden
        await events.publish({"action": "a-acme", "org_id": "acme"})  # the only allowed one

    with client.websocket_connect("/ws/events?user=acme-u&groups=org-users") as ws:
        asyncio.run(_publish_forbidden_then_allowed())
        assert ws.receive_json()["action"] == "a-acme"  # skipped both forbidden

    # Administrator (unscoped) receives all three, in order.
    with client.websocket_connect("/ws/events?user=root&groups=admins") as ws:
        asyncio.run(_publish_forbidden_then_allowed())
        got = [ws.receive_json()["action"] for _ in range(3)]
        assert got == ["a-default", "a-system", "a-acme"]


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


def test_event_deliverable_primitive_negative_and_positive() -> None:
    """Directly pin the WS org-filter primitive (external review finding 8):
    the delivery-path test proves skipping; this proves NON-delivery
    deterministically without relying on a blocking WebSocket read."""
    from workflow_platform.api.ws import event_deliverable

    # Scoped subscriber: only their own org's events.
    assert event_deliverable({"action": "x", "org_id": "acme"}, "acme") is True
    assert event_deliverable({"action": "x", "org_id": "default"}, "acme") is False
    assert event_deliverable({"action": "x"}, "acme") is False  # instance-less/system
    assert event_deliverable({"action": "x", "org_id": None}, "acme") is False  # malformed
    # Unscoped Administrator: everything.
    for ev in ({"org_id": "acme"}, {"org_id": "default"}, {}, {"org_id": None}):
        assert event_deliverable({**ev, "action": "x"}, None) is True


def test_raw_tool_payloads_hidden_from_non_admin_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    """External review 2026-08-01 finding 3: raw tool input/result (email
    bodies, file contents, error text) must NOT appear in ordinary audit
    responses — audit access must not become an intra-org read escalation.
    Admin-tier sees raw; Org User/Viewer see projected metadata."""
    client, repos, _ = _two_org_app(monkeypatch)

    async def _seed_tool_call() -> None:
        await repos.instances.create(
            WorkflowInstance(id="i-acme2", workflow_id="wf-acme", org_id="acme")
        )
        await repos.audit.append(
            AuditEntry(
                actor_type="agent",
                actor_id="agent:x",
                action="tool_call",
                workflow_instance_id="i-acme2",
                detail={
                    "name": "email_send",
                    "input": {"to": "victim@example.com", "subject": "SECRET", "body": "PRIVATE"},
                    "result": {"content": {"message_id": "m1"}, "error": None},
                },
            )
        )

    asyncio.run(_seed_tool_call())

    # Org User (read-only-ish, below admin tier): raw values withheld.
    user_view = client.get("/api/workflow-instances/i-acme2/audit", headers=_ACME_USER).json()
    tc = next(e for e in user_view if e["action"] == "tool_call")
    blob = str(tc["detail"])
    assert "PRIVATE" not in blob and "SECRET" not in blob and "victim@example.com" not in blob
    assert tc["detail"]["_redacted"]
    assert tc["detail"]["input_keys"] == ["body", "subject", "to"]  # keys ok, values not

    # Org Admin (admin tier): full raw payload.
    admin_view = client.get("/api/workflow-instances/i-acme2/audit", headers=_ACME_ADMIN).json()
    tc_admin = next(e for e in admin_view if e["action"] == "tool_call")
    assert tc_admin["detail"]["input"]["body"] == "PRIVATE"


def test_ws_rejects_non_admin_with_unresolvable_org(monkeypatch: pytest.MonkeyPatch) -> None:
    """External review 2026-08-01 finding 5: a non-Administrator whose
    platform user row can't be resolved is REJECTED — the WS must not
    manufacture 'default' tenant membership (matching HTTP's fail-closed)."""
    client, _, _ = _two_org_app(monkeypatch)
    # 'ghost' is an org-user that was never provisioned into any org.
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws/events?user=ghost&groups=org-users") as ws,
    ):
        ws.receive_json()
    # A seeded org user still connects fine (control).
    with client.websocket_connect("/ws/events?user=acme-u&groups=org-users") as ws:
        pass  # accepted


def test_ws_redacts_tool_secrets_for_non_admin_subscriber(monkeypatch: pytest.MonkeyPatch) -> None:
    """External review 2026-08-01 F3 round 3: WS events mirror audit incl.
    step_completed carrying tool_calls AND an echoed output_text. A
    below-admin subscriber must receive neither; an Administrator gets raw."""
    client, _, events = _two_org_app(monkeypatch)
    secret_in = "WS-SENTINEL-IN"
    secret_out = "WS-SENTINEL-OUT"
    step_event = {
        "action": "step_completed",
        "org_id": "acme",
        "detail": {
            "output": {
                "tool_calls": [
                    {"name": "t", "input": {"body": secret_in}, "result": {"content": secret_out}}
                ],
                "output_text": f"Saw {secret_in} and {secret_out}",
            }
        },
    }

    # Below-admin (org user): redacted.
    with client.websocket_connect("/ws/events?user=acme-u&groups=org-users") as ws:
        asyncio.run(events.publish(dict(step_event)))
        got = ws.receive_json()
        blob = str(got)
        assert secret_in not in blob and secret_out not in blob
        assert got["detail"]["output"]["output_text"].startswith("[redacted")

    # Administrator: raw.
    with client.websocket_connect("/ws/events?user=root&groups=admins") as ws:
        asyncio.run(events.publish(dict(step_event)))
        got = ws.receive_json()
        assert secret_out in str(got)
