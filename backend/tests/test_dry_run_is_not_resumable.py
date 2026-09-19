"""A dry run is a probe, not work in progress (2026-09-19).

`dry_run` was tagged onto `instance.context` by the API *after* the run.
The engine never read it, so `resume` / `retry` / `fork` re-drove a dry-run
instance against the REAL world and real tools — the sandbox lived in the
engine that ran it, not in the record. Worse, the resume path rebuilds the
context from scratch and erased the tag, so a retried dry run also lost the
only evidence it had ever been one.

Found by retrying one: the most recent FAILED instance happened to be a
failed dry run, and it was re-driven without complaint. It died at the
first step for unrelated reasons, which is luck, not design — a dry run
that fails at the ACT step would, on retry, apply real Gmail labels.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine.executor import (
    DryRunNotResumable,
    ToolCatalog,
    WorkflowEngine,
)
from workflow_platform.engine.registry import FunctionRegistry
from workflow_platform.persistence import (
    WorkflowInstance,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world


def _definition() -> Any:
    return load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [{"id": "a", "type": "deterministic", "function": "noop", "config": {}}],
            "edges": [],
        }
    )


async def _noop(config: Any, ctx: Any, world: Any) -> dict[str, Any]:
    return {"ok": True}


def _engine(repos: Any, *, dry_run: bool = False) -> WorkflowEngine:
    registry = FunctionRegistry()
    registry.register("noop", _noop)
    return WorkflowEngine(
        repositories=repos,
        functions=registry,
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        dry_run=dry_run,
    )


async def test_a_dry_run_is_marked_from_the_START_not_tagged_afterwards() -> None:
    """The flag has to be on the record before anything can go wrong with
    it. Tagged after the run, it was absent for any run that never got
    there, and erasable by the next context write."""
    repos = in_memory_repositories()
    await repos.definitions.save(_definition())
    instance = await _engine(repos, dry_run=True).run(_definition(), trigger_payload={})
    fresh = await repos.instances.get(instance.id)
    assert fresh is not None
    assert fresh.context.get("dry_run") is True


async def test_a_real_run_is_not_marked_as_one() -> None:
    repos = in_memory_repositories()
    await repos.definitions.save(_definition())
    instance = await _engine(repos).run(_definition(), trigger_payload={})
    fresh = await repos.instances.get(instance.id)
    assert fresh is not None
    assert not fresh.context.get("dry_run")


@pytest.mark.parametrize("verb", ["resume", "fork"])
async def test_the_engine_refuses_to_re_drive_a_dry_run(verb: str) -> None:
    """Enforced in the ENGINE, not only in the API: `tools/fire.py` and the
    orchestrator reach these methods directly."""
    repos = in_memory_repositories()
    await repos.definitions.save(_definition())
    instance = await repos.instances.create(
        WorkflowInstance(
            workflow_id="wf",
            state=WorkflowInstanceState.PAUSED,
            context={"dry_run": True},
        )
    )
    engine = _engine(repos)
    with pytest.raises(DryRunNotResumable):
        if verb == "resume":
            await engine.resume(_definition(), instance.id)
        else:
            await engine.fork(_definition(), instance.id, "a")


async def test_a_normal_instance_is_still_resumable() -> None:
    """The counterpart. A refusal that refuses everything is not a guard."""
    repos = in_memory_repositories()
    await repos.definitions.save(_definition())
    instance = await repos.instances.create(
        WorkflowInstance(workflow_id="wf", state=WorkflowInstanceState.PAUSED)
    )
    result = await _engine(repos).resume(_definition(), instance.id)
    assert result.state is WorkflowInstanceState.COMPLETED
