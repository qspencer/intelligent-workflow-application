"""Tests for workflow pause + resume."""

from __future__ import annotations

import asyncio
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


async def test_pause_then_resume_completes_remaining_steps() -> None:
    """Run a workflow with a sentinel that holds A; mid-flight set the instance
    to PAUSED; engine should exit cleanly. Then call resume; the workflow
    should continue from where it left off (B runs after resume).
    """
    repos = in_memory_repositories()
    fns = FunctionRegistry()
    a_done = asyncio.Event()

    async def step_a(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        a_done.set()
        return {"v": 1}

    async def step_b(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"v": ctx.steps["a"]["v"] + 1}

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

    # Start the workflow as a background task. A will complete; before B runs,
    # we mutate the instance state to PAUSED — engine notices on next iteration.
    run_task = asyncio.create_task(engine.run(definition))

    # Wait until A has completed.
    await asyncio.wait_for(a_done.wait(), timeout=5.0)

    # Find the running instance and mark it paused.
    instances = await repos.instances.list_by_workflow("wf")
    assert len(instances) == 1
    instance = instances[0]
    instance.state = WorkflowInstanceState.PAUSED
    await repos.instances.update(instance)

    paused_instance = await asyncio.wait_for(run_task, timeout=5.0)
    assert paused_instance.state == WorkflowInstanceState.PAUSED

    # B has not yet run.
    steps = await repos.steps.list_by_instance(paused_instance.id)
    states_by_id = {s.step_id: s.state for s in steps}
    assert states_by_id.get("a") == StepExecutionState.COMPLETED
    assert "b" not in states_by_id

    # Resume.
    resumed = await engine.resume(definition, paused_instance.id)
    assert resumed.state == WorkflowInstanceState.COMPLETED

    steps = await repos.steps.list_by_instance(paused_instance.id)
    states_by_id = {s.step_id: s.state for s in steps}
    assert states_by_id["a"] == StepExecutionState.COMPLETED
    assert states_by_id["b"] == StepExecutionState.COMPLETED

    audit = await repos.audit.list_by_instance(paused_instance.id)
    actions = [e.action for e in audit]
    assert "workflow_paused" in actions
    assert "workflow_resumed" in actions
    assert actions[-1] == "workflow_completed"
