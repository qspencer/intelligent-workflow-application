"""Tests for GET /api/workflows/{id}/capabilities (C6.3)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.security import CapabilityPolicy
from workflow_platform.tools import FileReadTool, FileWriteTool
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

MODEL = "anthropic.claude-3-haiku-20240307-v1:0"

_WF = {
    "id": "cap-wf",
    "name": "Cap WF",
    "trigger": {"type": "manual"},
    "steps": [
        # offers both tools, but the step capability layer denies file_write
        {
            "id": "reader",
            "type": "agentic",
            "goal": "read",
            "model": MODEL,
            "tools": ["file_read", "file_write"],
            "capabilities": {"tools": ["file_read"]},
        },
        # offers only file_read -> file_write is "not enabled"
        {
            "id": "narrow",
            "type": "agentic",
            "goal": "narrow",
            "model": MODEL,
            "tools": ["file_read"],
        },
        # deterministic steps are excluded from the report entirely
        {"id": "done", "type": "deterministic", "function": "noop"},
    ],
    "edges": [{"from": "reader", "to": "narrow"}, {"from": "narrow", "to": "done"}],
}


def _engine(repos: object, *, system_caps: CapabilityPolicy | None = None) -> WorkflowEngine:
    return WorkflowEngine(
        repositories=repos,  # type: ignore[arg-type]
        functions=FunctionRegistry(),
        tools=ToolCatalog([FileReadTool(), FileWriteTool()]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        system_capabilities=system_caps,
    )


def _client(repos: object, engine: WorkflowEngine) -> TestClient:
    return TestClient(create_app(repositories=repos, engine=engine))  # type: ignore[arg-type]


_H = {"X-Dev-User": "a", "X-Dev-Groups": "viewers"}


def test_capabilities_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    asyncio.run(repos.definitions.save(load_definition(_WF)))

    body = (
        _client(repos, _engine(repos)).get("/api/workflows/cap-wf/capabilities", headers=_H).json()
    )

    assert body["tool_catalog"] == ["file_read", "file_write"]
    steps = {s["step_id"]: s for s in body["steps"]}
    assert set(steps) == {"reader", "narrow"}  # the deterministic step is excluded

    reader = steps["reader"]
    assert reader["allowed"] == ["file_read"]
    rden = {d["tool"]: d for d in reader["denied"]}
    assert rden["file_write"]["reason_code"] == "capability_blocked"
    assert "step capability allowlist" in rden["file_write"]["reason"]

    narrow = steps["narrow"]
    assert narrow["allowed"] == ["file_read"]
    nden = {d["tool"]: d for d in narrow["denied"]}
    assert nden["file_write"]["reason_code"] == "not_enabled"


def test_capabilities_system_layer_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """A system-level allowlist that excludes a tool is attributed to 'system'."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    asyncio.run(repos.definitions.save(load_definition(_WF)))
    engine = _engine(repos, system_caps=CapabilityPolicy(tools=["file_read"]))

    body = _client(repos, engine).get("/api/workflows/cap-wf/capabilities", headers=_H).json()
    reader = next(s for s in body["steps"] if s["step_id"] == "reader")
    fw = next(d for d in reader["denied"] if d["tool"] == "file_write")
    assert fw["reason_code"] == "capability_blocked"
    assert "system capability allowlist" in fw["reason"]


def test_capabilities_unknown_workflow_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    r = _client(repos, _engine(repos)).get("/api/workflows/ghost/capabilities", headers=_H)
    assert r.status_code == 404
