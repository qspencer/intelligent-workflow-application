"""Tool-parameter pinning (external review 2026-07-31, finding 2): the
engine forces pinned tool params from context; the model cannot choose or
alter them, and an override attempt is audited."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.agent import Agent, AgentPolicy
from workflow_platform.tools import Tool, ToolContext, ToolResult

pytestmark = pytest.mark.asyncio


class _RecordingTool(Tool):
    name = "record_call"
    description = "records the params it was dispatched with"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}
    effect = "read_only"

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        self.seen.append(dict(params))
        return ToolResult(output={"ok": True})


def _agent(tool: _RecordingTool, pinned: dict[str, Any], model_params: dict[str, Any]) -> Agent:
    bedrock = FakeBedrock(
        [
            tool_use_response(tool_uses=[("tu-1", "record_call", model_params)]),
            text_response("done"),
        ]
    )
    return Agent(
        system_prompt="s",
        tools=[tool],
        model_id="claude-haiku-4-5",
        bedrock=bedrock,
        policy=AgentPolicy(max_iterations=3),
        pinned_tool_params=pinned,
    )


async def test_pinned_param_overrides_model_value() -> None:
    tool = _RecordingTool()
    # The model tries to label a DIFFERENT message; the pin forces the real id.
    agent = _agent(
        tool,
        pinned={"message_id": "real-123"},
        model_params={"message_id": "attacker-999", "labels": ["wf/newsletter"]},
    )
    result = await agent.run("go")
    assert tool.seen[0]["message_id"] == "real-123"  # forced, not attacker-999
    assert tool.seen[0]["labels"] == ["wf/newsletter"]  # unpinned param untouched
    (call,) = result.tool_calls
    assert call.input["message_id"] == "real-123"
    assert "message_id" in call.pinned
    assert call.pin_overrides == ["message_id"]  # the attempt is flagged


async def test_pin_injected_when_model_omits_it() -> None:
    tool = _RecordingTool()
    agent = _agent(tool, pinned={"message_id": "real-123"}, model_params={"labels": ["x"]})
    result = await agent.run("go")
    assert tool.seen[0]["message_id"] == "real-123"  # injected even though model omitted it
    (call,) = result.tool_calls
    assert call.pin_overrides == []  # omission is not an override attempt


async def test_no_override_flagged_when_model_agrees() -> None:
    tool = _RecordingTool()
    agent = _agent(tool, pinned={"message_id": "real-123"}, model_params={"message_id": "real-123"})
    result = await agent.run("go")
    (call,) = result.tool_calls
    assert call.pinned == ["message_id"]
    assert call.pin_overrides == []  # model happened to agree — no probe signal


async def test_multiple_pins() -> None:
    tool = _RecordingTool()
    agent = _agent(
        tool,
        pinned={"message_id": "real-123", "labels": ["wf/notification"]},
        model_params={"message_id": "x", "labels": ["wf/spam"]},
    )
    await agent.run("go")
    assert tool.seen[0] == {"message_id": "real-123", "labels": ["wf/notification"]}


async def test_unresolved_pin_fails_step_closed() -> None:
    """External review 2026-08-01 finding 2: a pin whose context path is
    missing/None FAILS the step before dispatch — it must NOT silently drop
    the pin and let the model choose the value (a security boundary must
    fail closed)."""
    from workflow_platform.engine import (
        FunctionRegistry,
        ToolCatalog,
        WorkflowEngine,
    )
    from workflow_platform.persistence import WorkflowInstanceState, in_memory_repositories
    from workflow_platform.workflow import load_definition
    from workflow_platform.world import mock_world

    repos = in_memory_repositories()
    # An agentic step that pins message_id from a trigger field that is ABSENT.
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "label it",
                    "model": "claude-haiku-4-5",
                    "tools": [],
                    "pin_params": {"message_id": "trigger.message_id"},
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=FakeBedrock([text_response("hi")]),
        world=mock_world(),
    )
    # trigger has NO message_id → the pin can't resolve → step fails closed.
    instance = await engine.run(definition, trigger_payload={})
    assert instance.state == WorkflowInstanceState.FAILED
    entries = await repos.audit.list_by_instance(instance.id)
    unresolved = [e for e in entries if e.action == "tool_pin_unresolved"]
    assert unresolved and unresolved[0].detail["param"] == "message_id"
