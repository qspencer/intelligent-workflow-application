"""G29 per-workflow released vocabularies are WITHDRAWN AT THE CODE BOUNDARY.

The external review of 2026-10-29 (`docs/TRACE_G29_REVIEW_RESULTS.md`) found
that removing the one bundled declaration did not withdraw anything: any new
definition re-activated the path (P0), and the scaffold endpoint persisted a
MODEL-AUTHORED declaration in one request (P0), which falsified the premise
that a declaration is operator-provenanced config.

So the guarantee these tests pin is not "the bundled workflow is clean". It is
that NO path can persist, load or execute a non-null `released_fields` — and
that a new path cannot be added without being enumerated here.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.tools import FileReadTool, FileWriteTool
from workflow_platform.workflow import (
    WorkflowDefinition,
    WorkflowDefinitionError,
    load_definition_from_yaml,
)
from workflow_platform.world import mock_world

SRC = Path(__file__).resolve().parents[1] / "src" / "workflow_platform"
_H = {"X-Dev-User": "a", "X-Dev-Groups": "admins"}

#: The reviewer's own probe value: a token chosen to LOOK operator-authored.
_DECLARATION = {"category": ["operator-looking-token"]}

_DEFINITION: dict[str, Any] = {
    "id": "g29-probe",
    "name": "G29 probe",
    "trigger": {"type": "manual", "config": {}},
    "steps": [
        {
            "id": "s",
            "type": "deterministic",
            "function": "append_file",
            "config": {"content_from": "x", "path": "/tmp/x"},
        }
    ],
    "edges": [],
    "released_fields": _DECLARATION,
}

_YAML = """
id: g29-probe
name: G29 probe
trigger: {type: manual, config: {}}
released_fields: {category: [operator-looking-token]}
steps:
  - {id: s, type: deterministic, function: append_file, config: {content_from: x, path: /tmp/x}}
