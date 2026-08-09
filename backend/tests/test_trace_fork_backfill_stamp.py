"""G-Trace-Review-4 F3 (P1): projection and STAMPING must be one indivisible
write across normal execution, fork, and migration/backfill. A row projected but
left unstamped (`projector_version is None`) makes `rehydrate` treat it as
"never projected" — so it skips the vault and returns only the redaction marker,
even to a grant-holder, even though the raw is sitting in the vault.
"""

from __future__ import annotations

import asyncio
from typing import Any

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.persistence import (
    StepExecution,
    StepExecutionState,
    WorkflowInstance,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.trace_projection import PROJECTOR_VERSION
from workflow_platform.trace_vault import RawTraceVault
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

SECRET = "FORK-STAMP-SENTINEL"


async def _noop(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
    return {"summary": SECRET}


def _engine() -> WorkflowEngine:
    return WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=FunctionRegistry(),
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),  # deterministic-only workflow
        world=mock_world(),
        trace_safe_only=True,  # THE FLIP
    )


_DEF = {
    "id": "wf",
    "name": "wf",
    "trigger": {"type": "manual"},
    "steps": [
        {"id": "a", "type": "deterministic", "function": "noop"},
        {"id": "b", "type": "deterministic", "function": "noop"},
    ],
    "edges": [{"from": "a", "to": "b"}],
}


def test_fork_stamps_the_preserved_step_rows() -> None:
    engine = _engine()
    engine.functions.register("noop", _noop)
    definition = load_definition(_DEF)

    async def go() -> None:
        src = await engine.run(definition, trigger_payload={"body": SECRET})
        forked = await engine.fork(definition, src.id, from_step_id="b")
        steps = await engine.repositories.steps.list_by_instance(forked.id)
        preserved = [s for s in steps if s.step_id == "a"]
        assert preserved, "fork did not preserve step a"
        for s in preserved:
            assert s.projector_version == PROJECTOR_VERSION, (
                f"forked preserved step left UNSTAMPED (projector_version="
                f"{s.projector_version!r}); rehydrate will skip the vault and "
                "return the marker"
            )

    asyncio.run(go())


def test_backfill_stamps_the_rows_it_projects() -> None:
    """The migration/backfill projects inline raw in place; it must stamp too, or
    the zero-raw verifier certifies a store whose rows rehydrate to markers."""
    from workflow_platform.trace_migration import backfill_instance

    repos = in_memory_repositories()

    async def go() -> None:
        inst = await repos.instances.create(
            WorkflowInstance(
                workflow_id="wf", org_id="default", state=WorkflowInstanceState.COMPLETED
            )
        )
        await repos.steps.create(
            StepExecution(
                instance_id=inst.id,
                step_id="a",
                attempt=1,
                state=StepExecutionState.COMPLETED,
                output={"output_text": SECRET},  # inline raw, pre-flip
            )
        )
        await backfill_instance(repos, RawTraceVault(repos), inst.id)
        steps = await repos.steps.list_by_instance(inst.id)
        for s in steps:
            assert s.projector_version == PROJECTOR_VERSION, (
                f"backfill projected step {s.step_id} but left it UNSTAMPED "
                f"(projector_version={s.projector_version!r})"
            )

    asyncio.run(go())


def test_backfill_skips_already_stamped_steps() -> None:
    """G-Trace-Review-4 backfill guard (found by the prod rehearsal): a step
    already carrying `projector_version` has been through the flip write path —
    its raw is in the vault. Backfill must NOT re-vault it (that puts the
    PROJECTED form under the raw's immutable key → VaultConflict). Only unstamped
    rows are migrated."""
    from workflow_platform.trace_migration import backfill_instance
    from workflow_platform.trace_projection import PROJECTOR_VERSION
    from workflow_platform.trace_vault import RawTraceVault

    repos = in_memory_repositories()

    async def go() -> None:
        inst = await repos.instances.create(
            WorkflowInstance(
                workflow_id="wf", org_id="default", state=WorkflowInstanceState.COMPLETED
            )
        )
        # already through the flip: stamped, output is the projection
        await repos.steps.create(
            StepExecution(
                instance_id=inst.id,
                step_id="done",
                attempt=1,
                state=StepExecutionState.COMPLETED,
                output={"output_text": "[redacted]", "model": "claude-haiku-4-5"},
                projector_version=PROJECTOR_VERSION,
            )
        )
        vault = RawTraceVault(repos)
        before = (
            len(await repos.raw_trace_vault.list_by_instance(inst.id))
            if hasattr(repos.raw_trace_vault, "list_by_instance")
            else None
        )
        written = await backfill_instance(repos, vault, inst.id)
        assert written == 0, f"backfill re-processed an already-stamped step (wrote {written})"

    asyncio.run(go())


def test_backfill_repairs_vaulted_but_unstamped_rows() -> None:
    """GR4-r2 F5: the F3 bug ran live, leaving rows VAULTED-but-UNSTAMPED (raw in
    the vault, operational row never stamped → rehydrate skips the vault). Backfill
    must STAMP them (so rehydrate uses the vault) WITHOUT re-vaulting (which would
    collide with the real raw at the immutable key)."""
    from workflow_platform.trace_migration import backfill_instance
    from workflow_platform.trace_projection import PROJECTOR_VERSION
    from workflow_platform.trace_vault import RawTraceVault

    repos = in_memory_repositories()

    async def go() -> None:
        inst = await repos.instances.create(
            WorkflowInstance(
                workflow_id="wf", org_id="default", state=WorkflowInstanceState.COMPLETED
            )
        )
        step = await repos.steps.create(
            StepExecution(
                instance_id=inst.id,
                step_id="s",
                attempt=1,
                state=StepExecutionState.COMPLETED,
                output={"output_text": SECRET},  # projected form left; unstamped
                projector_version=None,
            )
        )
        vault = RawTraceVault(repos)
        # simulate the F3-bug history: raw already vaulted for this attempt
        await vault.record_step_output(
            org_id=inst.org_id,
            instance_id=inst.id,
            step_attempt_id=step.id,
            output={"output_text": SECRET},
            durable=True,
        )
        vault_before = len(await repos.raw_trace_vault.list_by_instance(inst.id))
        await backfill_instance(repos, vault, inst.id)  # must NOT raise VaultConflict
        vault_after = len(await repos.raw_trace_vault.list_by_instance(inst.id))
        repaired = next(s for s in await repos.steps.list_by_instance(inst.id) if s.step_id == "s")
        assert repaired.projector_version == PROJECTOR_VERSION, (
            "vaulted-but-unstamped row not stamped"
        )
        assert vault_after == vault_before, "backfill re-vaulted an already-vaulted row"

    asyncio.run(go())
