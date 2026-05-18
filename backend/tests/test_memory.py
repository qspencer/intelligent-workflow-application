"""Tests for MemoryManager + agent memory injection."""

from __future__ import annotations

from pathlib import Path

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
)
from workflow_platform.memory import MemoryManager
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

MODEL = "anthropic.claude-3-haiku-20240307-v1:0"


async def test_memory_manager_load_returns_empty_for_unseen_agent(tmp_path: Path) -> None:
    mm = MemoryManager(tmp_path)
    assert await mm.load("steps/wf/x") == ""


async def test_memory_manager_append_creates_file_with_observation(tmp_path: Path) -> None:
    mm = MemoryManager(tmp_path)
    await mm.append("steps/wf/x", "Vendor template changed")
    content = await mm.load("steps/wf/x")
    assert "# Agent Memory: steps/wf/x" in content
    assert "## Recent Observations" in content
    assert "Vendor template changed" in content


async def test_memory_manager_append_inserts_recent_first(tmp_path: Path) -> None:
    mm = MemoryManager(tmp_path)
    await mm.append("a", "first")
    await mm.append("a", "second")
    content = await mm.load("a")
    second_idx = content.index("second")
    first_idx = content.index("first")
    # `second` was appended later and should appear before `first` (newest first).
    assert second_idx < first_idx


async def test_engine_injects_memory_into_agentic_step_system_prompt(tmp_path: Path) -> None:
    """A pre-existing memory file for an agent should be prepended to the
    system prompt the Agent sends to Bedrock."""
    repos = in_memory_repositories()
    mm = MemoryManager(tmp_path)
    await mm.append("steps/wf/act", "Last run, vendor ACME used DD/MM/YYYY date format.")

    bedrock = FakeBedrock([text_response("ack")])
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
        memory=mm,
    )

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "Acknowledge",
                    "model": MODEL,
                    "tools": [],
                }
            ],
            "edges": [],
        }
    )
    await engine.run(definition)

    assert len(bedrock.calls) == 1
    system_prompt = bedrock.calls[0]["system"][0]["text"]
    assert "Acknowledge" in system_prompt
    assert "Prior agent memory" in system_prompt
    assert "ACME used DD/MM/YYYY" in system_prompt


# --- Memory versioning in agent step output ---


async def test_agent_step_output_has_memory_hash_when_memory_loaded(tmp_path: Path) -> None:
    """When memory is loaded into the system prompt, the agent step's output
    records the memory_hash so audit-log consumers can correlate behavior with
    memory version."""
    repos = in_memory_repositories()
    mm = MemoryManager(tmp_path)
    await mm.append("steps/wf/act", "Vendor ACME uses DD/MM/YYYY")

    bedrock = FakeBedrock([text_response("ack")])
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
        memory=mm,
    )
    definition = load_definition(
        {
            "id": "wf",
            "name": "WF",
            "trigger": {"type": "manual"},
            "steps": [{"id": "act", "type": "agentic", "goal": "go", "model": MODEL, "tools": []}],
            "edges": [],
        }
    )
    await engine.run(definition)

    steps = await repos.steps.list_by_instance((await repos.instances.list_recent(limit=1))[0].id)
    assert steps[0].output is not None
    memory_hash = steps[0].output["memory_hash"]
    assert isinstance(memory_hash, str)
    assert memory_hash.startswith("sha256:")
    assert len(memory_hash) == len("sha256:") + 16


async def test_agent_step_memory_hash_changes_when_memory_changes(tmp_path: Path) -> None:
    """Two runs with different memory contents produce different memory_hash
    values — that's the whole point: a single audit query can show which run
    saw which memory version."""
    mm = MemoryManager(tmp_path)
    await mm.append("steps/wf/act", "First note")

    async def run_and_get_hash() -> str | None:
        repos = in_memory_repositories()
        bedrock = FakeBedrock([text_response("ack")])
        engine = WorkflowEngine(
            repositories=repos,
            functions=FunctionRegistry(),
            tools=ToolCatalog(),
            bedrock=bedrock,
            world=mock_world(),
            memory=mm,
        )
        definition = load_definition(
            {
                "id": "wf",
                "name": "WF",
                "trigger": {"type": "manual"},
                "steps": [
                    {"id": "act", "type": "agentic", "goal": "go", "model": MODEL, "tools": []}
                ],
                "edges": [],
            }
        )
        await engine.run(definition)
        steps = await repos.steps.list_by_instance(
            (await repos.instances.list_recent(limit=1))[0].id
        )
        assert steps[0].output is not None
        value = steps[0].output["memory_hash"]
        return value if isinstance(value, str) else None

    first_hash = await run_and_get_hash()
    await mm.append("steps/wf/act", "Second note — schema changed")
    second_hash = await run_and_get_hash()

    assert first_hash is not None
    assert second_hash is not None
    assert first_hash != second_hash


async def test_agent_step_memory_hash_is_none_when_no_memory_manager() -> None:
    """No MemoryManager → memory_hash is null in the step output."""
    repos = in_memory_repositories()
    bedrock = FakeBedrock([text_response("ack")])
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
        # no memory= kwarg
    )
    definition = load_definition(
        {
            "id": "wf",
            "name": "WF",
            "trigger": {"type": "manual"},
            "steps": [{"id": "act", "type": "agentic", "goal": "go", "model": MODEL, "tools": []}],
            "edges": [],
        }
    )
    await engine.run(definition)
    steps = await repos.steps.list_by_instance((await repos.instances.list_recent(limit=1))[0].id)
    assert steps[0].output is not None
    assert steps[0].output["memory_hash"] is None
