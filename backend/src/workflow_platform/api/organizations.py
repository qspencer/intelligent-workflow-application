"""Organization lifecycle API (docs/ROLES_PLAN.md S3).

Administrators create and rename organizations; deletion is deliberately
absent (§8 — orgs are rename-only until one actually needs deleting, so
nothing can cascade away a tenant's history by accident). Organization
Administrators can read their own org (the UI shows it); only
Administrators see the full list.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from workflow_platform.auth import Role, UserIdentity, require_roles
from workflow_platform.auth.provisioning import current_issuer
from workflow_platform.persistence import AuditEntry, Organization, Repositories
from workflow_platform.templates import slugify


class CreateOrgRequest(BaseModel):
    name: str
    id: str | None = None  # slugified from name when absent


class RenameOrgRequest(BaseModel):
    name: str


def build_organizations_router(repositories: Repositories) -> APIRouter:
    router = APIRouter(prefix="/api")

    async def _audit(actor: UserIdentity, action: str, detail: dict[str, Any]) -> None:
        await repositories.audit.append(
            AuditEntry(actor_type="user", actor_id=actor.sub, action=action, detail=detail)
        )

    @router.get("/organizations")
    async def list_organizations(
        actor: UserIdentity = Depends(require_roles(Role.ADMINISTRATOR, Role.ORG_ADMIN)),
    ) -> list[dict[str, Any]]:
        orgs = await repositories.organizations.list_all()
        if Role.ADMINISTRATOR.value not in actor.roles:
            row = await repositories.users.get_by_identity(current_issuer(), actor.sub)
            own = row.org_id if row else None
            orgs = [o for o in orgs if o.id == own]
        return [o.model_dump(mode="json") for o in orgs]

    @router.post("/organizations", status_code=201)
    async def create_organization(
        body: CreateOrgRequest,
        actor: UserIdentity = Depends(require_roles(Role.ADMINISTRATOR)),
    ) -> dict[str, Any]:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="A name is required")
        org_id = (body.id or slugify(name)).strip()
        if not org_id:
            raise HTTPException(status_code=400, detail="Organization id may not be empty")
        if await repositories.organizations.get(org_id) is not None:
            raise HTTPException(status_code=409, detail=f"Organization {org_id!r} already exists")
        org = Organization(id=org_id, name=name)
        await repositories.organizations.save(org)
        await _audit(actor, "org_created", {"org_id": org_id, "name": name})
        return org.model_dump(mode="json")

    @router.patch("/organizations/{org_id}")
    async def rename_organization(
        org_id: str,
        body: RenameOrgRequest,
        actor: UserIdentity = Depends(require_roles(Role.ADMINISTRATOR)),
    ) -> dict[str, Any]:
        org = await repositories.organizations.get(org_id)
        if org is None:
            raise HTTPException(status_code=404, detail="No such organization")
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="A name is required")
        if name != org.name:
            old = org.name
            org.name = name
            await repositories.organizations.save(org)
            await _audit(actor, "org_renamed", {"org_id": org_id, "from": old, "to": name})
        return org.model_dump(mode="json")

    return router
