"""Tests for conditional edges + skip propagation."""

from __future__ import annotations

from typing import Any

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.persistence import (
    StepExecutionState,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world


def _engine(fns: FunctionRegistry) -> WorkflowEngine:
    return WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )


async def test_active_branch_runs_inactive_branch_skipped() -> None:
    fns = FunctionRegistry()

    async def route(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"decision": "approve"}

    async def step(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"id": config.get("name")}

    fns.register("route", route)
    fns.register("step", step)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "route", "type": "deterministic", "function": "route"},
                {
                    "id": "approve",
                    "type": "deterministic",
                    "function": "step",
                    "config": {"name": "approve"},
                },
                {
                    "id": "reject",
                    "type": "deterministic",
                    "function": "step",
                    "config": {"name": "reject"},
                },
            ],
            "edges": [
                {
                    "from": "route",
                    "to": "approve",
                    "condition": "steps['route']['decision'] == 'approve'",
                },
                {
                    "from": "route",
                    "to": "reject",
                    "condition": "steps['route']['decision'] == 'reject'",
                },
            ],
        }
    )
    engine = _engine(fns)
    instance = await engine.run(definition)

    assert instance.state == WorkflowInstanceState.COMPLETED
    states_by_id = {
        s.step_id: s.state for s in await engine.repositories.steps.list_by_instance(instance.id)
    }
    assert states_by_id["route"] == StepExecutionState.COMPLETED
    assert states_by_id["approve"] == StepExecutionState.COMPLETED
    assert states_by_id["reject"] == StepExecutionState.SKIPPED


async def test_skip_propagates_through_chain() -> None:
    """If A → B is inactive and B → C is the only path to C, C is also skipped."""
    fns = FunctionRegistry()

    async def root(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"go": False}

    async def passthrough(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"called": True}

    fns.register("root", root)
    fns.register("passthrough", passthrough)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "root", "type": "deterministic", "function": "root"},
                {"id": "b", "type": "deterministic", "function": "passthrough"},
                {"id": "c", "type": "deterministic", "function": "passthrough"},
            ],
            "edges": [
                {"from": "root", "to": "b", "condition": "steps['root']['go']"},
                {"from": "b", "to": "c"},
            ],
        }
    )
    engine = _engine(fns)
    instance = await engine.run(definition)

    assert instance.state == WorkflowInstanceState.COMPLETED
    states_by_id = {
        s.step_id: s.state for s in await engine.repositories.steps.list_by_instance(instance.id)
    }
    assert states_by_id["root"] == StepExecutionState.COMPLETED
    assert states_by_id["b"] == StepExecutionState.SKIPPED
    assert states_by_id["c"] == StepExecutionState.SKIPPED


async def test_target_runs_if_at_least_one_incoming_edge_active() -> None:
    """A target with multiple parents runs if ANY incoming edge is active."""
    fns = FunctionRegistry()

    async def root(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return dict(config)

    async def merge(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"merged": True}

    fns.register("root", root)
    fns.register("merge", merge)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "root", "config": {"go": True}},
                {"id": "b", "type": "deterministic", "function": "root", "config": {"go": False}},
                {"id": "c", "type": "deterministic", "function": "merge"},
            ],
            "edges": [
                {"from": "a", "to": "c", "condition": "steps['a']['go']"},
                {"from": "b", "to": "c", "condition": "steps['b']['go']"},
            ],
        }
    )
    engine = _engine(fns)
    instance = await engine.run(definition)

    assert instance.state == WorkflowInstanceState.COMPLETED
    states_by_id = {
        s.step_id: s.state for s in await engine.repositories.steps.list_by_instance(instance.id)
    }
    assert states_by_id["c"] == StepExecutionState.COMPLETED
