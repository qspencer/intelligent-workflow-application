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


async def decide_raw_release(
    repositories: Repositories,
    *,
    raw_ok: bool,
    surface: str,
    actor_id: str,
    instance_id: str | None,
    kinds: Sequence[str],
) -> tuple[bool, str | None]:
    """Decide whether raw may be released, emitting the attempt+release pair.

    Returns `(released, redaction_reason)`. `released` is the flag the caller
    uses to project (False) or include raw (True). When `raw_ok` is False the
    caller is below-grant — nothing is released and NO audit is emitted (an
    ordinary projected read is not a raw-access event). When `raw_ok` is True
    but an audit append fails, returns `(False, ACCESS_AUDIT_UNAVAILABLE)`.
    """
    if not raw_ok:
        return False, None
    request_id = uuid.uuid4().hex
    detail = {"request_id": request_id, "surface": surface, "intended_kinds": sorted(kinds)}
    attempted = await _append(
        repositories,
        AuditEntry(
            actor_type="human",
            actor_id=actor_id,
            action="raw_trace_access_attempted",
            workflow_instance_id=instance_id,
            detail=detail,
        ),
    )
    if not attempted:
        return False, ACCESS_AUDIT_UNAVAILABLE
    released = await _append(
        repositories,
        AuditEntry(
            actor_type="human",
            actor_id=actor_id,
            action="raw_trace_release_decided",
            workflow_instance_id=instance_id,
            detail={**detail, "outcome": "released", "released_kinds": sorted(kinds)},
        ),
    )
    if not released:
        return False, ACCESS_AUDIT_UNAVAILABLE
    return True, None
