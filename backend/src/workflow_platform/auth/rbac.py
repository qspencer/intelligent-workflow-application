"""RBAC roles + IdP group mapping + endpoint guards.

Four tenant-scoped roles per `docs/ROLES_PLAN.md` (which revised D4's
"Human Permissions" table; the old five global roles map per its §3 table):

| Role                       | Scope        | Powers |
|----------------------------|--------------|--------|
| Administrator              | platform     | everything, all orgs |
| Organization Administrator | one org      | everything in their org, incl. its users |
| Organization User          | one org      | create/edit/run workflows; read everything |
| Organization Viewer        | one org      | read-only (run + dry-run are spend actions) |

Until ROLES_PLAN S2, only user management is org-scoped — the org checks on
other resources land with `require_org_access`. Roles map from IdP groups via
the `OIDC_GROUP_TO_ROLE` env var (JSON).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from enum import StrEnum

from fastapi import Depends, HTTPException, Request

from workflow_platform.auth.identity import UserIdentity


class Role(StrEnum):
    ADMINISTRATOR = "Administrator"
    ORG_ADMIN = "Organization Administrator"
    ORG_USER = "Organization User"
    ORG_VIEWER = "Organization Viewer"


# Convenience tuples for require_roles call sites (ROLES_PLAN §4 table).
ORG_WRITE_ROLES = (Role.ADMINISTRATOR, Role.ORG_ADMIN, Role.ORG_USER)
ANY_ROLE = tuple(Role)


def load_group_to_role_map() -> dict[str, str]:
    """Read OIDC_GROUP_TO_ROLE from env. JSON object: { "group_name": "Role" }.

    Defaults are friendly for local dev / CI: `admins → Admin`, `designers
    → Workflow Designer`, etc. Override by setting the env var explicitly.
    """
    raw = os.environ.get("OIDC_GROUP_TO_ROLE")
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return {str(k): str(v) for k, v in data.items()}
    return {
        "admins": Role.ADMINISTRATOR.value,
        "org-admins": Role.ORG_ADMIN.value,
        "org-users": Role.ORG_USER.value,
        "org-viewers": Role.ORG_VIEWER.value,
    }


def assign_roles(groups: list[str]) -> list[str]:
    mapping = load_group_to_role_map()
    return sorted({mapping[g] for g in groups if g in mapping})


def current_user(request: Request) -> UserIdentity:
    user = getattr(request.state, "user", None)
    if not isinstance(user, UserIdentity):
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_roles(*allowed: str | Role) -> Callable[..., UserIdentity]:
    """Dependency factory. Pass any combination of roles; the user needs at
    least one. Returns a callable suitable for `Depends(...)`.
    """
    allowed_set = {str(r) for r in allowed}

    def _checker(user: UserIdentity = Depends(current_user)) -> UserIdentity:
        if not (allowed_set & set(user.roles)):
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: requires one of {sorted(allowed_set)}",
            )
        return user

    return _checker
