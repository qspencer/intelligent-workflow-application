"""Tests for `WorkflowEngine.fork` — forking an instance at a specific step,
preserving topological-ancestor outputs and re-running from there.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
)
from workflow_platform.engine.executor import _ancestors
from workflow_platform.persistence import (
    StepExecutionState,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

# --- _ancestors helper ---


def test_ancestors_empty_for_root_step() -> None:
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "noop"},
                {"id": "b", "type": "deterministic", "function": "noop"},
            ],
            "edges": [{"from": "a", "to": "b"}],
        }
    )
    assert _ancestors(definition, "a") == set()
    assert _ancestors(definition, "b") == {"a"}


def test_ancestors_traverses_diamond() -> None:
    # Diamond: a → {b, c} → d
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [{"id": s, "type": "deterministic", "function": "noop"} for s in "abcd"],
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "a", "to": "c"},
                {"from": "b", "to": "d"},
                {"from": "c", "to": "d"},
            ],
        }
    )
    assert _ancestors(definition, "d") == {"a", "b", "c"}
    assert _ancestors(definition, "b") == {"a"}
    assert _ancestors(definition, "c") == {"a"}


# --- engine.fork ---


async def _run_workflow_then_fork_at(
    fork_step: str, *, definition_dict: dict[str, Any] | None = None
) -> tuple[Any, Any, Any, dict[str, int]]:
    """Helper: run a 3-step deterministic workflow, then fork from `fork_step`.
    Returns (repos, source_instance, forked_instance)."""
    counts: dict[str, int] = {"a": 0, "b": 0, "c": 0}

    fns = FunctionRegistry()

    async def step_a(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        counts["a"] += 1
        return {"v": "a-output", "run": counts["a"]}

    async def step_b(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        counts["b"] += 1
        return {"v": f"b-saw-{ctx.steps['a']['v']}", "run": counts["b"]}

    async def step_c(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        counts["c"] += 1
        return {"v": f"c-saw-{ctx.steps['b']['v']}", "run": counts["c"]}

    fns.register("step_a", step_a)
    fns.register("step_b", step_b)
    fns.register("step_c", step_c)

    definition = load_definition(
        definition_dict
        or {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "step_a"},
                {"id": "b", "type": "deterministic", "function": "step_b"},
                {"id": "c", "type": "deterministic", "function": "step_c"},
            ],
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "b", "to": "c"},
            ],
        }
    )
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )
    source = await engine.run(definition)
    forked = await engine.fork(definition, source.id, fork_step)
    return repos, source, forked, counts


async def test_fork_at_root_reruns_all_steps() -> None:
    """Forking at the first step is equivalent to a clean re-run."""
    repos, source, forked, counts = await _run_workflow_then_fork_at("a")
    assert source.state == WorkflowInstanceState.COMPLETED
    assert forked.state == WorkflowInstanceState.COMPLETED
    assert source.id != forked.id
    # Each step ran twice total (once in source, once in fork).
    assert counts == {"a": 2, "b": 2, "c": 2}
    # The forked instance has its own step executions.
    forked_steps = await repos.steps.list_by_instance(forked.id)
    assert sorted(s.step_id for s in forked_steps) == ["a", "b", "c"]
    assert all(s.state == StepExecutionState.COMPLETED for s in forked_steps)


async def test_fork_at_middle_preserves_upstream_reruns_downstream() -> None:
    """Forking at `b` preserves `a`'s output, re-runs `b` + `c`."""
    repos, _source, forked, counts = await _run_workflow_then_fork_at("b")
    assert forked.state == WorkflowInstanceState.COMPLETED
    # `a` ran once total (preserved on the fork); `b` and `c` ran twice.
    assert counts == {"a": 1, "b": 2, "c": 2}

    forked_steps = {s.step_id: s for s in await repos.steps.list_by_instance(forked.id)}
    # All three steps present, all COMPLETED.
    assert set(forked_steps.keys()) == {"a", "b", "c"}
    # `a` carried over from the source (same output, run=1).
    assert forked_steps["a"].output == {"v": "a-output", "run": 1}
    # `b` ran fresh (run=2 on the global counter).
    assert forked_steps["b"].output is not None
    assert forked_steps["b"].output["run"] == 2


async def test_fork_at_leaf_only_reruns_leaf() -> None:
    """Forking at `c` preserves `a` + `b`, re-runs only `c`."""
    repos, _source, forked, counts = await _run_workflow_then_fork_at("c")
    assert counts == {"a": 1, "b": 1, "c": 2}
    forked_steps = {s.step_id: s for s in await repos.steps.list_by_instance(forked.id)}
    # Preserved outputs carry over verbatim.
    assert forked_steps["a"].output == {"v": "a-output", "run": 1}
    assert forked_steps["b"].output is not None
    assert forked_steps["b"].output["run"] == 1


async def test_fork_records_workflow_forked_audit_entry() -> None:
    repos, source, forked, _ = await _run_workflow_then_fork_at("b")
    audit = await repos.audit.list_by_instance(forked.id)
    fork_entries = [e for e in audit if e.action == "workflow_forked"]
    assert len(fork_entries) == 1
    detail = fork_entries[0].detail
    assert detail["source_instance_id"] == source.id
    assert detail["from_step_id"] == "b"
    assert detail["preserved_step_ids"] == ["a"]


async def test_fork_rejects_unknown_step() -> None:
    fns = FunctionRegistry()

    async def s(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {}

    fns.register("s", s)
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [{"id": "a", "type": "deterministic", "function": "s"}],
            "edges": [],
        }
    )
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )
    inst = await engine.run(definition)
    with pytest.raises(ValueError, match="not in workflow"):
        await engine.fork(definition, inst.id, "nonexistent")


async def test_fork_rejects_unknown_instance() -> None:
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [{"id": "a", "type": "deterministic", "function": "noop"}],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )
    with pytest.raises(ValueError, match="not found"):
        await engine.fork(definition, "does-not-exist", "a")


async def test_fork_reruns_with_current_agent_memory() -> None:
    """The whole point of fork: a re-run picks up agent memory state at fork
    time. Simulated by changing the FakeBedrock response between runs."""
    fns = FunctionRegistry()
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
                    "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                    "tools": [],
                    "goal": "go",
                    "policy": {"max_iterations": 1, "max_total_tokens": 1000},
                }
            ],
            "edges": [],
        }
    )
    # First response for the original run; second response for the fork's re-run.
    bedrock = FakeBedrock(
        [
            text_response("v1 response"),
            text_response("v2 response"),
        ]
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )
    source = await engine.run(definition)
    forked = await engine.fork(definition, source.id, "act")

    src_steps = await repos.steps.list_by_instance(source.id)
    fork_steps = await repos.steps.list_by_instance(forked.id)
    assert src_steps[0].output is not None
    assert fork_steps[0].output is not None
    assert src_steps[0].output["output_text"] == "v1 response"
    assert fork_steps[0].output["output_text"] == "v2 response"
