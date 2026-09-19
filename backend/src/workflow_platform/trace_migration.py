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

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from workflow_platform.persistence import AuditEntry, RawTraceKind, Repositories
from workflow_platform.trace_projection import (
    PROJECTION_SCHEMA_VERSION,
    PROJECTOR_VERSION,
    REDACTED_ERROR,
    is_generated_marker,
    project_audit_detail_at_rest,
    redact_tool_data,
    safe_trigger_payload,
)
from workflow_platform.trace_vault import (
    RawTraceVault,
    audit_detail_has_raw,
    idempotency_key,
)

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


def _has_raw(record: Any, kind: str) -> bool:
    """A record still carries raw iff projecting it changes it (the default-deny
    projection is a fixed point on already-safe data)."""
    return bool(redact_tool_data(record, admin=False, kind=kind) != record)


def _audit_has_raw(detail: Any, action: str | None) -> bool:
    """Audit details are ACTION-scoped, not a single asset kind — a `tool_call`
    detail is a tool-call record, an `escalation_requested` detail is
    model-authored, the rest are engine operational metadata. The verifier must
    dispatch on action, or it both misses raw and false-flags correctly-projected
    tool-call / operational rows (G-Trace-Review-4 F4).

    The two questions this and `trace_vault.audit_detail_has_raw` ask — "does
    this STORED row still hold raw?" and "would the final policy remove
    anything?" — differed only while at-rest was the lenient denylist. The
    2026-09-18 tightening made at-rest BE the final policy, so they converged
    and this delegates rather than keeping a second implementation alive.
    `test_the_verifier_and_the_vaulting_predicate_are_one_question` pins it."""
    return audit_detail_has_raw(action, detail)


def _trigger_has_raw(trigger_payload: dict[str, Any]) -> bool:
    return safe_trigger_payload(trigger_payload) != trigger_payload


