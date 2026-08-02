"""Raw-trace read-grant lifecycle (docs/TRACE_GOVERNANCE_PLAN.md §2/§2.1, TG1).

The grant is a state machine, distinct from ordinary administration and
default-off for everyone including Administrators. This service owns the
transitions; role-gating (only Administrators may call) lives at the API.
Actor ids are the raw `sub` string, matching audit `actor_id`.

Legal transitions: pending → {active, rejected, cancelled};
active → {revoked, expired}.

Atomicity (external code review 2026-08-02 F5): the duplicate-check → set-active
critical section runs under an in-process `asyncio.Lock` (with a re-read of the
grant state under the lock), and migration 0009's EXPRESSION unique index over
`COALESCE(org_id, '__platform_wide__')` is the cross-process backstop — covering
the platform-wide NULL-scope case the old partial index missed.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime

from workflow_platform.persistence import RawTraceGrant, Repositories
from workflow_platform.persistence.models import (
    AuditEntry,
    RawTraceApprovalMode,
    RawTraceGrantState,
    RawTraceReasonCode,
)

# An approval reference is an OPAQUE token (a ticket/authorization id), never
# free-form text — external code review 2026-08-02: an arbitrary string is a
# route for customer content into operational storage AND (with the F4 fix) is
# no longer the sole activation gate. Bounded charset + length.
_APPROVAL_REF_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")


class GrantError(ValueError):
    """Base for grant-lifecycle rejections (the API maps these to 4xx)."""


class SelfEscalation(GrantError):
    """A principal cannot request or approve their own grant."""


class ApproverConflict(GrantError):
    """Approver is not distinct from the requester (dual_administrator)."""


class DuplicateActiveGrant(GrantError):
    """A live active grant for the same (principal, scope) already exists."""


class InvalidGrantTransition(GrantError):
    """The grant is not in a state this operation can act on."""


class MissingExpiry(GrantError):
    """A platform-wide grant must carry an expiry."""


class MissingApprovalArtifact(GrantError):
    """A tenant_authorized grant needs a valid external approval reference."""


def _validate_approval_ref(ref: str | None) -> str:
    """A tenant approval reference must be present and an opaque token (F4 +
    review nit). Rejects None/empty and any free-form string."""
    if ref is None or not ref.strip():
        raise MissingApprovalArtifact("tenant_authorized requires an external approval ref")
    ref = ref.strip()
    if not _APPROVAL_REF_RE.match(ref):
        raise MissingApprovalArtifact(
            "external approval ref must be an opaque token, not free text"
        )
    return ref


class GrantNotFound(GrantError):
    """No grant with that id."""


def covering_grant(
    grants: list[RawTraceGrant], target_org: str | None, now: datetime
) -> RawTraceGrant | None:
    """The first active grant that covers `target_org` (platform-wide covers
    any org; an org-scoped grant covers only its own). Pure — the read path
    uses this and never writes."""
    for g in grants:
        if g.covers(target_org, now):
            return g
    return None


class RawTraceGrantService:
    def __init__(
        self, repositories: Repositories, require_dual_control: bool | None = None
    ) -> None:
        self._repos = repositories
        # Dual-control ENFORCEMENT gate (external-org posture, §2.1): when on,
        # NO raw grant activates on a single Administrator — even an org-scoped
        # grant must be requested by one Admin and approved by a distinct
        # second. Default reads `WORKFLOW_PLATFORM_RAW_GRANT_DUAL_CONTROL`.
        if require_dual_control is None:
            require_dual_control = os.environ.get(
                "WORKFLOW_PLATFORM_RAW_GRANT_DUAL_CONTROL", ""
            ).lower() in ("1", "true", "yes")
        self._dual_control = require_dual_control
        # F5: serialize the activation critical section (check-for-duplicate →
        # set active) so two concurrent activations for the same principal/scope
        # can't both win in-process. The DB expression unique index (migration
        # 0009) is the cross-process backstop, incl. the platform-wide NULL case.
        self._activation_lock = asyncio.Lock()

    async def _audit(
        self,
        actor_id: str,
        action: str,
        grant: RawTraceGrant,
        detail_extra: dict[str, object] | None = None,
    ) -> None:
        detail: dict[str, object] = {
            "grant_id": grant.id,
            "principal_id": grant.principal_id,
            "scope": "platform_wide" if grant.is_platform_wide else grant.org_id,
            "approval_mode": grant.approval_mode.value,
            "reason_code": grant.reason_code.value,
            "state": grant.state.value,
        }
        if detail_extra:
            detail.update(detail_extra)
        await self._repos.audit.append(
            AuditEntry(actor_type="human", actor_id=actor_id, action=action, detail=detail)
        )

    async def _clear_stale_active(
        self, principal_id: str, org_id: str | None, now: datetime, actor_id: str
    ) -> None:
        """Before activating, transition any past-expiry active grant for the
        same (principal, scope) to EXPIRED (§2/F6 — an expired-but-not-
        transitioned row would otherwise block the replacement), and reject a
        still-live duplicate."""
        for g in await self._repos.raw_trace_grants.list_for_principal(principal_id):
            if g.state is RawTraceGrantState.ACTIVE and g.org_id == org_id:
                if g.is_active(now):
                    raise DuplicateActiveGrant(
                        f"principal {principal_id} already has an active grant for this scope"
                    )
                g.state = RawTraceGrantState.EXPIRED
                await self._repos.raw_trace_grants.save(g)
                await self._audit(actor_id, "raw_grant_expired", g)

    async def request(
        self,
        *,
        principal_id: str,
        org_id: str | None,
        requested_by: str,
        reason_code: RawTraceReasonCode,
        approval_mode: RawTraceApprovalMode = RawTraceApprovalMode.DUAL_ADMINISTRATOR,
        expires_at: datetime | None = None,
        ticket_ref: str | None = None,
        external_approval_ref: str | None = None,
        now: datetime | None = None,
    ) -> RawTraceGrant:
        """Request a grant. An ORG-scoped grant activates immediately — the
        distinct issuing Administrator IS the authorization. A PLATFORM-WIDE
        grant stays PENDING until a second, distinct authorization (§2.1)."""
        now = now or datetime.now(UTC)
        if requested_by == principal_id:
            raise SelfEscalation("a grant cannot be requested by its own recipient")
        is_platform = org_id is None
        if is_platform and expires_at is None:
            raise MissingExpiry("platform-wide grants require an expiry")
        grant = RawTraceGrant(
            principal_id=principal_id,
            org_id=org_id,
            approval_mode=approval_mode,
            requested_by=requested_by,
            requested_at=now,
            reason_code=reason_code,
            expires_at=expires_at,
            ticket_ref=ticket_ref,
            external_approval_ref=external_approval_ref,
            state=RawTraceGrantState.PENDING,
        )
        if not is_platform and not self._dual_control:
            # Single-control (default): the distinct issuing Administrator IS
            # the authorization — activate immediately.
            async with self._activation_lock:
                await self._clear_stale_active(principal_id, org_id, now, requested_by)
                grant.state = RawTraceGrantState.ACTIVE
                grant.approved_by = requested_by
                grant.approved_at = now
                await self._repos.raw_trace_grants.create(grant)
            await self._audit(requested_by, "raw_grant_granted", grant, {"org_scoped": True})
            return grant
        # Dual-control enforced (or platform-wide): stays PENDING until a
        # distinct second Administrator approves (§2.1).
        if approval_mode is RawTraceApprovalMode.TENANT_AUTHORIZED:
            grant.external_approval_ref = _validate_approval_ref(external_approval_ref)
        await self._repos.raw_trace_grants.create(grant)
        await self._audit(requested_by, "raw_grant_requested", grant)
        return grant

    async def approve(
        self,
        *,
        grant_id: str,
        approved_by: str,
        external_approval_ref: str | None = None,
        now: datetime | None = None,
    ) -> RawTraceGrant:
        """Second authorization for a PENDING grant. The activator must be a
        DISTINCT administrator — never the recipient, never the requester — for
        BOTH modes (external code review 2026-08-02 F4: an unverified external
        ref is NOT sufficient on its own, so tenant_authorized no longer lets a
        single admin self-activate; the accountable internal approver is always
        recorded). tenant_authorized additionally needs a valid opaque ref."""
        now = now or datetime.now(UTC)
        grant = await self._repos.raw_trace_grants.get(grant_id)
        if grant is None:
            raise GrantNotFound(grant_id)
        if grant.state is not RawTraceGrantState.PENDING:
            raise InvalidGrantTransition(f"grant {grant_id} is {grant.state.value}, not pending")
        if approved_by == grant.principal_id:
            raise SelfEscalation("the recipient cannot approve their own grant")
        if approved_by == grant.requested_by:
            raise ApproverConflict("approver must be distinct from the requester")
        if grant.approval_mode is RawTraceApprovalMode.TENANT_AUTHORIZED:
            grant.external_approval_ref = _validate_approval_ref(
                external_approval_ref or grant.external_approval_ref
            )
        # F5: the duplicate-check + activation is one critical section. Re-read
        # under the lock so a concurrent activation of a sibling grant is seen.
        async with self._activation_lock:
            fresh = await self._repos.raw_trace_grants.get(grant_id)
            if fresh is None or fresh.state is not RawTraceGrantState.PENDING:
                raise InvalidGrantTransition(f"grant {grant_id} is no longer pending")
            await self._clear_stale_active(grant.principal_id, grant.org_id, now, approved_by)
            grant.approved_by = approved_by
            grant.state = RawTraceGrantState.ACTIVE
            grant.approved_at = now
            await self._repos.raw_trace_grants.save(grant)
        await self._audit(approved_by, "raw_grant_granted", grant)
        return grant

    async def revoke(
        self, *, grant_id: str, revoked_by: str, now: datetime | None = None
    ) -> RawTraceGrant:
        """Revoke an ACTIVE grant (or cancel a PENDING one)."""
        now = now or datetime.now(UTC)
        grant = await self._repos.raw_trace_grants.get(grant_id)
        if grant is None:
            raise GrantNotFound(grant_id)
        if grant.state not in (RawTraceGrantState.ACTIVE, RawTraceGrantState.PENDING):
            raise InvalidGrantTransition(f"grant {grant_id} is {grant.state.value}")
        grant.state = (
            RawTraceGrantState.REVOKED
            if grant.state is RawTraceGrantState.ACTIVE
            else RawTraceGrantState.CANCELLED
        )
        grant.revoked_by = revoked_by
        grant.revoked_at = now
        await self._repos.raw_trace_grants.save(grant)
        await self._audit(revoked_by, "raw_grant_revoked", grant)
        return grant

    async def revoke_all_for_principal(
        self, *, principal_id: str, revoked_by: str, reason: str, now: datetime | None = None
    ) -> int:
        """Revoke every live grant for a principal — on deactivation or org
        transfer (§2). Returns the count revoked."""
        now = now or datetime.now(UTC)
        count = 0
        for g in await self._repos.raw_trace_grants.list_for_principal(principal_id):
            if g.state in (RawTraceGrantState.ACTIVE, RawTraceGrantState.PENDING):
                g.state = (
                    RawTraceGrantState.REVOKED
                    if g.state is RawTraceGrantState.ACTIVE
                    else RawTraceGrantState.CANCELLED
                )
                g.revoked_by = revoked_by
                g.revoked_at = now
                await self._repos.raw_trace_grants.save(g)
                await self._audit(revoked_by, "raw_grant_revoked", g, {"reason": reason})
                count += 1
        return count

    async def covering(
        self, *, principal_id: str, target_org: str | None, now: datetime | None = None
    ) -> RawTraceGrant | None:
        """The active grant (if any) letting `principal_id` read raw traces of
        `target_org`. Pure read — used by every raw-trace read surface."""
        now = now or datetime.now(UTC)
        grants = await self._repos.raw_trace_grants.list_for_principal(principal_id)
        return covering_grant(grants, target_org, now)
