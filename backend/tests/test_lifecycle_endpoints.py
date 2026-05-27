"""Tests for the kill / retry / list-instances API endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.main import create_app
from workflow_platform.persistence import (
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
    return {"X-Dev-User": "bob", "X-Dev-Groups": "viewers"}


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
    # Audit log entries for the deleted instance are intentionally preserved.
    post_audit = asyncio.run(repos.audit.list_by_instance(completed_id))
    assert len(post_audit) == len(pre_audit)


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
