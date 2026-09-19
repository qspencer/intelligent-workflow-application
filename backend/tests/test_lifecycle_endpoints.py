"""Tests for the kill / retry / list-instances API endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.main import create_app
from workflow_platform.persistence import (
    AuditEntry,
    StepExecution,
    StepExecutionState,
    WorkflowInstance,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition


def _seed(repos: Any) -> None:
    """Synchronous helper that runs async setup."""

    async def _do() -> None:
        await repos.definitions.save(
            load_definition(
                {
                    "id": "wf-1",
                    "name": "wf-1",
                    "trigger": {"type": "manual"},
                    "steps": [{"id": "a", "type": "deterministic", "function": "noop"}],
                    "edges": [],
                }
            )
        )
        running = WorkflowInstance(workflow_id="wf-1", state=WorkflowInstanceState.RUNNING)
        await repos.instances.create(running)
        failed = WorkflowInstance(workflow_id="wf-1", state=WorkflowInstanceState.FAILED)
        await repos.instances.create(failed)
        completed = WorkflowInstance(workflow_id="wf-1", state=WorkflowInstanceState.COMPLETED)
        await repos.instances.create(completed)
        # Mark the failed instance's step as actually failed so retry has work to do.
        await repos.steps.create(
            StepExecution(instance_id=failed.id, step_id="a", state=StepExecutionState.FAILED)
        )

    asyncio.run(_do())


@pytest.fixture
def dev_app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any, WorkflowEngine]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    _seed(repos)
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=__import__("workflow_platform.world", fromlist=["mock_world"]).mock_world(),
    )

    # noop function so retry's resume can run
    async def noop(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {}

    engine.functions.register("noop", noop)
    app = create_app(repositories=repos, engine=engine)
    return TestClient(app), repos, engine


def _admin() -> dict[str, str]:
    return {"X-Dev-User": "alice", "X-Dev-Groups": "admins"}


def _viewer() -> dict[str, str]:
    return {"X-Dev-User": "bob", "X-Dev-Groups": "org-viewers"}


def test_list_instances_returns_seeded(dev_app: tuple[TestClient, Any, WorkflowEngine]) -> None:
    client, _repos, _engine = dev_app
    r = client.get("/api/workflow-instances", headers=_admin())
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    states = {i["state"] for i in data}
    assert states == {"running", "failed", "completed"}


def test_list_instances_filters_by_state(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, *_ = dev_app
    r = client.get("/api/workflow-instances?state=failed", headers=_admin())
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["state"] == "failed"


def test_kill_running_instance(dev_app: tuple[TestClient, Any, WorkflowEngine]) -> None:
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    running_id = next(i.id for i in instances if i.state == WorkflowInstanceState.RUNNING)
    r = client.post(f"/api/workflow-instances/{running_id}/kill", headers=_admin())
    assert r.status_code == 200
    fresh = asyncio.run(repos.instances.get(running_id))
    assert fresh is not None
    assert fresh.state == WorkflowInstanceState.KILLED


def test_kill_terminal_instance_rejected(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    completed_id = next(i.id for i in instances if i.state == WorkflowInstanceState.COMPLETED)
    r = client.post(f"/api/workflow-instances/{completed_id}/kill", headers=_admin())
    assert r.status_code == 400


def test_kill_requires_admin_or_operator(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    running_id = next(i.id for i in instances if i.state == WorkflowInstanceState.RUNNING)
    r = client.post(f"/api/workflow-instances/{running_id}/kill", headers=_viewer())
    assert r.status_code == 403


def test_retry_failed_instance_returns_resume_started(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    failed_id = next(i.id for i in instances if i.state == WorkflowInstanceState.FAILED)
    r = client.post(f"/api/workflow-instances/{failed_id}/retry", headers=_admin())
    assert r.status_code == 200
    assert r.json()["status"] == "retry_started"


def test_retry_running_instance_rejected(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    running_id = next(i.id for i in instances if i.state == WorkflowInstanceState.RUNNING)
    r = client.post(f"/api/workflow-instances/{running_id}/retry", headers=_admin())
    assert r.status_code == 400


# --- POST /api/workflows/{id}/run ---


def test_run_workflow_creates_instance(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, _, _ = dev_app
    r = client.post(
        "/api/workflows/wf-1/run",
        json={"key": "value"},
        headers=_admin(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "started"
    assert body["state"] == "completed"
    assert body["instance_id"]


def test_run_unknown_workflow_404(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, *_ = dev_app
    r = client.post("/api/workflows/does-not-exist/run", json={}, headers=_admin())
    assert r.status_code == 404


def test_run_rejects_non_object_payload(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, *_ = dev_app
    r = client.post(
        "/api/workflows/wf-1/run",
        content='["not", "an", "object"]',
        headers={**_admin(), "Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert "JSON object" in r.json()["detail"]


def test_run_requires_operator_role(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, *_ = dev_app
    r = client.post("/api/workflows/wf-1/run", json={}, headers=_viewer())
    assert r.status_code == 403


def test_run_empty_body_treated_as_empty_dict(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, *_ = dev_app
    r = client.post("/api/workflows/wf-1/run", headers=_admin())
    assert r.status_code == 200
    assert r.json()["status"] == "started"


# --- POST /api/workflow-instances/{id}/fork ---


def test_fork_requires_operator_role(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    inst_id = next(i.id for i in instances if i.state == WorkflowInstanceState.COMPLETED)
    r = client.post(
        f"/api/workflow-instances/{inst_id}/fork",
        json={"from_step_id": "a"},
        headers=_viewer(),
    )
    assert r.status_code == 403


def test_fork_unknown_instance_404(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, *_ = dev_app
    r = client.post(
        "/api/workflow-instances/does-not-exist/fork",
        json={"from_step_id": "a"},
        headers=_admin(),
    )
    assert r.status_code == 404


def test_fork_missing_step_id_400(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    inst_id = next(i.id for i in instances if i.state == WorkflowInstanceState.COMPLETED)
    r = client.post(
        f"/api/workflow-instances/{inst_id}/fork",
        json={},
        headers=_admin(),
    )
    assert r.status_code == 400
    assert "from_step_id" in r.json()["detail"]


def test_fork_creates_new_instance(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    """Forking from the only step in this workflow re-runs that step fresh,
    leaving the source instance untouched."""
    client, repos, _ = dev_app
    # The seeded `wf-1` instances have a single deterministic step `a`.
    # Forking at `a` is "re-run the whole workflow with the same trigger."
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    source_id = next(i.id for i in instances if i.state == WorkflowInstanceState.COMPLETED)

    r = client.post(
        f"/api/workflow-instances/{source_id}/fork",
        json={"from_step_id": "a"},
        headers=_admin(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "forked"
    assert body["source_instance_id"] == source_id
    assert body["instance_id"] != source_id
    assert body["state"] == "completed"

    # The audit log of the new instance should record the fork event.
    audit = asyncio.run(repos.audit.list_by_instance(body["instance_id"]))
    actions = [e.action for e in audit]
    assert "workflow_forked" in actions


# ---------- DELETE endpoint ----------


def test_delete_terminal_instance_removes_it_and_steps(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    """A terminal (completed) instance + its step_executions are gone after
    DELETE. Audit entries stay — append-only log."""
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    completed_id = next(i.id for i in instances if i.state == WorkflowInstanceState.COMPLETED)
    # Seed at least one step_execution + audit entry so we can verify
    # the cascade. The dev_app fixture seeds instances but not
    # step_executions, so we add one directly here.
    from datetime import UTC, datetime

    from workflow_platform.persistence import StepExecution, StepExecutionState

    asyncio.run(
        repos.steps.create(
            StepExecution(
                instance_id=completed_id,
                step_id="seeded-step",
                state=StepExecutionState.COMPLETED,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
    )
    pre_audit = asyncio.run(repos.audit.list_by_instance(completed_id))

    r = client.delete(f"/api/workflow-instances/{completed_id}", headers=_admin())
    assert r.status_code == 204
    assert r.content == b""

    # Instance + steps gone.
    assert asyncio.run(repos.instances.get(completed_id)) is None
    assert asyncio.run(repos.steps.list_by_instance(completed_id)) == []
    # Audit log entries for the deleted instance are intentionally preserved,
    # plus exactly one new `instance_deleted` entry recording the deletion.
    post_audit = asyncio.run(repos.audit.list_by_instance(completed_id))
    assert len(post_audit) == len(pre_audit) + 1
    assert any(e.action == "instance_deleted" for e in post_audit)


def test_delete_failed_and_killed_instances_also_work(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    """All three terminal states are deletable."""
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    failed_id = next(i.id for i in instances if i.state == WorkflowInstanceState.FAILED)
    r = client.delete(f"/api/workflow-instances/{failed_id}", headers=_admin())
    assert r.status_code == 204
    # Also kill+delete a running one to cover KILLED.
    running_id = next(i.id for i in instances if i.state == WorkflowInstanceState.RUNNING)
    client.post(f"/api/workflow-instances/{running_id}/kill", headers=_admin())
    r = client.delete(f"/api/workflow-instances/{running_id}", headers=_admin())
    assert r.status_code == 204


def test_delete_nonterminal_instance_rejected(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    """RUNNING (not yet killed) can't be deleted — kill first."""
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    running_id = next(i.id for i in instances if i.state == WorkflowInstanceState.RUNNING)
    r = client.delete(f"/api/workflow-instances/{running_id}", headers=_admin())
    assert r.status_code == 400
    assert "running" in r.text.lower()


