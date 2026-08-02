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

from dataclasses import dataclass, field
from typing import Any

from workflow_platform.persistence import Repositories
from workflow_platform.trace_projection import (
    REDACTED_ERROR,
    redact_tool_data,
    safe_trigger_payload,
)
from workflow_platform.trace_vault import RawTraceVault

# Scan ceiling. Exceeding it is NOT silently ignored (external code review
# 2026-08-02 F10): `ZeroRawReport.capped` is set and the gate must NOT certify.
_SCAN_LIMIT = 200_000


@dataclass(frozen=True)
class RawFinding:
    table: str
    row_id: str
    column: str


@dataclass(frozen=True)
class ZeroRawReport:
    """The release gate's result (F10). `clean` requires BOTH no findings AND
    an EXHAUSTIVE scan — a capped scan can't certify what it didn't read."""

    findings: list[RawFinding] = field(default_factory=list)
    scanned: int = 0
    capped: bool = False

    @property
    def clean(self) -> bool:
        return not self.findings and not self.capped

    @property
    def audit_findings(self) -> list[RawFinding]:
        """Pre-flip `audit_log` raw — append-only, so the backfill does NOT
        rewrite it; it is reported here so the gate fails until it is encrypted
        or migrated (read-protection alone is not DB-operator resistance)."""
        return [f for f in self.findings if f.table == "audit_log"]


def _has_raw(record: Any) -> bool:
    """A record still carries raw iff projecting it changes it (the default-deny
    projection is a fixed point on already-safe data)."""
    return bool(redact_tool_data(record, admin=False) != record)


def _trigger_has_raw(trigger_payload: dict[str, Any]) -> bool:
    return safe_trigger_payload(trigger_payload) != trigger_payload


def _error_has_raw(error: str | None) -> bool:
    """Error text is raw unless it is already the redaction marker (F2/F10)."""
    return bool(error) and not error.startswith("[redacted")  # type: ignore[union-attr]


async def verify_zero_raw(repositories: Repositories, *, limit: int = _SCAN_LIMIT) -> ZeroRawReport:
    """Scan ALL raw-bearing operational columns — trigger, context, step output,
    instance + step ERROR (F2), and audit detail — for any raw that should live
    only in the vault. `ZeroRawReport.clean` is the flip's criterion-14 gate: it
    fails on ANY finding AND on a capped (non-exhaustive) scan (F10)."""
    findings: list[RawFinding] = []
    instances = await repositories.instances.list_recent(limit=limit)
    for inst in instances:
        if inst.trigger_payload and _trigger_has_raw(inst.trigger_payload):
            findings.append(RawFinding("workflow_instances", inst.id, "trigger_payload"))
        if inst.context and _has_raw(inst.context):
            findings.append(RawFinding("workflow_instances", inst.id, "context"))
        if _error_has_raw(inst.error):
            findings.append(RawFinding("workflow_instances", inst.id, "error"))
        for step in await repositories.steps.list_by_instance(inst.id):
            if step.output and _has_raw(step.output):
                findings.append(RawFinding("step_executions", step.id, "output"))
            if _error_has_raw(step.error):
                findings.append(RawFinding("step_executions", step.id, "error"))
        for entry in await repositories.audit.list_by_instance(inst.id):
            if entry.detail and _has_raw(entry.detail):
                findings.append(RawFinding("audit_log", entry.id, "detail"))
    return ZeroRawReport(findings=findings, scanned=len(instances), capped=len(instances) >= limit)


async def find_raw_in_operational_store(
    repositories: Repositories, *, limit: int = _SCAN_LIMIT
) -> list[RawFinding]:
    """Back-compat thin wrapper over `verify_zero_raw` (returns just findings).
    Prefer `verify_zero_raw` so the caller sees the `capped` exhaustiveness
    signal — findings alone can't tell a clean store from a truncated scan."""
    return (await verify_zero_raw(repositories, limit=limit)).findings


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

    # Trigger + context + error on the instance.
    if inst.trigger_payload and _trigger_has_raw(inst.trigger_payload):
        await vault.record_trigger(
            org_id=inst.org_id, instance_id=inst.id, payload=inst.trigger_payload, durable=True
        )
        written += 1
        inst.trigger_payload = safe_trigger_payload(inst.trigger_payload)
    if inst.context:
        inst.context = redact_tool_data(inst.context, admin=False)
    if _error_has_raw(inst.error):
        await vault.record_error(
            org_id=inst.org_id,
            instance_id=inst.id,
            step_attempt_id=None,
            error=inst.error,
            durable=True,
        )
        inst.error = REDACTED_ERROR
        written += 1
    await repositories.instances.update(inst)

    # Each step's raw output + error.
    for step in await repositories.steps.list_by_instance(inst.id):
        changed = False
        if step.output and _has_raw(step.output):
            await vault.record_step_output(
                org_id=inst.org_id,
                instance_id=inst.id,
                step_attempt_id=step.id,
                output=step.output,
                durable=True,
            )
            step.output = redact_tool_data(step.output, admin=False)
            written += 1
            changed = True
        if _error_has_raw(step.error):
            await vault.record_error(
                org_id=inst.org_id,
                instance_id=inst.id,
                step_attempt_id=step.id,
                error=step.error,
                durable=True,
            )
            step.error = REDACTED_ERROR
            written += 1
            changed = True
        if changed:
            await repositories.steps.update(step)
    return written


async def backfill_all(repositories: Repositories, *, limit: int = 100_000) -> int:
    """Backfill every instance. Returns total vault objects written."""
    vault = RawTraceVault(repositories)
    total = 0
    for inst in await repositories.instances.list_recent(limit=limit):
        total += await backfill_instance(repositories, vault, inst.id)
    return total
