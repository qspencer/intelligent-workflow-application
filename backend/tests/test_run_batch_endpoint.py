"""Tests for POST /api/workflows/{id}/run-batch (C8.1)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
from workflow_platform.main import create_app
from workflow_platform.persistence import Repositories, in_memory_repositories
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

_ADMIN = {"X-Dev-User": "a", "X-Dev-Groups": "admins"}
_VIEWER = {"X-Dev-User": "v", "X-Dev-Groups": "viewers"}

# A deterministic noop workflow runs to completion with no Bedrock calls.
_WF = {
    "id": "batch-wf",
    "name": "Batch WF",
    "trigger": {"type": "manual"},
    "steps": [{"id": "noop", "type": "deterministic", "function": "noop"}],
    "edges": [],
}


def _engine(repos: Repositories) -> WorkflowEngine:
    return WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )


def _client(repos: Repositories) -> TestClient:
    return TestClient(create_app(repositories=repos, engine=_engine(repos)))


def _seed(repos: Repositories) -> None:
    asyncio.run(repos.definitions.save(load_definition(_WF)))


def test_batch_fires_one_instance_per_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    _seed(repos)
    client = _client(repos)

    rows = [{"n": 1}, {"n": 2}, {"n": 3}]
    body = client.post("/api/workflows/batch-wf/run-batch", json=rows, headers=_ADMIN).json()

    assert body["submitted"] == 3
    assert body["succeeded"] == 3
    assert body["failed"] == 0
    assert [r["index"] for r in body["results"]] == [0, 1, 2]
    assert all(r["ok"] and r["instance_id"] for r in body["results"])

    # Each row produced a persisted instance.
    instances = asyncio.run(repos.instances.list_by_workflow("batch-wf"))
    assert len(instances) == 3


def test_batch_empty_array_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    _seed(repos)
    r = _client(repos).post("/api/workflows/batch-wf/run-batch", json=[], headers=_ADMIN)
    assert r.status_code == 400


def test_batch_not_an_array_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    _seed(repos)
    r = _client(repos).post("/api/workflows/batch-wf/run-batch", json={"n": 1}, headers=_ADMIN)
    assert r.status_code == 400


def test_batch_non_object_rows_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    _seed(repos)
    r = _client(repos).post(
        "/api/workflows/batch-wf/run-batch", json=[{"ok": 1}, "nope"], headers=_ADMIN
    )
    assert r.status_code == 400


def test_batch_too_large_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    _seed(repos)
    rows = [{"n": i} for i in range(101)]
    r = _client(repos).post("/api/workflows/batch-wf/run-batch", json=rows, headers=_ADMIN)
    assert r.status_code == 400


def test_batch_unknown_workflow_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    r = _client(repos).post("/api/workflows/ghost/run-batch", json=[{"n": 1}], headers=_ADMIN)
    assert r.status_code == 404


def test_batch_role_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    _seed(repos)
    r = _client(repos).post("/api/workflows/batch-wf/run-batch", json=[{"n": 1}], headers=_VIEWER)
    assert r.status_code == 403
