"""G-Trace-Audit-Rest: moving pre-flip `audit_log.detail` raw into the
vault, with the rewrite itself recorded (option C).

The audit log is append-only BY DISCIPLINE — no chain, no signature, no
digest on the table, and `THREAT_MODEL.md` lists *audit not tamper-evident*
as a known gap. So this migration breaks no verifiable property; it spends
a stated invariant. The ledger is what buys it back, and most of what these
tests pin is the ledger being worth what it claims.
"""

from __future__ import annotations

from typing import Any

import pytest

from workflow_platform.persistence import (
    AuditEntry,
    RawTraceKind,
    WorkflowInstance,
    in_memory_repositories,
)
from workflow_platform.trace_migration import (
    migrate_audit_details,
    verify_zero_raw,
)
from workflow_platform.trace_projection import PROJECTOR_VERSION

RAW_DETAIL = {"error": "SYNTHETIC exception text", "attempt": 1}


async def _seeded(rows: int = 3) -> tuple[Any, Any, list[str]]:
    repos = in_memory_repositories()
    instance = await repos.instances.create(WorkflowInstance(workflow_id="wf", org_id="alpha"))
    ids = []
    for _ in range(rows):
        entry = await repos.audit.append(
            AuditEntry(
                actor_type="engine",
                actor_id="workflow_engine",
                action="step_failed",
                workflow_instance_id=instance.id,
                detail=dict(RAW_DETAIL),
            )
        )
        ids.append(entry.id)
    return repos, instance, ids


async def test_report_only_by_default_and_changes_nothing() -> None:
    """It rewrites production audit rows. The default must be to say what
    it would do."""
    repos, instance, ids = await _seeded()
    report = await migrate_audit_details(repos)

    assert report.dry_run and report.candidates == 3
    assert report.vaulted == 0 and report.projected == 0
    entries = await repos.audit.list_by_instance(instance.id)
    assert all(e.detail == RAW_DETAIL for e in entries if e.id in ids), "a dry run wrote"
    assert not await repos.raw_trace_vault.list_by_instance(instance.id), "a dry run vaulted"


async def test_apply_vaults_the_raw_and_projects_the_row() -> None:
    repos, instance, ids = await _seeded()
    report = await migrate_audit_details(repos, dry_run=False)

    assert (report.vaulted, report.projected) == (3, 3)
    by_id = {e.id: e for e in await repos.audit.list_by_instance(instance.id)}
    for row_id in ids:
        entry = by_id[row_id]
        assert "SYNTHETIC" not in str(entry.detail), "raw survived in the row"
        assert entry.detail["attempt"] == 1, "operational metadata was lost"
        assert entry.projector_version == PROJECTOR_VERSION, "row not stamped"

    vaulted = [
        r
        for r in await repos.raw_trace_vault.list_by_instance(instance.id)
        if r.kind is RawTraceKind.AUDIT_DETAIL
    ]
    assert len(vaulted) == 3
    assert all("SYNTHETIC" in str(r.payload) for r in vaulted), "raw not in the vault"


async def test_the_rewrite_is_itself_audited() -> None:
    """The whole argument for option C. A mutation of the audit log that
    the audit log does not record is indistinguishable from tampering by
    anyone reading it later."""
    repos, _instance, ids = await _seeded()
    await migrate_audit_details(repos, dry_run=False)

    ledger = [
        e for e in await repos.audit.list_recent(limit=50) if e.action == "audit_detail_migrated"
    ]
    assert len(ledger) == 1
    detail = ledger[0].detail
    assert sorted(detail["rows"]) == sorted(ids), "the ledger does not name the rows it rewrote"
    assert detail["row_count"] == 3
    assert detail["projector_version"] == PROJECTOR_VERSION
    assert detail["pre_image_digest"].startswith("sha256:")
    assert "SYNTHETIC" not in str(detail), (
        "the ledger carries the pre-images, which puts the raw straight back into audit_log"
    )


