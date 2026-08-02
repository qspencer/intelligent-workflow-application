"""TG2 release-boundary audit for raw-trace reads
(docs/TRACE_GOVERNANCE_PLAN.md §3.1).

Every read that RELEASES a raw trace to a grant-holder emits an append-only
pair under one correlation id: `raw_trace_access_attempted` (before any raw
is assembled) then `raw_trace_release_decided` (before any raw byte leaves
the authorization boundary). The audited boundary is RELEASE, not client
receipt — the app can only prove it released bytes to its transport.

Fail-closed: if EITHER append fails, no raw is released — the caller degrades
to the projected view with `redaction_reason: access_audit_unavailable`.
Because both appends are committed before the caller assembles/returns the
raw content, "no raw byte crosses the boundary unless release_decided
committed" holds by construction.

Pre-TG3, raw lives inline and is atomic (there is no vault to partially
fetch), so the only outcomes are `released` and the degradation to
`projected`; `partial` / `retrieval_failed` / `integrity_failed` arrive with
the TG3 vault. `raw_trace_delivery_observed` (best-effort transport
telemetry, never proof of receipt) is likewise deferred to when it can carry
real signal.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from workflow_platform.persistence import AuditEntry, Repositories

logger = logging.getLogger(__name__)

# Read surfaces (the audited event's `surface`).
SURFACE_DETAIL = "detail"
SURFACE_EXPLAIN = "explain"
SURFACE_AUDIT = "audit"
SURFACE_WS = "ws"

ACCESS_AUDIT_UNAVAILABLE = "access_audit_unavailable"


async def _append(repositories: Repositories, entry: AuditEntry) -> bool:
    try:
        await repositories.audit.append(entry)
        return True
    except Exception:
        # Any persistence failure fails closed (caller degrades to projected).
        logger.warning("raw-trace access audit append failed; degrading", exc_info=True)
        return False


async def begin_raw_release(
    repositories: Repositories,
    *,
    raw_ok: bool,
    surface: str,
    actor_id: str,
    instance_id: str | None,
    kinds: Sequence[str],
) -> tuple[str | None, str | None]:
    """Phase 1 (external code review 2026-08-02 F8): commit
    `raw_trace_access_attempted` BEFORE any vault fetch. Returns
    `(request_id, redaction_reason)`. `request_id is None` with reason None →
    below-grant (project, no audit); with `ACCESS_AUDIT_UNAVAILABLE` → the
    attempt audit failed, degrade to projected. A non-None request_id must be
    finished with `commit_raw_release` once the actual outcome is known."""
    if not raw_ok:
        return None, None
    request_id = uuid.uuid4().hex
    ok = await _append(
        repositories,
        AuditEntry(
            actor_type="human",
            actor_id=actor_id,
            action="raw_trace_access_attempted",
            workflow_instance_id=instance_id,
            detail={"request_id": request_id, "surface": surface, "intended_kinds": sorted(kinds)},
        ),
    )
    return (request_id, None) if ok else (None, ACCESS_AUDIT_UNAVAILABLE)


async def commit_raw_release(
    repositories: Repositories,
    *,
    request_id: str,
    surface: str,
    actor_id: str,
    instance_id: str | None,
    returned_kinds: Sequence[str],
    withheld_kinds: Sequence[str],
) -> tuple[bool, str | None]:
    """Phase 2 (F8): commit `raw_trace_release_decided` with the ACTUAL outcome,
    determined AFTER the fetch — `released` (all present), `partial` (some
    missing), or `retrieval_failed` (none) — and the exact returned/withheld
    kind lists. Returns `(audit_ok, redaction_reason)`; `audit_ok False` fails
    closed, `redaction_reason` is None only when everything was released."""
    if withheld_kinds and returned_kinds:
        outcome = "partial"
    elif withheld_kinds:
        outcome = "retrieval_failed"
    else:
        outcome = "released"
    ok = await _append(
        repositories,
        AuditEntry(
            actor_type="human",
            actor_id=actor_id,
            action="raw_trace_release_decided",
            workflow_instance_id=instance_id,
            detail={
                "request_id": request_id,
                "surface": surface,
                "outcome": outcome,
                "released_kinds": sorted(returned_kinds),
                "withheld_kinds": sorted(withheld_kinds),
            },
        ),
    )
    if not ok:
        return False, ACCESS_AUDIT_UNAVAILABLE
    return True, (None if outcome == "released" else outcome)


async def decide_raw_release(
    repositories: Repositories,
    *,
    raw_ok: bool,
    surface: str,
    actor_id: str,
    instance_id: str | None,
    kinds: Sequence[str],
) -> tuple[bool, str | None]:
    """Atomic release for surfaces whose raw is inline and COMPLETE (WS frames,
    the audit endpoint) — begin + commit(all-returned) in one call. Vault-fetch
    surfaces (instance detail, explain) use begin/commit directly so the
    decided outcome reflects what was actually retrieved (F8)."""
    request_id, reason = await begin_raw_release(
        repositories,
        raw_ok=raw_ok,
        surface=surface,
        actor_id=actor_id,
        instance_id=instance_id,
        kinds=kinds,
    )
    if request_id is None:
        return False, reason
    ok, reason = await commit_raw_release(
        repositories,
        request_id=request_id,
        surface=surface,
        actor_id=actor_id,
        instance_id=instance_id,
        returned_kinds=kinds,
        withheld_kinds=(),
    )
    return ok, reason