def test_delete_nonexistent_instance_returns_404(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, _, _ = dev_app
    r = client.delete("/api/workflow-instances/no-such-id", headers=_admin())
    assert r.status_code == 404


def test_delete_requires_admin_or_operator(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    completed_id = next(i.id for i in instances if i.state == WorkflowInstanceState.COMPLETED)
    r = client.delete(f"/api/workflow-instances/{completed_id}", headers=_viewer())
    assert r.status_code == 403


# ---------- Bulk DELETE endpoint ----------


def test_bulk_delete_removes_all_in_requested_terminal_states(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    """One request, three states, all matching instances + their steps gone."""
    client, repos, _ = dev_app
    pre = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    terminal_pre = [
        i
        for i in pre
        if i.state
        in (
            WorkflowInstanceState.COMPLETED,
            WorkflowInstanceState.FAILED,
        )
    ]
    assert len(terminal_pre) >= 2

    r = client.delete(
        "/api/workflow-instances?state=completed&state=failed&state=killed",
        headers=_admin(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted_instances"] == len(terminal_pre)
    # `deleted_steps` may be 0 if the fixture didn't seed step rows.
    assert body["deleted_steps"] >= 0

    # Post-state: none of the deleted instances remain.
    for inst in terminal_pre:
        assert asyncio.run(repos.instances.get(inst.id)) is None


def test_deletes_write_audit_entries(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    """Destructive admin ops must leave a trace in the audit log — a real
    platform-wide bulk wipe was once only reconstructable from HTTP access
    logs. Both the single and bulk delete record actor + scope + counts."""
    client, repos, _ = dev_app
    pre = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    completed = next(i for i in pre if i.state == WorkflowInstanceState.COMPLETED)

    r = client.delete(f"/api/workflow-instances/{completed.id}", headers=_admin())
    assert r.status_code == 204
    r = client.delete(
        "/api/workflow-instances?state=completed&state=failed&state=killed",
        headers=_admin(),
    )
    assert r.status_code == 200

    entries = asyncio.run(repos.audit.list_recent(limit=50))
    single = next(e for e in entries if e.action == "instance_deleted")
    assert single.workflow_instance_id == completed.id
    assert single.actor_type == "human" and single.actor_id
    bulk = next(e for e in entries if e.action == "instances_bulk_deleted")
    assert bulk.detail["states"] == ["completed", "failed", "killed"]
    assert bulk.detail["workflow_id"] is None  # null records "platform-wide"
    assert bulk.detail["deleted_instances"] >= 1


def test_bulk_delete_scoped_by_workflow_id(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    """Passing workflow_id limits deletes to that workflow only."""
    client, repos, _ = dev_app
    pre = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    completed_count = sum(1 for i in pre if i.state == WorkflowInstanceState.COMPLETED)

    r = client.delete(
        "/api/workflow-instances?state=completed&workflow_id=wf-1",
        headers=_admin(),
    )
    assert r.status_code == 200
    assert r.json()["deleted_instances"] == completed_count


def test_bulk_delete_rejects_non_terminal_state(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    """Including `running` in the states list is a 400 — bulk delete is
    for cleanup, not stopping live runs."""
    client, _, _ = dev_app
    r = client.delete(
        "/api/workflow-instances?state=completed&state=running",
        headers=_admin(),
    )
    assert r.status_code == 400
    assert "not terminal" in r.text.lower()


def test_bulk_delete_requires_at_least_one_state(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    """Calling with no `?state=` is 422 (FastAPI's required-param error)."""
    client, _, _ = dev_app
    r = client.delete("/api/workflow-instances", headers=_admin())
    assert r.status_code in (400, 422)


def test_bulk_delete_requires_admin_or_operator(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    client, _, _ = dev_app
    r = client.delete("/api/workflow-instances?state=completed", headers=_viewer())
    assert r.status_code == 403


def test_audit_endpoint_filters_by_instance_id(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    """`GET /api/audit?instance_id=` scopes to that instance (previously the
    param was silently ignored and the global list came back)."""
    client, repos, _ = dev_app

    async def _seed_audit() -> tuple[str, str]:
        instances = await repos.instances.list_by_workflow("wf-1")
        first, second = instances[0].id, instances[1].id
        for iid, action in ((first, "workflow_started"), (second, "workflow_killed")):
            await repos.audit.append(
                AuditEntry(
                    actor_type="engine",
                    actor_id="workflow_engine",
                    action=action,
                    workflow_instance_id=iid,
                )
            )
        return first, second

    first, _second = asyncio.run(_seed_audit())

    scoped = client.get(f"/api/audit?instance_id={first}", headers=_admin()).json()
    assert [e["workflow_instance_id"] for e in scoped] == [first]

    unscoped = client.get("/api/audit", headers=_admin()).json()
    assert len(unscoped) >= 2


# --- audit coverage (2026-09-19) -------------------------------------------
#
# `POST .../kill` wrote the terminal state and NO audit entry. The engine
# audits `workflow_killed` when IT observes a kill mid-run, and
# `_note_bypass`'s docstring reasoned from that — "lifecycle ops: the engine
# audits the effect". True of resume and fork, which delegate to the engine;
# false of kill, pause and retry, which write the state themselves and never
# reach it. Found while bulk-killing 171 recovered orphans: 171 rows to a
# terminal state with nothing recording who or why.


def _audit_actions(repos: Any, instance_id: str) -> list[str]:
    entries = asyncio.run(repos.audit.list_recent(limit=50))
    return [e.action for e in entries if e.workflow_instance_id == instance_id]


def test_kill_is_audited(dev_app: tuple[TestClient, Any, WorkflowEngine]) -> None:
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    running_id = next(i.id for i in instances if i.state == WorkflowInstanceState.RUNNING)
    assert (
        client.post(f"/api/workflow-instances/{running_id}/kill", headers=_admin()).status_code
        == 200
    )
    assert "workflow_killed" in _audit_actions(repos, running_id)


def test_pause_is_audited(dev_app: tuple[TestClient, Any, WorkflowEngine]) -> None:
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    running_id = next(i.id for i in instances if i.state == WorkflowInstanceState.RUNNING)
    assert (
        client.post(f"/api/workflow-instances/{running_id}/pause", headers=_admin()).status_code
        == 200
    )
    assert "workflow_paused" in _audit_actions(repos, running_id)


def test_retry_is_audited(dev_app: tuple[TestClient, Any, WorkflowEngine]) -> None:
    """`workflow_retried`, distinct from the engine's `workflow_resumed`:
    otherwise the trail reads "resumed" with no record that a human retried
    a failed run."""
    client, repos, _ = dev_app
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    failed_id = next(i.id for i in instances if i.state == WorkflowInstanceState.FAILED)
    assert (
        client.post(f"/api/workflow-instances/{failed_id}/retry", headers=_admin()).status_code
        == 200
    )
    assert "workflow_retried" in _audit_actions(repos, failed_id)


def test_the_audited_actor_is_the_CALLER_not_the_engine() -> None:
    """An entry saying only "this was killed" is half a record. The point of
    auditing the API path is WHO reached for the button."""
    import os

    os.environ["AUTH_MODE"] = "dev"
    repos = in_memory_repositories()
    _seed(repos)
    app = create_app(repositories=repos, start_triggers=False)
    client = TestClient(app)
    instances = asyncio.run(repos.instances.list_by_workflow("wf-1"))
    running_id = next(i.id for i in instances if i.state == WorkflowInstanceState.RUNNING)

    client.post(
        f"/api/workflow-instances/{running_id}/kill",
        headers={"X-Dev-User": "operator-7", "X-Dev-Groups": "admins"},
    )
    entry = next(
        e
        for e in asyncio.run(repos.audit.list_recent(limit=50))
        if e.action == "workflow_killed" and e.workflow_instance_id == running_id
    )
    assert entry.actor_type == "human"
    assert entry.actor_id == "operator-7"


def test_every_instance_lifecycle_ENDPOINT_is_classified_for_audit() -> None:
    """R-d: the fix is a RULE, not three lines. Enumerated from the route
    table so a new lifecycle endpoint fails the build until someone says
    where its audit entry comes from — which is exactly the question nobody
    asked when kill was written.
    """
    from workflow_platform.main import create_app as _create_app

    #: endpoint suffix -> where its audit entry comes from.
    CLASSIFIED = {
        "kill": "api",  # writes the state itself; engine never sees it
        "pause": "api",  # ditto (the engine audits only ITS OWN budget pause)
        "retry": "api",  # the FAILED -> PAUSED hop; engine then audits resume
        "resume": "engine",  # delegates to engine.resume -> workflow_resumed
        "fork": "engine",  # delegates to engine.fork -> workflow_forked
    }
    app = _create_app(repositories=in_memory_repositories(), start_triggers=False)
    # From the OpenAPI schema, not `app.routes`: included routers nest, so
    # walking the top level silently finds NOTHING and the enumeration
    # passes by being empty — the failure mode an enumeration is least able
    # to notice. The `found` assertion below is the guard for that.
    prefix = "/api/workflow-instances/{instance_id}/"
    found = {
        path[len(prefix) :]
        for path, ops in app.openapi()["paths"].items()
        if path.startswith(prefix) and "post" in ops
    }
    assert found, "the route enumeration found no lifecycle endpoints — it has stopped working"
    unclassified = found - set(CLASSIFIED)
    assert not unclassified, (
        f"new instance lifecycle endpoint(s) {sorted(unclassified)}: say where the audit "
        "entry comes from. An endpoint that mutates instance state and audits nothing is "
        "the defect this enumeration exists for."
    )
    stale = set(CLASSIFIED) - found
    assert not stale, f"classified endpoints that no longer exist: {sorted(stale)}"


# --- dry-run instances are not re-drivable (2026-09-19) --------------------


@pytest.mark.parametrize(
    ("verb", "state"),
    [
        ("resume", WorkflowInstanceState.PAUSED),
        ("retry", WorkflowInstanceState.FAILED),
        ("fork", WorkflowInstanceState.FAILED),
    ],
)
def test_the_api_refuses_to_re_drive_a_dry_run(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
    verb: str,
    state: WorkflowInstanceState,
) -> None:
    """400 with a reason, not a 500 from the engine's refusal. The engine
    guard is the real fence; this is the one an operator reads."""
    client, repos, _ = dev_app
    instance = asyncio.run(
        repos.instances.create(
            WorkflowInstance(workflow_id="wf-1", state=state, context={"dry_run": True})
        )
    )
    body = {"from_step_id": "a"} if verb == "fork" else None
    r = client.post(f"/api/workflow-instances/{instance.id}/{verb}", headers=_admin(), json=body)
    assert r.status_code == 400
    assert "dry run" in r.json()["detail"].lower()


def test_explain_reads_the_highest_ATTEMPT_not_the_last_started(
    dev_app: tuple[TestClient, Any, WorkflowEngine],
) -> None:
    """EXECUTION_SEMANTICS §3a: current state = the row with max `attempt`.
    `explain` used `execs[-1]` — last by `started_at`, which agrees only
    while attempt numbers rise with time. They did not while the resume
    path reused attempt 1."""
    client, repos, _ = dev_app
    instance = asyncio.run(
        repos.instances.create(
            WorkflowInstance(workflow_id="wf-1", state=WorkflowInstanceState.COMPLETED)
        )
    )
    now = datetime.now(UTC)
    # Attempt 2 STARTED FIRST — so insertion order and attempt order disagree.
    asyncio.run(
        repos.steps.create(
            StepExecution(
                instance_id=instance.id,
                step_id="a",
                attempt=2,
                state=StepExecutionState.COMPLETED,
                started_at=now - timedelta(minutes=5),
            )
        )
    )
    asyncio.run(
        repos.steps.create(
            StepExecution(
                instance_id=instance.id,
                step_id="a",
                attempt=1,
                state=StepExecutionState.FAILED,
                started_at=now,
            )
        )
    )
    r = client.get(f"/api/workflow-instances/{instance.id}/steps/a/explain", headers=_admin())
    assert r.status_code == 200
    assert r.json()["attempt"] == 2, "explain picked by start order, not by attempt"
