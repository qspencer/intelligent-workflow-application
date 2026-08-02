"""Backfill + zero-raw verifier (docs/TRACE_GOVERNANCE_PLAN.md §8.6/§8.14,
TG3c): the verifier detects raw left in the operational tables; the backfill
vaults it and projects the rows in place so the verifier then finds none."""

from __future__ import annotations

from workflow_platform.persistence import (
    StepExecution,
    WorkflowInstance,
    in_memory_repositories,
)
from workflow_platform.trace_migration import (
    backfill_all,
    backfill_instance,
    find_raw_in_operational_store,
)
from workflow_platform.trace_vault import RawTraceVault

SECRET_TOOL = "MIGRATE-TOOL-SECRET"
SECRET_TRIG = "MIGRATE-TRIGGER-SECRET"


async def _seed_raw(repos: object) -> str:
    r = repos  # narrow for the calls below
    inst = WorkflowInstance(
        workflow_id="wf",
        org_id="default",
        trigger_payload={"subject": "s", "body": SECRET_TRIG},
    )
    await r.instances.create(inst)  # type: ignore[attr-defined]
    await r.steps.create(  # type: ignore[attr-defined]
        StepExecution(
            instance_id=inst.id,
            step_id="act",
            state="completed",
            output={
                "category": "urgent",  # structured/safe — must survive
                "tool_calls": [{"name": "t", "input": {"body": SECRET_TOOL}, "result": {}}],
                "output_text": f"echoed {SECRET_TOOL}",
            },
        )
    )
    return inst.id


async def test_verifier_detects_raw_then_backfill_clears_it() -> None:
    repos = in_memory_repositories()
    iid = await _seed_raw(repos)

    # Before: the verifier finds raw in the operational store.
    before = await find_raw_in_operational_store(repos)
    tables = {f.column for f in before}
    assert "trigger_payload" in tables and "output" in tables

    # Backfill vaults the raw and projects the rows in place.
    written = await backfill_all(repos)
    assert written >= 2  # trigger + step output

    # After: zero raw in the operational store.
    after = await find_raw_in_operational_store(repos)
    assert after == []

    # The raw is in the vault; the structured field survived the projection.
    vault = await repos.raw_trace_vault.list_by_instance(iid)
    vblob = str([v.payload for v in vault])
    assert SECRET_TOOL in vblob and SECRET_TRIG in vblob
    step = (await repos.steps.list_by_instance(iid))[0]
    assert step.output is not None
    assert step.output["category"] == "urgent"  # safe field preserved
    assert SECRET_TOOL not in str(step.output)


async def test_backfill_is_idempotent() -> None:
    repos = in_memory_repositories()
    iid = await _seed_raw(repos)
    vault = RawTraceVault(repos)
    first = await backfill_instance(repos, vault, iid)
    assert first >= 2
    # re-running finds the rows already safe → nothing new to write
    second = await backfill_instance(repos, vault, iid)
    assert second == 0
    assert await find_raw_in_operational_store(repos) == []
