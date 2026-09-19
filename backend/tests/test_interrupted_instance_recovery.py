"""Interrupted runs must not be stranded RUNNING (found live 2026-09-19).

THE DEFECT. `asyncio.CancelledError` derives from `BaseException`, not
`Exception`. `_dispatch_loop` caught `BaseException`, marked the in-flight
steps CANCELLED and re-raised; `_drive_inner`'s handler chain ended at
`except Exception`, so nothing marked the instance and nothing audited a
terminal event. The row stayed RUNNING forever.

On the live box that was 171 instances (oldest from July), and 26,462
`alert_stuck_workflow` rows about them — the largest single action in the
audit log, all of it one symptom counted over and over because the
monitoring de-dupe is per-process and the dev server reloaded 90 times a
day.

The amplifier was autoreload; the mechanism is not a dev artifact. A
`systemctl restart`, a SIGTERM in production, or an outer `asyncio.timeout`
around a run cancels the same way.

Two halves, both tested here: the in-process handler (an orderly
cancellation), and the boot sweep (what a hard kill leaves behind, plus
everything stranded before the handler existed).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.audit_writer import AuditWriter
from workflow_platform.engine.executor import ToolCatalog, WorkflowEngine
from workflow_platform.engine.registry import FunctionRegistry
from workflow_platform.persistence import (
    StepExecution,
    StepExecutionState,
    WorkflowInstance,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.recovery import (
    DISABLE_ENV_VAR,
    sweep_interrupted_instances,
)
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

#: Set by the blocking step so a test knows the run really is mid-step
#: before cancelling it. A module-level `Event` binds to the first loop that
#: touches it and then raises "bound to a different event loop" in the next
#: test, so it is created per test and handed over here.
_STARTED: list[asyncio.Event] = []


def _arm() -> asyncio.Event:
    _STARTED.clear()
    event = asyncio.Event()
    _STARTED.append(event)
    return event


async def _blocks_forever(config: Any, ctx: Any, world: Any) -> dict[str, Any]:
    """A step that is still running when the cancellation arrives — the
    position all 171 production orphans were caught in."""
    if _STARTED:
        _STARTED[0].set()
    await asyncio.sleep(3600)
    return {}


def _engine(repos: Any) -> WorkflowEngine:
    registry = FunctionRegistry()
    registry.register("blocks", _blocks_forever)
    return WorkflowEngine(
        repositories=repos,
        functions=registry,
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )


def _definition() -> Any:
    return load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [{"id": "slow", "type": "deterministic", "function": "blocks", "config": {}}],
            "edges": [],
        }
    )


# --- half 1: the in-process handler ---------------------------------------


async def test_a_cancelled_run_is_marked_PAUSED_not_left_RUNNING() -> None:
    """THE REGRESSION TEST. Cancel a run mid-step, exactly as a reload or a
    SIGTERM does, and the instance must not be left RUNNING."""
    started = _arm()
    repos = in_memory_repositories()
    engine = _engine(repos)

    task = asyncio.create_task(engine.run(_definition(), trigger_payload={}))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    instances = await repos.instances.list_recent(limit=10)
    assert len(instances) == 1
    assert instances[0].state is WorkflowInstanceState.PAUSED, (
        f"a cancelled run left the instance {instances[0].state.value} — this is the "
        "defect: CancelledError is a BaseException, so `except Exception` never saw it"
    )
    assert "interrupted" in (instances[0].error or "")


async def test_a_cancelled_run_audits_a_terminal_event() -> None:
    """The other half of the strandedness: the audit trail simply STOPPED
    after `step_started`, so nothing downstream could tell an interrupted
    run from one still in flight."""
    started = _arm()
    repos = in_memory_repositories()
    engine = _engine(repos)

    task = asyncio.create_task(engine.run(_definition(), trigger_payload={}))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    actions = [e.action for e in await repos.audit.list_recent(limit=20)]
    assert "workflow_interrupted" in actions, (
        f"no terminal audit entry for a cancelled run (saw {actions}); the trail "
        "stops mid-run, which is what made the live orphans invisible"
    )


async def test_the_cancellation_still_propagates() -> None:
    """Marking the instance must not SWALLOW the cancellation — shutdown has
    to proceed, and no caller may mistake an interrupted run for a finished
    one. `pytest.raises(CancelledError)` above already asserts this; stated
    separately because "handle it" and "absorb it" are one keyword apart.
    """
    started = _arm()
    repos = in_memory_repositories()
    engine = _engine(repos)

    task = asyncio.create_task(engine.run(_definition(), trigger_payload={}))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() or task.exception() is not None


async def test_the_interrupted_step_is_CANCELLED_so_a_resume_re_runs_it() -> None:
    """PAUSED is only useful if resuming does the right thing. CANCELLED is
    deliberately not in `already_done`, so the interrupted step re-runs in
    full (EXECUTION_SEMANTICS §7) rather than being skipped as done."""
    started = _arm()
    repos = in_memory_repositories()
    engine = _engine(repos)

    task = asyncio.create_task(engine.run(_definition(), trigger_payload={}))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    instances = await repos.instances.list_recent(limit=10)
    steps = await repos.steps.list_by_instance(instances[0].id)
    assert [s.state for s in steps] == [StepExecutionState.CANCELLED]


# --- half 2: the boot sweep ------------------------------------------------


async def _stranded(repos: Any, *, with_running_step: bool = True) -> WorkflowInstance:
    instance: WorkflowInstance = await repos.instances.create(
        WorkflowInstance(workflow_id="wf", state=WorkflowInstanceState.RUNNING)
    )
    if with_running_step:
        await repos.steps.create(
            StepExecution(
                instance_id=instance.id,
                step_id="slow",
                attempt=1,
                state=StepExecutionState.RUNNING,
            )
        )
    return instance


async def test_the_boot_sweep_recovers_what_a_hard_kill_left_behind() -> None:
    """The in-process handler cannot run when the process does not get to
    run anything. At boot nothing is executing, so a row still marked
    RUNNING is not running."""
    repos = in_memory_repositories()
    instance = await _stranded(repos)

    recovered = await sweep_interrupted_instances(repos, AuditWriter(repos))
    assert recovered == 1

    fresh = await repos.instances.get(instance.id)
    assert fresh is not None
    assert fresh.state is WorkflowInstanceState.PAUSED
    assert "interrupted" in (fresh.error or "")
    steps = await repos.steps.list_by_instance(instance.id)
    assert [s.state for s in steps] == [StepExecutionState.CANCELLED], (
        "an instance PAUSED while its step row still says RUNNING is the same lie "
        "in a smaller place"
    )
    assert "workflow_interrupted" in [e.action for e in await repos.audit.list_recent(limit=10)]


async def test_the_boot_sweep_leaves_terminal_instances_alone() -> None:
    """It recovers RUNNING and nothing else. A COMPLETED run is finished, a
    PAUSED one is already in the state the sweep produces, and rewriting
    either would be the sweep inventing history."""
    repos = in_memory_repositories()
    keep = []
    for state in (
        WorkflowInstanceState.COMPLETED,
        WorkflowInstanceState.FAILED,
        WorkflowInstanceState.KILLED,
        WorkflowInstanceState.PAUSED,
        WorkflowInstanceState.PENDING,
    ):
        keep.append(await repos.instances.create(WorkflowInstance(workflow_id="wf", state=state)))

    assert await sweep_interrupted_instances(repos, AuditWriter(repos)) == 0
    for instance in keep:
        fresh = await repos.instances.get(instance.id)
        assert fresh is not None and fresh.state is instance.state


async def test_the_boot_sweep_continues_past_one_bad_row() -> None:
    """It runs in the startup path. One unrecoverable instance must not
    cost the others their recovery, nor stop the service booting."""
    repos = in_memory_repositories()
    good = await _stranded(repos)
    bad = await _stranded(repos)

    original = repos.instances.update

    async def _explode(instance: WorkflowInstance) -> WorkflowInstance:
        if instance.id == bad.id:
            raise RuntimeError("SYNTHETIC persistence failure")
        return await original(instance)

    repos.instances.update = _explode  # type: ignore[method-assign]
    recovered = await sweep_interrupted_instances(repos, AuditWriter(repos))
    repos.instances.update = original  # type: ignore[method-assign]

    assert recovered == 1
    fresh = await repos.instances.get(good.id)
    assert fresh is not None and fresh.state is WorkflowInstanceState.PAUSED


async def test_the_boot_sweep_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The single-process assumption is load-bearing, so an operator who
    reaches a multi-process deployment before leases (G21) has a way to
    stop the sweep claiming a peer's in-flight rows."""
    repos = in_memory_repositories()
    instance = await _stranded(repos)
    monkeypatch.setenv(DISABLE_ENV_VAR, "1")

    assert await sweep_interrupted_instances(repos, AuditWriter(repos)) == 0
    fresh = await repos.instances.get(instance.id)
    assert fresh is not None and fresh.state is WorkflowInstanceState.RUNNING


async def test_list_by_state_finds_the_oldest_rows_not_just_recent_ones() -> None:
    """Why the sweep needed its own query. `list_recent` is time-ordered
    over ALL instances, and on the live box the oldest stranded row
    predated the newest by two months — reaching it that way would mean an
    unbounded scan."""
    from datetime import UTC, datetime, timedelta

    repos = in_memory_repositories()
    old = await repos.instances.create(
        WorkflowInstance(
            workflow_id="wf",
            state=WorkflowInstanceState.RUNNING,
            created_at=datetime.now(UTC) - timedelta(days=60),
        )
    )
    for _ in range(20):
        await repos.instances.create(
            WorkflowInstance(workflow_id="wf", state=WorkflowInstanceState.COMPLETED)
        )

    found = await repos.instances.list_by_state([WorkflowInstanceState.RUNNING.value], limit=5)
    assert [i.id for i in found] == [old.id]
