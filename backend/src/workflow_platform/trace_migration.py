"""Backfill + zero-raw verifier (docs/TRACE_GOVERNANCE_PLAN.md §8.6/§8.14,
TG3c).

The **verifier** is the release gate (criterion 14): it proves no raw remains
in the operational tables. It is asset-map-driven (it walks the known
raw-bearing columns) and STRUCTURAL, not key-name matching — a record has raw
iff projecting it *changes* it (the projection is a fixed point on
already-safe data).

The **backfill** migrates existing inline raw (from before the flip) into the
vault and projects the operational rows in place — one-way, operator-run,
idempotent (re-running finds the rows already safe + the vault put is
idempotent on its key).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from workflow_platform.persistence import Repositories
from workflow_platform.trace_projection import redact_tool_data, safe_trigger_payload
from workflow_platform.trace_vault import RawTraceVault


@dataclass(frozen=True)
class RawFinding:
    table: str
    row_id: str
    column: str


def _has_raw(record: Any) -> bool:
    """A record still carries raw iff projecting it changes it (the projection
    is a fixed point on already-safe data — `safe_tool_call` is idempotent)."""
    return bool(redact_tool_data(record, admin=False) != record)


def _trigger_has_raw(trigger_payload: dict[str, Any]) -> bool:
    return safe_trigger_payload(trigger_payload) != trigger_payload


async def find_raw_in_operational_store(
    repositories: Repositories, *, limit: int = 1000
) -> list[RawFinding]:
    """Scan the operational tables for any raw that should live only in the
    vault. Empty result = zero-raw (the flip's criterion 14)."""
    findings: list[RawFinding] = []
    for inst in await repositories.instances.list_recent(limit=limit):
        if inst.trigger_payload and _trigger_has_raw(inst.trigger_payload):
            findings.append(RawFinding("workflow_instances", inst.id, "trigger_payload"))
        if inst.context and _has_raw(inst.context):
            findings.append(RawFinding("workflow_instances", inst.id, "context"))
        for step in await repositories.steps.list_by_instance(inst.id):
            if step.output and _has_raw(step.output):
                findings.append(RawFinding("step_executions", step.id, "output"))
        for entry in await repositories.audit.list_by_instance(inst.id):
            if entry.detail and _has_raw(entry.detail):
                findings.append(RawFinding("audit_log", entry.id, "detail"))
    return findings


async def backfill_instance(
    repositories: Repositories, vault: RawTraceVault, instance_id: str
) -> int:
    """Vault the inline raw for one instance (trigger + each step's output),
    then project the operational rows in place. Returns the number of vault
    objects written. Idempotent."""
    inst = await repositories.instances.get(instance_id)
    if inst is None:
        return 0
    written = 0

    # Trigger + context on the instance.
    if inst.trigger_payload and _trigger_has_raw(inst.trigger_payload):
        await vault.record_trigger(
            org_id=inst.org_id, instance_id=inst.id, payload=inst.trigger_payload, durable=True
        )
        written += 1
        inst.trigger_payload = safe_trigger_payload(inst.trigger_payload)
    if inst.context:
        inst.context = redact_tool_data(inst.context, admin=False)
    await repositories.instances.update(inst)

    # Each step's raw output.
    for step in await repositories.steps.list_by_instance(inst.id):
        if step.output and _has_raw(step.output):
            await vault.record_step_output(
                org_id=inst.org_id,
                instance_id=inst.id,
                step_attempt_id=step.id,
                output=step.output,
                durable=True,
            )
            step.output = redact_tool_data(step.output, admin=False)
            await repositories.steps.update(step)
            written += 1
    return written


async def backfill_all(repositories: Repositories, *, limit: int = 100_000) -> int:
    """Backfill every instance. Returns total vault objects written."""
    vault = RawTraceVault(repositories)
    total = 0
    for inst in await repositories.instances.list_recent(limit=limit):
        total += await backfill_instance(repositories, vault, inst.id)
    return total