def _error_has_raw(error: str | None) -> bool:
    """Error text is raw unless it is EXACTLY a generated marker. Prefix-matching
    `"[redacted"` made a forged `"[redacted victim@example.com]"` read as safe, so
    verify_zero_raw certified it and backfill left it (GR4-r2 F4 — the same
    marker-as-input bug F2 removed from the projector). Uses the single shared
    predicate."""
    return bool(error) and not is_generated_marker(error)


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
        if inst.context and _has_raw(inst.context, "context"):
            findings.append(RawFinding("workflow_instances", inst.id, "context"))
        if _error_has_raw(inst.error):
            findings.append(RawFinding("workflow_instances", inst.id, "error"))
        for step in await repositories.steps.list_by_instance(inst.id):
            if step.output and _has_raw(step.output, "step_output"):
                findings.append(RawFinding("step_executions", step.id, "output"))
            if _error_has_raw(step.error):
                findings.append(RawFinding("step_executions", step.id, "error"))
        for entry in await repositories.audit.list_by_instance(inst.id):
            if entry.detail and _audit_has_raw(entry.detail, entry.action):
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
    """Vault the PRE-FLIP inline raw for one instance (trigger + each step's
    output), then project the operational rows in place. Returns the number of
    vault objects written.

    IDEMPOTENT via the projection stamp: a row already carrying
    `projector_version` has been through the flip write path — its raw is ALREADY
    in the vault and its operational form is the projection. Re-vaulting it would
    put the PROJECTED form under the same immutable key as the real raw and the
    vault would reject it (`VaultConflict`, exists-with-different-content). So
    stamped rows are skipped entirely; backfill only migrates unstamped (pre-flip)
    inline raw. Found by the rehearsal against a copy — this aborted mid-run
    before the guard existed."""
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
        inst.context = redact_tool_data(inst.context, admin=False, kind="context")
        # F3: projected rows MUST be stamped, or rehydrate treats them as
        # never-projected and the zero-raw verifier certifies a store whose rows
        # rehydrate to markers.
        inst.projector_version = PROJECTOR_VERSION
        inst.projection_schema_version = PROJECTION_SCHEMA_VERSION
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
        # A row already carrying the stamp has been through the flip write path —
        # its raw is vaulted and its `output` is the projection; leave it.
        if step.projector_version is not None:
            continue
        changed = False
        if step.output:
            # Is this step-attempt's OUTPUT already in the vault? (GR4-r2 F5: the
            # F3 bug ran live, so prod has rows VAULTED-BUT-UNSTAMPED — raw safe
            # in the vault, operational row never stamped, so rehydrate skips the
            # vault and a grant-holder gets only the marker.)
            key = idempotency_key(inst.org_id, inst.id, step.id, RawTraceKind.OUTPUT)
            already_vaulted = await repositories.raw_trace_vault.get_by_idempotency_key(key)
            if already_vaulted is not None:
                # REPAIR: re-project to the current form + stamp so rehydrate uses
                # the vault. Do NOT re-vault — the raw is already there and the
                # operational output is a projection (VaultConflict otherwise).
                step.output = redact_tool_data(step.output, admin=False, kind="step_output")
                step.projector_version = PROJECTOR_VERSION
                step.projection_schema_version = PROJECTION_SCHEMA_VERSION
                changed = True
            elif _has_raw(step.output, "step_output"):
                # Pre-flip inline raw with no vault object: vault + project + stamp.
                await vault.record_step_output(
                    org_id=inst.org_id,
                    instance_id=inst.id,
                    step_attempt_id=step.id,
                    output=step.output,
                    durable=True,
                )
                step.output = redact_tool_data(step.output, admin=False, kind="step_output")
                step.projector_version = PROJECTOR_VERSION  # F3: project + stamp together
                step.projection_schema_version = PROJECTION_SCHEMA_VERSION
                written += 1
                changed = True
            # else: already-safe output with no vault object (raw long gone) —
            # leave unstamped; rehydrate returns the safe operational form and the
            # verifier already reports it clean. Stamping would point rehydrate at
            # a vault object that does not exist.
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


#: Rows per ledger entry. Bounded so one entry stays readable and so a
#: resumed run is visible at batch granularity rather than all-or-nothing.
AUDIT_MIGRATION_BATCH = 200


@dataclass(frozen=True)
class AuditMigrationReport:
    """What one `migrate_audit_details` pass did, or would do."""

    dry_run: bool
    candidates: int
    vaulted: int
    projected: int
    skipped_no_instance: int
    skipped_no_org: int
    batches: int


