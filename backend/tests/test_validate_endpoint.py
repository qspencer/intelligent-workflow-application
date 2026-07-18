"""Tests for validate_definition + POST /api/workflows/validate (C7.3)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.workflow import WorkflowDefinition, validate_definition

MODEL = "anthropic.claude-3-haiku-20240307-v1:0"


def _def(steps: list[dict[str, Any]], edges: list[dict[str, str]]) -> WorkflowDefinition:
    # model_validate (not load_definition) so we can build structurally-invalid
    # graphs — Pydantic checks types, not the DAG.
    return WorkflowDefinition.model_validate(
        {"id": "wf", "name": "WF", "trigger": {"type": "manual"}, "steps": steps, "edges": edges}
    )


def _agentic(sid: str, goal: str = "do it", tools: list[str] | None = None) -> dict[str, Any]:
    return {"id": sid, "type": "agentic", "goal": goal, "model": MODEL, "tools": tools or []}


# --- validate_definition (unit) ---


def test_valid_workflow_has_no_errors() -> None:
    d = _def([_agentic("a"), _agentic("b")], [{"from": "a", "to": "b"}])
    assert validate_definition(d) == []


def test_duplicate_step_ids() -> None:
    d = _def([_agentic("a"), _agentic("a")], [])
    codes = {f.code for f in validate_definition(d)}
    assert "duplicate_step_id" in codes


def test_edge_to_unknown_step() -> None:
    d = _def([_agentic("a")], [{"from": "a", "to": "ghost"}])
    f = next(f for f in validate_definition(d) if f.code == "edge_unknown_target")
    assert f.level == "error"
    assert f.edge == {"from": "a", "to": "ghost"}


def test_empty_goal_flagged() -> None:
    d = _def([_agentic("a", goal="   ")], [])
    f = next(f for f in validate_definition(d) if f.code == "empty_goal")
    assert f.node_id == "a"


def test_cycle_flagged() -> None:
    d = _def([_agentic("a"), _agentic("b")], [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}])
    assert any(f.code == "cycle" for f in validate_definition(d))


def test_disconnected_step_is_a_warning() -> None:
    d = _def([_agentic("a"), _agentic("b"), _agentic("c")], [{"from": "a", "to": "b"}])
    f = next(f for f in validate_definition(d) if f.code == "disconnected_step")
    assert f.level == "warning"
    assert f.node_id == "c"


# --- endpoint ---


def _client() -> TestClient:
    return TestClient(create_app(repositories=in_memory_repositories()))


_H = {"X-Dev-User": "a", "X-Dev-Groups": "org-viewers"}


def test_validate_endpoint_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    spec = {
        "id": "wf",
        "name": "WF",
        "trigger": {"type": "manual"},
        "steps": [_agentic("a")],
        "edges": [],
    }
    body = _client().post("/api/workflows/validate", json=spec, headers=_H).json()
    assert body["valid"] is True
    assert body["findings"] == []


def test_validate_endpoint_reports_bad_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    spec = {
        "id": "wf",
        "name": "WF",
        "trigger": {"type": "manual"},
        "steps": [_agentic("a")],
        "edges": [{"from": "a", "to": "ghost"}],
    }
    body = _client().post("/api/workflows/validate", json=spec, headers=_H).json()
    assert body["valid"] is False
    assert any(f["code"] == "edge_unknown_target" for f in body["findings"])


def test_validate_endpoint_parse_error_maps_to_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Agentic step missing the required `model` field -> Pydantic ValidationError.
    spec = {
        "id": "wf",
        "name": "WF",
        "trigger": {"type": "manual"},
        "steps": [{"id": "a", "type": "agentic", "goal": "x"}],
        "edges": [],
    }
    body = _client().post("/api/workflows/validate", json=spec, headers=_H).json()
    assert body["valid"] is False
    assert any(f["code"] == "parse_error" and f["node_id"] == "a" for f in body["findings"])
