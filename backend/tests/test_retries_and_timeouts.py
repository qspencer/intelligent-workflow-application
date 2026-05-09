"""Tests for per-step retries + per-step / per-workflow timeouts."""

from __future__ import annotations

import asyncio
from typing import Any

from tests._bedrock_fakes import FakeBedrock
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


async def test_step_retries_succeed_on_second_attempt() -> None:
    fns = FunctionRegistry()
    counter: dict[str, int] = {"calls": 0}

    async def flaky(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        counter["calls"] += 1
        if counter["calls"] < 2:
            raise StepFailure("transient")
        return {"ok": True}

    fns.register("flaky", flaky)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "a",
                    "type": "deterministic",
                    "function": "flaky",
                    "runtime": {"retries": 2},
                }
            ],
            "edges": [],
        }
    )
    engine = _engine(fns)
    instance = await engine.run(definition)

    assert instance.state == WorkflowInstanceState.COMPLETED
    assert counter["calls"] == 2
    audit = await engine.repositories.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert "step_retry" in actions
    assert "step_completed" in actions


async def test_step_retries_exhausted_fails_workflow() -> None:
    fns = FunctionRegistry()

    async def always_fails(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        raise StepFailure("persistent")

    fns.register("always_fails", always_fails)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "a",
                    "type": "deterministic",
                    "function": "always_fails",
                    "runtime": {"retries": 3},
                }
            ],
            "edges": [],
        }
    )
    engine = _engine(fns)
    instance = await engine.run(definition)

    assert instance.state == WorkflowInstanceState.FAILED
    audit = await engine.repositories.audit.list_by_instance(instance.id)
    retry_count = sum(1 for e in audit if e.action == "step_retry")
    assert retry_count == 3  # initial attempt + 3 retries = 4 total, 3 of those fired step_retry


async def test_step_timeout_is_treated_as_failure() -> None:
    fns = FunctionRegistry()

    async def slow(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        await asyncio.sleep(2.0)
        return {"ok": True}

    fns.register("slow", slow)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "a",
                    "type": "deterministic",
                    "function": "slow",
                    "runtime": {"timeout_seconds": 0.1},
                }
            ],
            "edges": [],
        }
    )
    engine = _engine(fns)
    instance = await engine.run(definition)

    assert instance.state == WorkflowInstanceState.FAILED
    assert instance.error is not None
    assert "timeout" in instance.error.lower()
    steps = await engine.repositories.steps.list_by_instance(instance.id)
    assert steps[-1].state == StepExecutionState.FAILED


async def test_workflow_timeout_kills_in_flight_steps() -> None:
    fns = FunctionRegistry()

    async def slow(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        await asyncio.sleep(2.0)
        return {"ok": True}

    fns.register("slow", slow)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "policies": {"timeout_seconds": 0.1},
            "steps": [{"id": "a", "type": "deterministic", "function": "slow"}],
            "edges": [],
        }
    )
    engine = _engine(fns)
    instance = await engine.run(definition)

    assert instance.state == WorkflowInstanceState.FAILED
    assert instance.error == "workflow timeout"


async def test_step_retry_success_after_timeout() -> None:
    """A step that times out the first attempt but succeeds on retry."""
    fns = FunctionRegistry()
    counter: dict[str, int] = {"calls": 0}

    async def flaky_slow(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        counter["calls"] += 1
        if counter["calls"] == 1:
            await asyncio.sleep(2.0)  # will be cancelled by timeout
        return {"ok": True}

    fns.register("flaky_slow", flaky_slow)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "a",
                    "type": "deterministic",
                    "function": "flaky_slow",
                    "runtime": {"timeout_seconds": 0.1, "retries": 1},
                }
            ],
            "edges": [],
        }
    )
    engine = _engine(fns)
    instance = await engine.run(definition)

    assert instance.state == WorkflowInstanceState.COMPLETED
    assert counter["calls"] == 2
