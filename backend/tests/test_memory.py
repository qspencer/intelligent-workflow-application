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
