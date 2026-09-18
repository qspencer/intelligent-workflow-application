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
    # R5 F4: step ids are PLATFORM-MINTED — the model names its own steps, and
    # step ids are published as dict KEYS in the projected `context.steps`, so a
    # model-chosen id would be model-derived content on a platform-keyed path.
    #
    # R6 F1: assert the ID SET, not the absence of a substring. The previous
    # version required "extract" to vanish from the whole serialized definition,
    # which the reviewer noted REWARDED the substring-rewrite defect — it also
    # required the function `pdf_extract` to be mangled. The property is about
    # identifiers, so it is asserted on identifiers.
    assert {s["id"] for s in fetched["steps"]} == {"step_1", "step_2"}
    assert not ({"extract", "summarize"} & {s["id"] for s in fetched["steps"]}), (
        "a model-chosen step id survived as an identifier"
    )


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
        headers={"X-Dev-User": "v", "X-Dev-Groups": "org-viewers"},
    )
    assert r.status_code == 403


def test_extract_json_tolerates_trailing_prose() -> None:
    """The Haiku eval run's dominant L1 failure: valid JSON followed by
    explanation text ('Extra data'). raw_decode scanning takes the first
    complete object."""
    out = extract_json('{"name": "wf", "steps": []}\n\nThis workflow watches a folder and...')
    assert out == {"name": "wf", "steps": []}


def test_extract_json_tolerates_braces_in_leading_prose() -> None:
    out = extract_json('Here is {my} answer: {"name": "wf"} hope that helps')
    assert out == {"name": "wf"}


# --- R6 F1: minting rewrites REFERENCES, and nothing else --------------------


def test_minting_rewrites_references_and_preserves_everything_else() -> None:
    """R6 F1. The first version substring-replaced every string in the draft:
    the function `pdf_extract` became `pdf_step_1` (structurally valid, so it
    persisted and failed at run time), a path `/inbox/extract/` was rewritten,
    and a condition's comparison LITERAL changed with its step reference.
    Whole-word matching would still have broken the literal."""
    from workflow_platform.scaffold import mint_platform_step_ids

    draft = {
        "steps": [
            {
                "id": "extract",
                "type": "deterministic",
                "function": "pdf_extract",
                "config": {"path": "/inbox/extract/file.pdf"},
            },
            {
                "id": "route",
                "type": "agentic",
                "inputs": ["extract"],
                "goal": "read {steps.extract.output_text}",
            },
        ],
        "edges": [
            {
                "from": "extract",
                "to": "route",
                "condition": "steps['extract']['document_type'] == 'extract'",
            }
        ],
    }
    out = mint_platform_step_ids(json.loads(json.dumps(draft)))

    # references move …
    assert [s["id"] for s in out["steps"]] == ["step_1", "step_2"]
    assert out["steps"][1]["inputs"] == ["step_1"]
    assert out["edges"][0]["from"] == "step_1" and out["edges"][0]["to"] == "step_2"
    assert "steps['step_1']" in out["edges"][0]["condition"]
    assert out["steps"][1]["goal"] == "read {steps.step_1.output_text}"

    # … and nothing else does
    assert out["steps"][0]["function"] == "pdf_extract", "a FUNCTION NAME was rewritten"
    assert out["steps"][0]["config"]["path"] == "/inbox/extract/file.pdf", "a PATH was rewritten"
    assert out["edges"][0]["condition"].endswith("== 'extract'"), (
        "a comparison LITERAL was rewritten"
    )


def test_minting_rejects_duplicate_ids_before_they_are_minted_apart() -> None:
    """R6 F1: duplicates were silently minted into distinct ids, destroying the
    very collision definition validation exists to reject."""
    import pytest as _pytest

    from workflow_platform.scaffold import ScaffoldIdError, mint_platform_step_ids

    with _pytest.raises(ScaffoldIdError):
        mint_platform_step_ids({"steps": [{"id": "a"}, {"id": "a"}]})
