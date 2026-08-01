"""Raw-trace grant administration (docs/TRACE_GOVERNANCE_PLAN.md §2/§2.1, TG1).

Administrator-gated. The grant is the raw-trace read privilege, distinct from
administration — so an Administrator manages grants but does not read raw
content without one held for their own principal.

Actor and recipient ids are user-row ids (the same namespace the read-path
privilege check uses), so the service's distinctness/self-escalation rules
compare like with like.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from workflow_platform.auth import Role, UserIdentity, require_roles
from workflow_platform.auth.provisioning import current_issuer
from workflow_platform.auth.raw_trace_grants import (
    DuplicateActiveGrant,
    GrantError,
    GrantNotFound,
    RawTraceGrantService,
)
from workflow_platform.persistence import (
    RawTraceApprovalMode,
    RawTraceReasonCode,
    Repositories,
)


class RequestGrantBody(BaseModel):
    principal_id: str  # the recipient's user-row id
    org_id: str | None = None  # None = platform-wide
    reason_code: RawTraceReasonCode
    approval_mode: RawTraceApprovalMode = RawTraceApprovalMode.DUAL_ADMINISTRATOR
    expires_at: datetime | None = None
    ticket_ref: str | None = None
    external_approval_ref: str | None = None


class ApproveGrantBody(BaseModel):
    external_approval_ref: str | None = None


def _grant_http(exc: GrantError) -> HTTPException:
    if isinstance(exc, GrantNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DuplicateActiveGrant):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def build_raw_trace_grants_router(repositories: Repositories) -> APIRouter:
    router = APIRouter(prefix="/api")
    service = RawTraceGrantService(repositories)

    async def _actor_row_id(actor: UserIdentity) -> str:
        row = await repositories.users.get_by_identity(current_issuer(), actor.sub)
        if row is None:
            raise HTTPException(status_code=400, detail="acting user is not provisioned")
        return row.id

    @router.get("/raw-trace-grants")
    async def list_grants(
        actor: UserIdentity = Depends(require_roles(Role.ADMINISTRATOR)),
    ) -> list[dict[str, Any]]:
        grants = await repositories.raw_trace_grants.list_all()
        return [g.model_dump(mode="json") for g in grants]

    @router.post("/raw-trace-grants", status_code=201)
    async def request_grant(
        body: RequestGrantBody,
        actor: UserIdentity = Depends(require_roles(Role.ADMINISTRATOR)),
    ) -> dict[str, Any]:
        if await repositories.users.get(body.principal_id) is None:
            raise HTTPException(status_code=404, detail="No such principal")
        requested_by = await _actor_row_id(actor)
        try:
            grant = await service.request(
                principal_id=body.principal_id,
                org_id=body.org_id,
                requested_by=requested_by,
                reason_code=body.reason_code,
                approval_mode=body.approval_mode,
                expires_at=body.expires_at,
                ticket_ref=body.ticket_ref,
                external_approval_ref=body.external_approval_ref,
            )
        except GrantError as exc:
            raise _grant_http(exc) from exc
        return grant.model_dump(mode="json")

    @router.post("/raw-trace-grants/{grant_id}/approve")
    async def approve_grant(
        grant_id: str,
        body: ApproveGrantBody,
        actor: UserIdentity = Depends(require_roles(Role.ADMINISTRATOR)),
    ) -> dict[str, Any]:
        approved_by = await _actor_row_id(actor)
        try:
            grant = await service.approve(
                grant_id=grant_id,
                approved_by=approved_by,
                external_approval_ref=body.external_approval_ref,
            )
        except GrantError as exc:
            raise _grant_http(exc) from exc
        return grant.model_dump(mode="json")

    @router.post("/raw-trace-grants/{grant_id}/revoke")
    async def revoke_grant(
        grant_id: str,
        actor: UserIdentity = Depends(require_roles(Role.ADMINISTRATOR)),
    ) -> dict[str, Any]:
        revoked_by = await _actor_row_id(actor)
        try:
            grant = await service.revoke(grant_id=grant_id, revoked_by=revoked_by)
        except GrantError as exc:
            raise _grant_http(exc) from exc
        return grant.model_dump(mode="json")

    return router
