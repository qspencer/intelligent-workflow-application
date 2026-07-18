"""Org-scope resolution for resource endpoints (docs/ROLES_PLAN.md §4, S2).

`require_roles` answers "may you take this action"; `OrgScope` answers "over
which resources". Administrators are unscoped (`org_id=None`) — every filter
they pass through is an explicit, audited bypass, never a missing check.
"""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel

from workflow_platform.auth.identity import UserIdentity
from workflow_platform.auth.provisioning import current_issuer
from workflow_platform.auth.rbac import Role
from workflow_platform.persistence import Repositories


class OrgScope(BaseModel):
    sub: str
    is_administrator: bool
    org_id: str | None
    """The org every query filters by. None = unscoped (Administrator)."""
    home_org_id: str
    """The caller's own org — for Administrators, the reference point that
    makes a mutation "cross-org" (and therefore `org_bypass`-audited)."""


async def resolve_org_scope(repositories: Repositories, actor: UserIdentity) -> OrgScope:
    row = await repositories.users.get_by_identity(current_issuer(), actor.sub)
    if Role.ADMINISTRATOR.value in actor.roles:
        return OrgScope(
            sub=actor.sub,
            is_administrator=True,
            org_id=None,
            home_org_id=row.org_id if row else "default",
        )
    if row is None:
        # Provisioning runs in the middleware, so a missing row is a genuine
        # anomaly — fail closed rather than defaulting into an org.
        raise HTTPException(status_code=403, detail="No platform user record")
    return OrgScope(
        sub=actor.sub, is_administrator=False, org_id=row.org_id, home_org_id=row.org_id
    )
