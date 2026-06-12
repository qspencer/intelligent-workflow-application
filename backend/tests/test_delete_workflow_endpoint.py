"""Tests for DELETE /api/workflows/{id} — cascade delete of a definition."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from workflow_platform.main import create_app
from workflow_platform.persistence import (
    Repositories,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.persistence.models import StepExecution, WorkflowInstance
from workflow_platform.workflow import load_definition

_ADMIN = {"X-Dev-User": "a", "X-Dev-Groups": "admins"}
_VIEWER = {"X-Dev-User": "v", "X-Dev-Groups": "viewers"}

_WF = {
    "id": "to-delete",
    "name": "To Delete",
    "trigger": {"type": "manual"},
    "steps": [{"id": "noop", "type": "deterministic", "function": "noop"}],
    "edges": [],
}


def _client(repos: Repositories) -> TestClient:
    return TestClient(create_app(repositories=repos))


def test_delete_cascades_instances_and_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()

    async def seed() -> None:
        await repos.definitions.save(load_definition(_WF))
        inst = await repos.instances.create(
            WorkflowInstance(workflow_id="to-delete", state=WorkflowInstanceState.COMPLETED)
        )
        await repos.steps.create(StepExecution(instance_id=inst.id, step_id="noop"))

    asyncio.run(seed())

    r = _client(repos).delete("/api/workflows/to-delete", headers=_ADMIN)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "deleted_workflow": "to-delete",
        "deleted_instances": 1,
        "deleted_steps": 1,
    }

    # Definition + its run history are gone.
    assert asyncio.run(repos.definitions.get("to-delete")) is None
    assert asyncio.run(repos.instances.list_by_workflow("to-delete")) == []


def test_delete_unknown_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    r = _client(repos).delete("/api/workflows/ghost", headers=_ADMIN)
    assert r.status_code == 404


def test_delete_role_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    asyncio.run(repos.definitions.save(load_definition(_WF)))

    r = _client(repos).delete("/api/workflows/to-delete", headers=_VIEWER)
    assert r.status_code == 403
    # A forbidden attempt leaves the definition intact.
    assert asyncio.run(repos.definitions.get("to-delete")) is not None


@pytest.mark.parametrize(
    "live_state",
    [
        WorkflowInstanceState.PENDING,
        WorkflowInstanceState.RUNNING,
        WorkflowInstanceState.PAUSED,
    ],
)
def test_delete_refused_while_instances_live(
    monkeypatch: pytest.MonkeyPatch, live_state: WorkflowInstanceState
) -> None:
    """409 while any run is non-terminal — never delete rows out from under
    the engine, and never strand a PAUSED run."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()

    async def seed() -> None:
        await repos.definitions.save(load_definition(_WF))
        await repos.instances.create(WorkflowInstance(workflow_id="to-delete", state=live_state))

    asyncio.run(seed())

    r = _client(repos).delete("/api/workflows/to-delete", headers=_ADMIN)
    assert r.status_code == 409
    assert live_state.value in r.json()["detail"]
    # Nothing was deleted.
    assert asyncio.run(repos.definitions.get("to-delete")) is not None
    assert len(asyncio.run(repos.instances.list_by_workflow("to-delete"))) == 1


def test_delete_allowed_once_instances_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()

    async def seed() -> None:
        await repos.definitions.save(load_definition(_WF))
        for state in (
            WorkflowInstanceState.COMPLETED,
            WorkflowInstanceState.FAILED,
            WorkflowInstanceState.KILLED,
        ):
            await repos.instances.create(WorkflowInstance(workflow_id="to-delete", state=state))

    asyncio.run(seed())

    r = _client(repos).delete("/api/workflows/to-delete", headers=_ADMIN)
    assert r.status_code == 200
    assert r.json()["deleted_instances"] == 3