"""


def _client(response_text: str = "{}") -> tuple[TestClient, Any]:
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog([FileReadTool(), FileWriteTool()]),
        bedrock=FakeBedrock([text_response(response_text)]),
        world=mock_world(),
    )
    return TestClient(create_app(repositories=repos, engine=engine)), repos


async def _nothing_persisted_with_a_declaration(repos: Any) -> bool:
    return all(d.released_fields is None for d in await repos.definitions.list_all())


# --- the model itself: the single validation chokepoint ---


def test_model_validate_refuses_a_declaration() -> None:
    with pytest.raises(ValueError, match="released_fields is withdrawn"):
        WorkflowDefinition.model_validate(_DEFINITION)


def test_yaml_load_refuses_a_declaration() -> None:
    """The bundled-example and orchestrator seeding path."""
    with pytest.raises(WorkflowDefinitionError, match="released_fields is withdrawn"):
        load_definition_from_yaml(_YAML)


def test_absent_and_explicit_null_are_both_still_accepted() -> None:
    """The refusal is of a VALUE, not of the key — a stored body serializes
    `released_fields: null`, and all 27 live definitions carry exactly that."""
    body = {k: v for k, v in _DEFINITION.items() if k != "released_fields"}
    assert WorkflowDefinition.model_validate(body).released_fields is None
    assert (
        WorkflowDefinition.model_validate({**body, "released_fields": None}).released_fields is None
    )


# --- every HTTP path that persists a definition ---


@pytest.mark.parametrize(
    ("content_type", "payload"),
    [("application/yaml", _YAML), ("application/json", json.dumps(_DEFINITION))],
    ids=["yaml", "json"],
)
async def test_import_refuses_a_declaration(
    monkeypatch: pytest.MonkeyPatch, content_type: str, payload: str
) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client, repos = _client()
    r = client.post(
        "/api/workflows/import",
        content=payload,
        headers={**_H, "content-type": content_type},
    )
    assert r.status_code == 400, r.text
    assert await _nothing_persisted_with_a_declaration(repos)


async def test_scaffold_cannot_persist_a_model_authored_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE REVIEWER'S PROBE, reproduced: the model's draft carries a
    declaration. Before this fix the endpoint returned 200 and a GET returned
    `{'category': ['operator-looking-token']}` — a disclosure policy authored
    by model output and persisted with no field-level approval."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    draft = {k: v for k, v in _DEFINITION.items() if k != "id"}
    client, repos = _client(json.dumps(draft))
    r = client.post("/api/workflows/scaffold", json={"description": "triage"}, headers=_H)
    assert r.status_code == 422, r.text
    assert await _nothing_persisted_with_a_declaration(repos)


async def test_clone_cannot_propagate_a_declaration_from_an_unvalidated_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clone path used `model_copy`, which skips validators. It was closed
    only TRANSITIVELY (a template is validated when loaded). This injects a
    source that bypassed validation, so the test fails if the clone ever goes
    back to copying instead of validating."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    unvalidated = WorkflowDefinition.model_construct(
        **{
            **WorkflowDefinition.model_validate(
                {k: v for k, v in _DEFINITION.items() if k != "released_fields"}
            ).__dict__,
            "released_fields": _DECLARATION,
        }
    )
    monkeypatch.setattr("workflow_platform.api.workflows.load_templates", lambda _d: [unvalidated])
    client, repos = _client()
    r = client.post("/api/workflows", json={"template_id": "g29-probe"}, headers=_H)
    assert r.status_code >= 400, f"clone persisted a declaration: {r.status_code} {r.text}"
    assert await _nothing_persisted_with_a_declaration(repos)


def test_validate_endpoint_reports_the_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-persisting, but the canvas uses it to tell an author WHY."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client, _ = _client()
    r = client.post("/api/workflows/validate", json=_DEFINITION, headers=_H)
    assert "released_fields is withdrawn" in r.text, r.text


# --- the population: a new persisting path cannot skip the above ---

#: Every function that persists a WorkflowDefinition, and the test above that
#: proves it refuses a declaration. DERIVED from the source below and compared
#: both ways, so adding a fifth persisting path fails until it is enumerated
#: here with a test — a hand list of a derivable set is how a path goes missing.
_DECLARED_PERSISTING_SITES: dict[tuple[str, str], str] = {
    ("orchestrator.py", "_register_one"): "test_yaml_load_refuses_a_declaration",
    ("api/workflows.py", "create_workflow"): (
        "test_clone_cannot_propagate_a_declaration_from_an_unvalidated_source"
    ),
    ("api/workflows.py", "scaffold_workflow_endpoint"): (
        "test_scaffold_cannot_persist_a_model_authored_declaration"
    ),
    ("api/workflows.py", "import_workflow"): "test_import_refuses_a_declaration",
}


def _derived_persisting_sites() -> set[tuple[str, str]]:
    """Every `<...>.definitions.save(...)` call in the source, keyed by the
    function that makes it."""
    sites: set[tuple[str, str]] = set()

    def visit(node: ast.AST, rel: str, enclosing: str | None) -> None:
        # Attribute each call to its INNERMOST function: route handlers are
        # nested inside `build_router`, and crediting every enclosing scope
        # would declare the factory instead of the endpoint.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = node.name
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "save"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "definitions"
            and enclosing is not None
        ):
            sites.add((rel, enclosing))
        for child in ast.iter_child_nodes(node):
            visit(child, rel, enclosing)

    for path in sorted(SRC.rglob("*.py")):
        visit(ast.parse(path.read_text()), str(path.relative_to(SRC)), None)
    return sites


def test_every_definition_persisting_site_is_declared() -> None:
    derived = _derived_persisting_sites()
    declared = set(_DECLARED_PERSISTING_SITES)
    assert derived - declared == set(), (
        f"new path(s) persist a WorkflowDefinition: {sorted(derived - declared)}. "
        f"Add each to _DECLARED_PERSISTING_SITES with a test proving it refuses "
        f"a non-null released_fields."
    )
    assert declared - derived == set(), (
        f"declared site(s) no longer exist: {sorted(declared - derived)} — remove them"
    )


def test_every_declared_site_names_a_test_that_exists() -> None:
    """A declaration pointing at a renamed or deleted test is prose."""
    here = {n.name for n in ast.parse(Path(__file__).read_text()).body if hasattr(n, "name")}
    missing = {t for t in _DECLARED_PERSISTING_SITES.values() if t not in here}
    assert not missing, f"declared sites cite tests that do not exist: {sorted(missing)}"
