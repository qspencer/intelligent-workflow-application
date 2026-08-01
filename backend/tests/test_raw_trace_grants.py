"""Grant lifecycle (docs/TRACE_GOVERNANCE_PLAN.md §2/§2.1, TG1). The grant is
a raw-trace read privilege distinct from ordinary administration: default-off,
scope-constrained, two-person for platform-wide."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from workflow_platform.auth.raw_trace_grants import (
    ApproverConflict,
    DuplicateActiveGrant,
    MissingApprovalArtifact,
    MissingExpiry,
    RawTraceGrantService,
    SelfEscalation,
)
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.persistence.models import (
    RawTraceApprovalMode,
    RawTraceGrantState,
    RawTraceReasonCode,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
ADMIN_A = "admin-a"
ADMIN_B = "admin-b"
PRINCIPAL = "user-p"
DEBUG = RawTraceReasonCode.DEBUGGING


def _service() -> RawTraceGrantService:
    return RawTraceGrantService(in_memory_repositories())


async def test_org_grant_activates_immediately_and_is_scope_constrained() -> None:
    svc = _service()
    grant = await svc.request(
        principal_id=PRINCIPAL, org_id="org1", requested_by=ADMIN_A, reason_code=DEBUG, now=NOW
    )
    assert grant.state is RawTraceGrantState.ACTIVE  # a distinct admin IS the authorization
    # covers its own org, not another — an org-A grant does not travel
    assert await svc.covering(principal_id=PRINCIPAL, target_org="org1", now=NOW) is not None
    assert await svc.covering(principal_id=PRINCIPAL, target_org="org2", now=NOW) is None
    # default-off: an ungranted principal reads nothing
    assert await svc.covering(principal_id="other", target_org="org1", now=NOW) is None


async def test_self_escalation_blocked_at_request_and_approval() -> None:
    svc = _service()
    with pytest.raises(SelfEscalation):
        await svc.request(
            principal_id=PRINCIPAL,
            org_id="org1",
            requested_by=PRINCIPAL,
            reason_code=DEBUG,
            now=NOW,
        )
    pending = await svc.request(
        principal_id=PRINCIPAL,
        org_id=None,
        requested_by=ADMIN_A,
        reason_code=DEBUG,
        expires_at=NOW + timedelta(hours=1),
        now=NOW,
    )
    with pytest.raises(SelfEscalation):
        await svc.approve(grant_id=pending.id, approved_by=PRINCIPAL, now=NOW)


async def test_platform_wide_needs_two_distinct_admins_and_expiry() -> None:
    svc = _service()
    with pytest.raises(MissingExpiry):
        await svc.request(
            principal_id=PRINCIPAL, org_id=None, requested_by=ADMIN_A, reason_code=DEBUG, now=NOW
        )
    pending = await svc.request(
        principal_id=PRINCIPAL,
        org_id=None,
        requested_by=ADMIN_A,
        reason_code=DEBUG,
        expires_at=NOW + timedelta(hours=1),
        now=NOW,
    )
    assert pending.state is RawTraceGrantState.PENDING
    # PENDING covers nothing
    assert await svc.covering(principal_id=PRINCIPAL, target_org="anyorg", now=NOW) is None
    # the requester cannot also approve
    with pytest.raises(ApproverConflict):
        await svc.approve(grant_id=pending.id, approved_by=ADMIN_A, now=NOW)
    active = await svc.approve(grant_id=pending.id, approved_by=ADMIN_B, now=NOW)
    assert active.state is RawTraceGrantState.ACTIVE
    # platform-wide covers ANY org
    assert await svc.covering(principal_id=PRINCIPAL, target_org="org1", now=NOW) is not None
    assert await svc.covering(principal_id=PRINCIPAL, target_org="org2", now=NOW) is not None


async def test_duplicate_active_grant_rejected() -> None:
    svc = _service()
    await svc.request(
        principal_id=PRINCIPAL, org_id="org1", requested_by=ADMIN_A, reason_code=DEBUG, now=NOW
    )
    with pytest.raises(DuplicateActiveGrant):
        await svc.request(
            principal_id=PRINCIPAL, org_id="org1", requested_by=ADMIN_B, reason_code=DEBUG, now=NOW
        )


async def test_expiry_is_authoritative_and_replaceable() -> None:
    svc = _service()
    # an org-scoped grant with an expiry (activates immediately)
    await svc.request(
        principal_id=PRINCIPAL,
        org_id="org1",
        requested_by=ADMIN_A,
        reason_code=DEBUG,
        expires_at=NOW + timedelta(hours=1),
        now=NOW,
    )
    later = NOW + timedelta(hours=2)
    # past expiry: not covering even though the row is still state=active
    assert await svc.covering(principal_id=PRINCIPAL, target_org="org1", now=later) is None
    # a replacement for the SAME (principal, scope) activates and transitions
    # the stale row to EXPIRED, so it doesn't block the replacement (F6)
    replacement = await svc.request(
        principal_id=PRINCIPAL, org_id="org1", requested_by=ADMIN_A, reason_code=DEBUG, now=later
    )
    assert replacement.state is RawTraceGrantState.ACTIVE
    grants = await svc._repos.raw_trace_grants.list_for_principal(PRINCIPAL)
    assert any(g.state is RawTraceGrantState.EXPIRED for g in grants)
    assert await svc.covering(principal_id=PRINCIPAL, target_org="org1", now=later) is not None


async def test_revoke_and_revoke_all() -> None:
    svc = _service()
    grant = await svc.request(
        principal_id=PRINCIPAL, org_id="org1", requested_by=ADMIN_A, reason_code=DEBUG, now=NOW
    )
    await svc.revoke(grant_id=grant.id, revoked_by=ADMIN_B, now=NOW)
    assert await svc.covering(principal_id=PRINCIPAL, target_org="org1", now=NOW) is None
    # revoke_all (deactivation / org transfer) clears every live grant
    await svc.request(
        principal_id=PRINCIPAL, org_id="org2", requested_by=ADMIN_A, reason_code=DEBUG, now=NOW
    )
    n = await svc.revoke_all_for_principal(
        principal_id=PRINCIPAL, revoked_by=ADMIN_A, reason="deactivation", now=NOW
    )
    assert n == 1
    assert await svc.covering(principal_id=PRINCIPAL, target_org="org2", now=NOW) is None


async def test_tenant_authorized_requires_artifact() -> None:
    svc = _service()
    with pytest.raises(MissingApprovalArtifact):
        await svc.request(
            principal_id=PRINCIPAL,
            org_id=None,
            requested_by=ADMIN_A,
            reason_code=DEBUG,
            approval_mode=RawTraceApprovalMode.TENANT_AUTHORIZED,
            expires_at=NOW + timedelta(hours=1),
            now=NOW,
        )
    pending = await svc.request(
        principal_id=PRINCIPAL,
        org_id=None,
        requested_by=ADMIN_A,
        reason_code=DEBUG,
        approval_mode=RawTraceApprovalMode.TENANT_AUTHORIZED,
        external_approval_ref="tenant-approval-123",
        expires_at=NOW + timedelta(hours=1),
        now=NOW,
    )
    active = await svc.approve(grant_id=pending.id, approved_by=ADMIN_B, now=NOW)
    assert active.state is RawTraceGrantState.ACTIVE
    assert active.approved_by is None  # tenant-authorized, not a 2nd admin
    assert active.external_approval_ref == "tenant-approval-123"


async def test_lifecycle_is_audited() -> None:
    repos = in_memory_repositories()
    svc = RawTraceGrantService(repos)
    grant = await svc.request(
        principal_id=PRINCIPAL, org_id="org1", requested_by=ADMIN_A, reason_code=DEBUG, now=NOW
    )
    await svc.revoke(grant_id=grant.id, revoked_by=ADMIN_B, now=NOW)
    actions = [e.action for e in await repos.audit.list_recent(limit=50)]
    assert "raw_grant_granted" in actions
    assert "raw_grant_revoked" in actions
