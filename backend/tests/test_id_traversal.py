"""Workflow and step ids cannot escape the memory directory.

Found in the G30 pre-package review (2026-10-29). An agentic step's memory file
is `<memory dir>/steps/<workflow id>/<step id>.md`, and the engine loads it
into the step's system prompt. Ids were unconstrained strings, so a definition
imported with id `../../x` made the engine read ANY `.md` file the service
could reach into an agent's prompt — reproduced in-process, and it needed only
the `steps/` directory production already has (Linux resolves `..` through
existing directories only).

Read-only (the only memory WRITE is boot-time seeding of bundled, operator-
authored YAMLs), and not reachable through the scaffold, which mints its ids.
Two layers: ids are plain tokens at every definition path, and the memory
manager refuses any id that resolves outside its directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
from workflow_platform.main import create_app
from workflow_platform.memory.manager import MemoryManager
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.workflow import WorkflowDefinitionError, load_definition
from workflow_platform.world import mock_world

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _definition(workflow_id: str, step_id: str = "s") -> dict[str, object]:
    return {
        "id": workflow_id,
        "name": "x",
        "trigger": {"type": "manual"},
        "steps": [{"id": step_id, "type": "agentic", "goal": "hi", "model": MODEL, "tools": []}],
        "edges": [],
    }


@pytest.mark.parametrize("bad", ["../../outside", "a/b", ".hidden", "x..y", "", "/abs", "a b"])
def test_an_unsafe_workflow_id_is_refused(bad: str) -> None:
    with pytest.raises((WorkflowDefinitionError, ValueError)):
        load_definition(_definition(bad))


@pytest.mark.parametrize("bad", ["../private", "a/b", ".."])
def test_an_unsafe_step_id_is_refused(bad: str) -> None:
    with pytest.raises((WorkflowDefinitionError, ValueError)):
        load_definition(_definition("ok", bad))


@pytest.mark.parametrize("good", ["email-triage-apply", "step_1", "dmarc.ingest", "A1"])
def test_ordinary_ids_still_load(good: str) -> None:
    assert load_definition(_definition(good)).id == good


async def test_memory_refuses_an_id_that_resolves_outside_its_directory(tmp_path: Path) -> None:
    """Defense in depth: even an id the definition rule missed cannot reach
    out. `steps/` exists, as it does in production — the precondition the
    original reproduction needed."""
    (tmp_path / "base" / "steps").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "private.md").write_text("SECRET-NOTES")
    memory = MemoryManager(tmp_path / "base")
    with pytest.raises(ValueError, match="outside the memory directory"):
        await memory.load("steps/../../outside/private")
    assert await memory.load("steps/wf/step") == ""


async def test_an_ordinary_run_still_loads_its_memory(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path / "base")
    await memory.write_raw("steps/wf-1/s", "RUBRIC-TEXT")
    fake = FakeBedrock([text_response("hi")])
    engine = WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=default_function_registry(),
        tools=ToolCatalog([]),
        bedrock=fake,
        world=mock_world(),
        memory=memory,
    )
    await engine.run(load_definition(_definition("wf-1")))
    assert "RUBRIC-TEXT" in str(fake.calls[0].get("system"))


def test_import_refuses_a_traversal_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The path that was actually exploitable."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    client = TestClient(create_app(repositories=repos, start_triggers=False))
    import json

    r = client.post(
        "/api/workflows/import",
        content=json.dumps(_definition("../../outside")),
        headers={"X-Dev-User": "a", "X-Dev-Groups": "admins", "content-type": "application/json"},
    )
    assert r.status_code == 400, r.text
