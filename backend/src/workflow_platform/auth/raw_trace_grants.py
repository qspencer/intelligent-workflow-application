"""Raw-trace read-grant lifecycle (docs/TRACE_GOVERNANCE_PLAN.md §2/§2.1, TG1).

The grant is a state machine, distinct from ordinary administration and
default-off for everyone including Administrators. This service owns the
transitions; role-gating (only Administrators may call) lives at the API.
Actor ids are the raw `sub` string, matching audit `actor_id`.

Legal transitions: pending → {active, rejected, cancelled};
active → {revoked, expired}.

Note on atomicity: activation performs a check-then-set at the service
level; the migration's partial unique index on active org-scoped grants is
the DB backstop against a duplicate active org grant. A conditional
(compare-and-set) UPDATE is the production hardening for the platform-wide
NULL-scope case — logged here rather than silently assumed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from workflow_platform.persistence import RawTraceGrant, Repositories
from workflow_platform.persistence.models import (
    AuditEntry,
    RawTraceApprovalMode,
    RawTraceGrantState,
    RawTraceReasonCode,
)


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
    """A tenant_authorized grant needs an external approval reference."""


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
    def __init__(self, repositories: Repositories) -> None:
        self._repos = repositories

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
        if not is_platform:
            await self._clear_stale_active(principal_id, org_id, now, requested_by)
            grant.state = RawTraceGrantState.ACTIVE
            grant.approved_by = requested_by
            grant.approved_at = now
            await self._repos.raw_trace_grants.create(grant)
            await self._audit(requested_by, "raw_grant_granted", grant, {"org_scoped": True})
            return grant
        if (
            approval_mode is RawTraceApprovalMode.TENANT_AUTHORIZED
            and external_approval_ref is None
        ):
            raise MissingApprovalArtifact("tenant_authorized requires an external approval ref")
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
        """Second authorization for a PENDING platform-wide grant. Recipient
        excluded from both roles; dual_administrator requires an approver
        distinct from the requester (§2.1)."""
        now = now or datetime.now(UTC)
        grant = await self._repos.raw_trace_grants.get(grant_id)
        if grant is None:
            raise GrantNotFound(grant_id)
        if grant.state is not RawTraceGrantState.PENDING:
            raise InvalidGrantTransition(f"grant {grant_id} is {grant.state.value}, not pending")
        if approved_by == grant.principal_id:
            raise SelfEscalation("the recipient cannot approve their own grant")
        if grant.approval_mode is RawTraceApprovalMode.DUAL_ADMINISTRATOR:
            if approved_by == grant.requested_by:
                raise ApproverConflict("approver must be distinct from the requester")
            grant.approved_by = approved_by
        else:  # tenant_authorized
            ref = external_approval_ref or grant.external_approval_ref
            if ref is None:
                raise MissingApprovalArtifact("tenant_authorized requires an external approval ref")
            grant.external_approval_ref = ref
            grant.approved_by = None
        await self._clear_stale_active(grant.principal_id, grant.org_id, now, approved_by)
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