def _pre_image_digest(pairs: list[tuple[str, Any]]) -> str:
    """One digest over the batch's (row id, pre-image) pairs.

    Evidence that a later reader can check WITHOUT the pre-images being
    stored anywhere readable: if the vault objects are opened under a
    grant, re-deriving this digest proves the ledger describes those exact
    rows with those exact prior contents. Storing the pre-images in the
    ledger instead would put the raw straight back into `audit_log`, which
    is the thing being removed.
    """
    canonical = json.dumps(
        [[rid, detail] for rid, detail in sorted(pairs)], sort_keys=True, default=str
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


async def migrate_audit_details(
    repositories: Repositories,
    *,
    dry_run: bool = True,
    limit: int = _SCAN_LIMIT,
    batch_size: int = AUDIT_MIGRATION_BATCH,
) -> AuditMigrationReport:
    """Move pre-flip `audit_log.detail` raw into the vault and project the
    row in place — `G-Trace-Audit-Rest`, option C.

    WHY THIS IS NOT `backfill_all`. That walks INSTANCES and rewrites their
    step/trigger/error columns; audit rows are deliberately outside it,
    because the audit log is append-only. This is the deliberate exception,
    and it is a separate entry point so nobody reaches it by accident.

    WHAT MAKES THE EXCEPTION PAYABLE. Append-only here is a discipline, not
    a construction — no chain, no signature, no digest on the table
    (`THREAT_MODEL.md`: *audit not tamper-evident*). So the rewrite breaks
    no verifiable property, and the cost is the invariant itself. The
    ledger is what buys it back: every batch appends an
    `audit_detail_migrated` entry naming the rows it rewrote, the projector
    version applied, and a digest of their pre-images. The one mutation the
    audit log has ever taken is therefore itself audited.

    ORDER, and it is the same rule the engine's `_audit` follows: vault the
    raw DURABLY first, project second. A row projected before its raw is
    safely stored is a row whose raw is gone.

    IDEMPOTENT by construction rather than by bookkeeping: a projected row
    is a fixed point of the projection, so a second pass does not select
    it. `dry_run=True` is the default — this rewrites production.
    """
    vault = RawTraceVault(repositories)
    candidates: list[tuple[AuditEntry, str]] = []  # (entry, org_id)
    no_instance = 0
    no_org = 0

    for inst in await repositories.instances.list_recent(limit=limit):
        for entry in await repositories.audit.list_by_instance(inst.id):
            if not entry.detail or not _audit_has_raw(entry.detail, entry.action):
                continue
            if entry.workflow_instance_id is None:
                # Cannot be vaulted: the vault is instance-scoped. Counted
                # and left alone rather than projected, because projecting
                # would destroy the raw with nowhere to have put it.
                no_instance += 1
                continue
            if not inst.org_id:
                no_org += 1
                continue
            candidates.append((entry, inst.org_id))

    if dry_run:
        return AuditMigrationReport(
            dry_run=True,
            candidates=len(candidates),
            vaulted=0,
            projected=0,
            skipped_no_instance=no_instance,
            skipped_no_org=no_org,
            batches=(len(candidates) + batch_size - 1) // max(batch_size, 1),
        )

    vaulted = projected = batches = 0
    for start in range(0, len(candidates), max(batch_size, 1)):
        batch = candidates[start : start + batch_size]
        pairs: list[tuple[str, Any]] = []
        done: list[str] = []
        for entry, org_id in batch:
            assert entry.workflow_instance_id is not None  # filtered above
            # DURABLE: a lost vault write must fail the migration, not
            # silently precede a projection that destroys the raw.
            await vault.record_audit_detail(
                org_id=org_id,
                instance_id=entry.workflow_instance_id,
                audit_entry_id=entry.id,
                action=entry.action,
                detail=entry.detail,
                durable=True,
            )
            vaulted += 1
            pairs.append((entry.id, entry.detail))
            safe = project_audit_detail_at_rest(entry.action, entry.detail)
            if await repositories.audit.replace_detail_for_migration(
                entry.id, safe, PROJECTOR_VERSION
            ):
                projected += 1
                done.append(entry.id)
        batches += 1
        # THE LEDGER. Appended AFTER the batch, so an interrupted run
        # leaves vaulted-and-projected rows with no entry rather than an
        # entry claiming rows it never touched.
        await repositories.audit.append(
            AuditEntry(
                actor_type="system",
                actor_id="audit_rest_migration",
                action="audit_detail_migrated",
                detail={
                    "rows": done,
                    "row_count": len(done),
                    "batch": batches,
                    "projector_version": PROJECTOR_VERSION,
                    "pre_image_digest": _pre_image_digest(pairs),
                },
            )
        )
    return AuditMigrationReport(
        dry_run=False,
        candidates=len(candidates),
        vaulted=vaulted,
        projected=projected,
        skipped_no_instance=no_instance,
        skipped_no_org=no_org,
        batches=batches,
    )


async def backfill_all(repositories: Repositories, *, limit: int = 100_000) -> int:
    """Backfill every instance. Returns total vault objects written."""
    vault = RawTraceVault(repositories)
    total = 0
    for inst in await repositories.instances.list_recent(limit=limit):
        total += await backfill_instance(repositories, vault, inst.id)
    return total
