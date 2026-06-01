"""Tests for the templates gallery + create-workflow endpoints (canvas C5).

Covers `workflow_platform.templates` (discovery + id slug helpers) and the
`GET /api/templates` / `POST /api/workflows` endpoints, including role gating
and template cloning.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.templates import (
    default_examples_dir,
    load_templates,
    slugify,
    unique_id,
)
from workflow_platform.world import mock_world

_WF_A = """
id: alpha-flow
name: Alpha Flow
description: First template.
trigger:
  type: manual
steps:
  - id: a
    type: deterministic
    function: noop
edges: []
"""

_WF_B = """
id: beta-flow
name: Beta Flow
description: Second template.
trigger:
  type: webhook
  config:
    trigger_id: beta
steps:
  - id: x
    type: deterministic
    function: noop
  - id: y
    type: deterministic
    function: noop
edges:
  - from: x
    to: y
"""

_JUNK = "this: is: not: a: workflow: ["


@pytest.fixture
def examples_dir(tmp_path: Path) -> Path:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "workflow.yaml").write_text(_WF_A)
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "workflow.yaml").write_text(_WF_B)
    # A malformed file must be skipped, not crash the gallery.
    (tmp_path / "beta" / "workflow-broken.yaml").write_text(_JUNK)
    # A non-workflow yaml (e.g. agent memory companions are .md, but guard
    # against stray yaml too) — wrong name, never matched by the glob.
    (tmp_path / "beta" / "notes.yaml").write_text("hello: world")
    return tmp_path


@pytest.fixture
def client(examples_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )
    app = create_app(
        repositories=repos,
        engine=engine,
        definitions_dir=examples_dir,
        start_triggers=False,
    )
    return TestClient(app)


def _designer() -> dict[str, str]:
    return {"X-Dev-User": "dana", "X-Dev-Groups": "designers"}


def _viewer() -> dict[str, str]:
    return {"X-Dev-User": "val", "X-Dev-Groups": "viewers"}


# --- unit: discovery + slug helpers ---


def test_load_templates_skips_junk_and_sorts(examples_dir: Path) -> None:
    templates = load_templates(examples_dir)
    assert [t.id for t in templates] == ["alpha-flow", "beta-flow"]  # sorted by name
    assert [len(t.steps) for t in templates] == [1, 2]


def test_load_templates_missing_dir(tmp_path: Path) -> None:
    assert load_templates(tmp_path / "nope") == []


def test_slugify() -> None:
    assert slugify("My Cool Flow!") == "my-cool-flow"
    assert slugify("  ") == "workflow"
    assert slugify("Already-Kebab") == "already-kebab"


def test_unique_id() -> None:
    assert unique_id("foo", set()) == "foo"
    assert unique_id("foo", {"foo"}) == "foo-2"
    assert unique_id("foo", {"foo", "foo-2"}) == "foo-3"


# --- GET /api/templates ---


def test_list_templates(client: TestClient) -> None:
    r = client.get("/api/templates", headers=_viewer())
    assert r.status_code == 200
    body = r.json()
    assert [t["id"] for t in body] == ["alpha-flow", "beta-flow"]
    beta = body[1]
    assert beta["step_count"] == 2
    assert beta["trigger_type"] == "webhook"
    assert beta["description"] == "Second template."


# --- POST /api/workflows (blank) ---


def test_create_blank_default_name(client: TestClient) -> None:
    r = client.post("/api/workflows", json={}, headers=_designer())
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == "untitled-workflow"
    assert body["name"] == "Untitled workflow"
    assert body["steps"] == []
    assert body["trigger"]["type"] == "manual"
    # Persisted + fetchable.
    got = client.get(f"/api/workflows/{body['id']}", headers=_designer())
    assert got.status_code == 200


def test_create_blank_with_name(client: TestClient) -> None:
    r = client.post("/api/workflows", json={"name": "My Cool Flow"}, headers=_designer())
    assert r.status_code == 201
    assert r.json()["id"] == "my-cool-flow"


def test_create_blank_id_dedupes(client: TestClient) -> None:
    first = client.post("/api/workflows", json={}, headers=_designer()).json()
    second = client.post("/api/workflows", json={}, headers=_designer()).json()
    assert first["id"] == "untitled-workflow"
    assert second["id"] == "untitled-workflow-2"


# --- POST /api/workflows (from template) ---


def test_create_from_template(client: TestClient) -> None:
    r = client.post("/api/workflows", json={"template_id": "beta-flow"}, headers=_designer())
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Beta Flow (copy)"
    assert body["id"] == "beta-flow-copy"
    # Structure copied verbatim.
    assert len(body["steps"]) == 2
    assert body["trigger"]["type"] == "webhook"
    assert {e["from"] for e in body["edges"]} == {"x"}


def test_create_from_unknown_template(client: TestClient) -> None:
    r = client.post("/api/workflows", json={"template_id": "ghost"}, headers=_designer())
    assert r.status_code == 404


# --- role gating ---


def test_create_requires_designer_or_admin(client: TestClient) -> None:
    r = client.post("/api/workflows", json={}, headers=_viewer())
    assert r.status_code == 403


def test_create_rejects_bad_body(client: TestClient) -> None:
    r = client.post(
        "/api/workflows",
        content="[1,2,3]",
        headers={**_designer(), "Content-Type": "application/json"},
    )
    assert r.status_code == 400


# --- default examples dir (CWD-independent regression) ---


def test_default_examples_dir_resolves_repo_root_regardless_of_cwd() -> None:
    """The bundled-examples default resolves to the repo-root `examples/` via
    the package location, not the process CWD. Guards the regression where
    launching uvicorn from `backend/` made the templates gallery come up empty
    (a bare `Path("examples")` resolved to the non-existent `backend/examples`).
    """
    examples = default_examples_dir()
    assert examples.is_dir()
    assert examples.name == "examples"
    ids = {t.id for t in load_templates(examples)}
    # The shipped examples must load — `pdf-classifier` is one of them.
    assert "pdf-classifier" in ids
    assert len(ids) >= 8
