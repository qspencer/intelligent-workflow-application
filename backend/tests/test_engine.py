"""Tests for the workflow engine."""

from __future__ import annotations

from typing import Any

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.engine import (
    FunctionRegistry,
    StepFailure,
    ToolCatalog,
    WorkflowEngine,
)
from workflow_platform.persistence import (
    StepExecutionState,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.tools import FileWriteTool
from workflow_platform.workflow import load_definition
from workflow_platform.world import MockFilesystem, mock_world

MODEL = "anthropic.claude-3-haiku-20240307-v1:0"


def _record_step_calls(record: list[str]) -> dict[str, Any]:
    """Build a function registry that appends step ids to `record` when called."""

    async def make(step_id: str) -> Any:
        async def fn(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
            record.append(step_id)
            return {"step_id": step_id, "config": config}

        return fn

    return {"make": make}


async def test_deterministic_workflow_runs_to_completion() -> None:
    repos = in_memory_repositories()
    record: list[str] = []
    fns = FunctionRegistry()

    async def step_a(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        record.append("a")
        return {"value": 1}

    async def step_b(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        record.append("b")
        return {"value": ctx.steps["a"]["value"] + 1}

    fns.register("step_a", step_a)
    fns.register("step_b", step_b)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "step_a"},
                {"id": "b", "type": "deterministic", "function": "step_b"},
            ],
            "edges": [{"from": "a", "to": "b"}],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )

    instance = await engine.run(definition, trigger_payload={"hello": "world"})

    assert record == ["a", "b"]
    assert instance.state == WorkflowInstanceState.COMPLETED
    assert instance.error is None
    assert instance.context["steps"] == {"a": {"value": 1}, "b": {"value": 2}}

    steps = await repos.steps.list_by_instance(instance.id)
    assert {s.step_id for s in steps} == {"a", "b"}
    assert all(s.state == StepExecutionState.COMPLETED for s in steps)

    audit = await repos.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert actions[0] == "workflow_started"
    assert actions[-1] == "workflow_completed"
    assert "step_started" in actions and "step_completed" in actions


async def test_unknown_function_fails_workflow() -> None:
    repos = in_memory_repositories()
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [{"id": "a", "type": "deterministic", "function": "missing"}],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )

    instance = await engine.run(definition)

    assert instance.state == WorkflowInstanceState.FAILED
    assert instance.error is not None
    assert "missing" in instance.error
    audit = await repos.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert "step_failed" in actions
    assert actions[-1] == "workflow_failed"


async def test_step_function_raising_step_failure() -> None:
    repos = in_memory_repositories()
    fns = FunctionRegistry()

    async def boom(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        raise StepFailure("intentional")

    fns.register("boom", boom)
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [{"id": "boom", "type": "deterministic", "function": "boom"}],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )

    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.FAILED
    assert instance.error == "intentional"


async def test_agentic_step_runs_through_engine() -> None:
    repos = in_memory_repositories()
    bedrock = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[("c1", "file_write", {"path": "/result.txt", "content": "ok"})]
            ),
            text_response("Wrote the file."),
        ]
    )
    world = mock_world()

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "Write 'ok' to /result.txt",
                    "model": MODEL,
                    "tools": ["file_write"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([FileWriteTool()]),
        bedrock=bedrock,
        world=world,
    )

    instance = await engine.run(definition, trigger_payload={"source": "test"})

    assert instance.state == WorkflowInstanceState.COMPLETED
    fs = world.fs
    assert isinstance(fs, MockFilesystem)
    assert fs.files["/result.txt"] == b"ok"

    audit = await repos.audit.list_by_instance(instance.id)
    tool_call_entries = [e for e in audit if e.action == "tool_call"]
    assert len(tool_call_entries) == 1
    assert tool_call_entries[0].detail["name"] == "file_write"


async def test_agentic_step_unknown_tool_fails() -> None:
    repos = in_memory_repositories()
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "...",
                    "model": MODEL,
                    "tools": ["does_not_exist"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )

    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.FAILED
    assert instance.error is not None
    assert "does_not_exist" in instance.error