async def test_the_pre_image_digest_is_derivable_from_the_vaulted_raw() -> None:
    """Evidence has to be checkable or it is decoration. A reader who opens
    the vault objects under a grant must be able to re-derive the digest and
    confirm the ledger describes those exact rows with those exact prior
    contents."""
    from workflow_platform.trace_migration import _pre_image_digest

    repos, instance, ids = await _seeded()
    pre_images = {
        e.id: dict(e.detail) for e in await repos.audit.list_by_instance(instance.id) if e.id in ids
    }
    await migrate_audit_details(repos, dry_run=False)

    ledger = next(
        e for e in await repos.audit.list_recent(limit=50) if e.action == "audit_detail_migrated"
    )
    rederived = _pre_image_digest(list(pre_images.items()))
    assert rederived == ledger.detail["pre_image_digest"]


async def test_running_it_twice_migrates_nothing_the_second_time() -> None:
    """Idempotent by construction rather than by bookkeeping: a projected
    row is a fixed point of the projection, so a second pass cannot select
    it."""
    repos, _instance, _ids = await _seeded()
    first = await migrate_audit_details(repos, dry_run=False)
    second = await migrate_audit_details(repos, dry_run=False)

    assert first.projected == 3
    assert (second.candidates, second.projected, second.batches) == (0, 0, 0)
    ledger = [
        e for e in await repos.audit.list_recent(limit=50) if e.action == "audit_detail_migrated"
    ]
    assert len(ledger) == 1, "the second run appended a ledger entry for nothing"


async def test_it_clears_the_audit_findings_from_the_gate() -> None:
    """The point of the exercise: these rows are why `verify_zero_raw`
    cannot certify, and `backfill_all` does not touch them."""
    repos, _instance, _ids = await _seeded()
    assert len((await verify_zero_raw(repos)).audit_findings) == 3
    await migrate_audit_details(repos, dry_run=False)
    assert (await verify_zero_raw(repos)).audit_findings == []


async def test_an_instance_less_row_is_SKIPPED_not_projected() -> None:
    """There is no vault for an instance-less entry, so projecting one
    would destroy raw with nowhere to have put it. Counted and left."""
    repos, _instance, _ids = await _seeded(rows=0)
    orphan = await repos.audit.append(
        AuditEntry(
            actor_type="system",
            actor_id="x",
            action="step_failed",
            detail=dict(RAW_DETAIL),
        )
    )
    report = await migrate_audit_details(repos, dry_run=False)

    assert report.candidates == 0
    fresh = [e for e in await repos.audit.list_recent(limit=20) if e.id == orphan.id]
    assert fresh and fresh[0].detail == RAW_DETAIL, "an unvaultable row was projected anyway"


async def test_the_ledger_detail_is_projection_lossless() -> None:
    """It is instance-less, so it has no vault. A withheld field in it
    would be a deleted field in the evidence for a deletion."""
    from workflow_platform.trace_projection import project_audit_detail_at_rest

    repos, _instance, _ids = await _seeded()
    await migrate_audit_details(repos, dry_run=False)
    ledger = next(
        e for e in await repos.audit.list_recent(limit=50) if e.action == "audit_detail_migrated"
    )
    assert project_audit_detail_at_rest("audit_detail_migrated", ledger.detail) == ledger.detail


async def test_batches_get_their_own_ledger_entries() -> None:
    repos, _instance, _ids = await _seeded(rows=5)
    report = await migrate_audit_details(repos, dry_run=False, batch_size=2)

    assert report.batches == 3
    ledger = [
        e for e in await repos.audit.list_recent(limit=50) if e.action == "audit_detail_migrated"
    ]
    assert sorted(e.detail["batch"] for e in ledger) == [1, 2, 3]
    assert sum(e.detail["row_count"] for e in ledger) == 5


async def test_the_vault_write_happens_BEFORE_the_row_is_projected() -> None:
    """Order is the whole safety property: a row projected before its raw
    is durably stored is a row whose raw is gone. Enforced by making the
    vault write fail and checking the row was left alone."""
    repos, instance, ids = await _seeded(rows=1)

    async def _explode(trace: Any) -> Any:
        raise RuntimeError("SYNTHETIC vault failure")

    repos.raw_trace_vault.put = _explode
    with pytest.raises(Exception, match="SYNTHETIC vault failure"):
        await migrate_audit_details(repos, dry_run=False)

    entry = next(e for e in await repos.audit.list_by_instance(instance.id) if e.id in ids)
    assert entry.detail == RAW_DETAIL, "the row was projected despite the vault write failing"
