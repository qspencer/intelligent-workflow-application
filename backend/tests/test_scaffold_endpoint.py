"""Tests for the NL scaffold + POST /api/workflows/scaffold (C7.1)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.catalog import build_catalog
from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.scaffold import ScaffoldError, build_system_prompt, extract_json
from workflow_platform.tools import FileReadTool, FileWriteTool
from workflow_platform.world import mock_world

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_H = {"X-Dev-User": "a", "X-Dev-Groups": "admins"}

_GOOD_WORKFLOW = {
    "name": "Triage dropped PDFs",
    "description": "Extract and summarize PDFs dropped in a folder.",
    "trigger": {"type": "filesystem", "config": {"path": "/tmp/in"}},
    "steps": [
        {"id": "extract", "type": "deterministic", "function": "pdf_extract", "config": {}},
        {
            "id": "summarize",
            "type": "agentic",
            "goal": "Summarize",
            "model": MODEL,
            "tools": ["file_read"],
        },
    ],
    "edges": [{"from": "extract", "to": "summarize"}],
}


# --- pure helpers ---


def test_build_system_prompt_inlines_catalog() -> None:
    catalog = build_catalog(default_function_registry(), ToolCatalog([FileReadTool()]))
    prompt = build_system_prompt(catalog)
    assert "pdf_extract" in prompt
    assert "file_read" in prompt
    assert "filesystem" in prompt  # a trigger type


def test_extract_json_strips_code_fences() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_handles_surrounding_prose() -> None:
    assert extract_json('Sure! Here:\n{"a": 1}\nHope that helps.') == {"a": 1}


def test_extract_json_rejects_non_json() -> None:
    with pytest.raises(ScaffoldError):
        extract_json("no json here")


# --- endpoint ---


def _engine(repos: object, response_text: str) -> WorkflowEngine:
    return WorkflowEngine(
        repositories=repos,  # type: ignore[arg-type]
        functions=default_function_registry(),
        tools=ToolCatalog([FileReadTool(), FileWriteTool()]),
        bedrock=FakeBedrock([text_response(response_text)]),
        world=mock_world(),
    )


def _client(repos: object, engine: WorkflowEngine) -> TestClient:
    return TestClient(create_app(repositories=repos, engine=engine))  # type: ignore[arg-type]


def test_scaffold_creates_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    client = _client(repos, _engine(repos, json.dumps(_GOOD_WORKFLOW)))

    r = client.post("/api/workflows/scaffold", json={"description": "triage pdfs"}, headers=_H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "created"
    wf_id = body["workflow_id"]

    # Persisted + fetchable, with the scaffolded steps.
    fetched = client.get(f"/api/workflows/{wf_id}", headers=_H).json()
    assert {s["id"] for s in fetched["steps"]} == {"extract", "summarize"}


def test_scaffold_requires_description(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    r = _client(repos, _engine(repos, "{}")).post("/api/workflows/scaffold", json={}, headers=_H)
    assert r.status_code == 400


def test_scaffold_non_json_output_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    r = _client(repos, _engine(repos, "I cannot help with that.")).post(
        "/api/workflows/scaffold", json={"description": "x"}, headers=_H
    )
    assert r.status_code == 422


def test_scaffold_malformed_definition_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    # Agentic step missing the required `model` field.
    bad = {"name": "x", "steps": [{"id": "a", "type": "agentic", "goal": "g"}], "edges": []}
    r = _client(repos, _engine(repos, json.dumps(bad))).post(
        "/api/workflows/scaffold", json={"description": "x"}, headers=_H
    )
    assert r.status_code == 422


def test_scaffold_role_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    r = _client(repos, _engine(repos, json.dumps(_GOOD_WORKFLOW))).post(
        "/api/workflows/scaffold",
        json={"description": "x"},
        headers={"X-Dev-User": "v", "X-Dev-Groups": "viewers"},
    )
    assert r.status_code == 403
