"""RBAC roles + IdP group mapping + endpoint guards.

Five roles aligned with `docs/ARCHITECTURE.md`'s "Human Permissions" table:

| Role            | Reads | Writes | Audit access |
|-----------------|-------|--------|--------------|
| Admin           |   ✓   |   ✓    |      ✓       |
| Workflow Designer |   ✓   |   ✓    |              |
| Operator        |   ✓   |   ✓ (start/retry/approve) |  |
| Viewer          |   ✓   |        |              |
| Auditor         |   ✓ (audit) |   |     ✓       |

Roles map from IdP groups via the `OIDC_GROUP_TO_ROLE` env var (JSON).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from enum import StrEnum

from fastapi import Depends, HTTPException, Request

from workflow_platform.auth.identity import UserIdentity


class Role(StrEnum):
    ADMIN = "Admin"
    DESIGNER = "Workflow Designer"
    OPERATOR = "Operator"
    VIEWER = "Viewer"
    AUDITOR = "Auditor"


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
        "admins": Role.ADMIN.value,
        "designers": Role.DESIGNER.value,
        "operators": Role.OPERATOR.value,
        "viewers": Role.VIEWER.value,
        "auditors": Role.AUDITOR.value,
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
