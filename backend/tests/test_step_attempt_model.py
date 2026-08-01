"""The immutable-attempt model (docs/EXECUTION_SEMANTICS.md §3a): each
execution attempt is its own immutable step_executions row with a 1-based
`attempt`; a retry APPENDS a new row and never mutates a prior attempt's."""

from __future__ import annotations

from typing import Any

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine import FunctionRegistry, StepFailure, ToolCatalog, WorkflowEngine
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


def _definition(function: str, retries: int) -> Any:
    return load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "a",
                    "type": "deterministic",
                    "function": function,
                    "runtime": {"retries": retries},
                }
            ],
            "edges": [],
        }
    )


async def test_retries_append_immutable_attempt_rows() -> None:
    fns = FunctionRegistry()
    counter = {"calls": 0}

    async def flaky(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        counter["calls"] += 1
        if counter["calls"] < 2:
            raise StepFailure("transient")
        return {"ok": True}

    fns.register("flaky", flaky)
    engine = _engine(fns)
    instance = await engine.run(_definition("flaky", retries=2))
    assert instance.state == WorkflowInstanceState.COMPLETED

    rows = [
        s for s in await engine.repositories.steps.list_by_instance(instance.id) if s.step_id == "a"
    ]
    by_attempt = {s.attempt: s for s in rows}
    # Two distinct immutable rows: attempt 1 FAILED, attempt 2 COMPLETED.
    assert set(by_attempt) == {1, 2}
    assert by_attempt[1].id != by_attempt[2].id
    assert by_attempt[1].state is StepExecutionState.FAILED
    assert by_attempt[1].error == "transient"  # prior attempt's row untouched
    assert by_attempt[2].state is StepExecutionState.COMPLETED
    assert by_attempt[2].output == {"ok": True}
    # The step counts as done (a COMPLETED attempt exists) — downstream reads it.
    assert instance.context["steps"]["a"]["ok"] is True


async def test_attempt_numbers_increase_on_exhausted_retries() -> None:
    fns = FunctionRegistry()

    async def always_fails(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        raise StepFailure("persistent")

    fns.register("always_fails", always_fails)
    engine = _engine(fns)
    instance = await engine.run(_definition("always_fails", retries=2))
    assert instance.state == WorkflowInstanceState.FAILED

    rows = sorted(
        (
            s
            for s in await engine.repositories.steps.list_by_instance(instance.id)
            if s.step_id == "a"
        ),
        key=lambda s: s.attempt,
    )
    # Three attempts, each its own row, all FAILED and all preserved.
    assert [s.attempt for s in rows] == [1, 2, 3]
    assert len({s.id for s in rows}) == 3
    assert all(s.state is StepExecutionState.FAILED for s in rows)
