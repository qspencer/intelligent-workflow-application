"""User identity + role assignment.

`UserIdentity` is what every authenticated request carries on `request.state.user`
and what FastAPI dependencies operate on. Roles are derived from IdP groups via
a configurable mapping (see `auth/rbac.py`).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserIdentity(BaseModel):
    sub: str
    email: str | None = None
    name: str | None = None
    groups: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)

    def has_role(self, role: str) -> bool:
        return role in self.roles
