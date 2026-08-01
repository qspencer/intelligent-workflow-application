"""Tests for parallel DAG execution.

Independent branches must execute concurrently. A failure must cancel siblings
that are still in flight; downstream dependents must not start.
"""

from __future__ import annotations

import asyncio
import time
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


async def test_two_independent_steps_run_concurrently() -> None:
    """Two leaf steps with no edge between them should run in parallel."""
    repos = in_memory_repositories()
    fns = FunctionRegistry()

    async def slow(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        await asyncio.sleep(0.2)
        return {"ok": True}

    fns.register("slow", slow)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "slow"},
                {"id": "b", "type": "deterministic", "function": "slow"},
            ],
            "edges": [],  # both are roots
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )

    start = time.perf_counter()
    instance = await engine.run(definition)
    elapsed = time.perf_counter() - start

    assert instance.state == WorkflowInstanceState.COMPLETED
    # Sequential would be ~0.4s; parallel should be ~0.2s (allow generous slack).
    assert elapsed < 0.35, f"Expected parallel (<0.35s), got {elapsed:.3f}s"


async def test_failure_in_one_branch_cancels_pending_siblings() -> None:
    """If one parallel branch fails fast, the other should not be allowed to
    keep running indefinitely; the workflow fails and pending tasks cancel.
    """
    repos = in_memory_repositories()
    fns = FunctionRegistry()
    sentinel: dict[str, Any] = {"slow_completed": False}

    async def fast_fail(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        raise StepFailure("boom")

    async def slow_marker(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        try:
            await asyncio.sleep(2.0)
            sentinel["slow_completed"] = True
        except asyncio.CancelledError:
            sentinel["slow_completed"] = False
            raise
        return {"ok": True}

    fns.register("fast_fail", fast_fail)
    fns.register("slow_marker", slow_marker)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "fast_fail"},
                {"id": "b", "type": "deterministic", "function": "slow_marker"},
            ],
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
    assert sentinel["slow_completed"] is False  # the slow task was cancelled

    # The cancelled sibling is persisted CANCELLED — distinct from the
    # failing step's FAILED and from an unscheduled PENDING (external review
    # 2026-08-01). Previously it was stranded RUNNING.
    steps = {s.step_id: s for s in await repos.steps.list_by_instance(instance.id)}
    assert steps["a"].state == StepExecutionState.FAILED
    assert steps["b"].state == StepExecutionState.CANCELLED
    assert steps["b"].completed_at is not None


async def test_diamond_topology_runs_branches_in_parallel() -> None:
    """A → {B, C} → D : B and C must run in parallel."""
    repos = in_memory_repositories()
    fns = FunctionRegistry()

    async def step_a(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"v": 0}

    async def step_branch(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        await asyncio.sleep(0.15)
        return {"v": 1}

    async def step_d(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"v": ctx.steps["b"]["v"] + ctx.steps["c"]["v"]}

    fns.register("step_a", step_a)
    fns.register("step_branch", step_branch)
    fns.register("step_d", step_d)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "step_a"},
                {"id": "b", "type": "deterministic", "function": "step_branch"},
                {"id": "c", "type": "deterministic", "function": "step_branch"},
                {"id": "d", "type": "deterministic", "function": "step_d"},
            ],
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "a", "to": "c"},
                {"from": "b", "to": "d"},
                {"from": "c", "to": "d"},
            ],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )

    start = time.perf_counter()
    instance = await engine.run(definition)
    elapsed = time.perf_counter() - start

    assert instance.state == WorkflowInstanceState.COMPLETED
    assert instance.context["steps"]["d"]["v"] == 2
    # 2 sequential branch calls = 0.3s; parallel = 0.15s.
    assert elapsed < 0.25, f"Expected parallel branches, got {elapsed:.3f}s"

    steps = await repos.steps.list_by_instance(instance.id)
    assert all(s.state == StepExecutionState.COMPLETED for s in steps)


async def test_unexpected_exception_cancels_mutating_sibling() -> None:
    """External review 2026-08-01 finding 1: an UNEXPECTED (non-StepFailure)
    branch exception must still cancel in-flight siblings — previously a
    RuntimeError escaped the dispatch loop and a mutating sibling kept
    running after the workflow was marked FAILED."""
    repos = in_memory_repositories()
    fns = FunctionRegistry()
    sentinel: dict[str, Any] = {"sibling_mutated": False}

    async def fast_unexpected(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        raise RuntimeError("not a StepFailure")  # unexpected, non-StepFailure

    async def slow_mutator(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        try:
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            raise
        sentinel["sibling_mutated"] = True  # the "external mutation" — must NOT run
        return {"ok": True}

    fns.register("fast_unexpected", fast_unexpected)
    fns.register("slow_mutator", slow_mutator)
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "fast_unexpected"},
                {"id": "b", "type": "deterministic", "function": "slow_mutator"},
            ],
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
    assert sentinel["sibling_mutated"] is False, "mutating sibling ran after failure"
    steps = {s.step_id: s for s in await repos.steps.list_by_instance(instance.id)}
    # The originating step is FAILED (not stranded RUNNING) with its error;
    # the cancelled sibling is CANCELLED (external review 2026-08-01 F1 pt 2).
    assert steps["a"].state == StepExecutionState.FAILED
    assert steps["a"].error == "not a StepFailure"
    assert steps["b"].state == StepExecutionState.CANCELLED
