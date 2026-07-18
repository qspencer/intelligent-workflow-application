"""User management API (docs/AUTH_PLAN.md §6 + ROLES_PLAN.md S1).

Admin-gated at two scopes: **Administrators** manage everyone;
**Organization Administrators** manage only their own org — they can never
see users outside it (cross-org targets 404, not 403 — no existence leaks),
never grant Administrator, and never modify a user who holds it.

Mounted in every mode, but credential/role/active writes only apply to
local-issuer rows — in `oidc` mode the IdP is the sole role authority
(ARCHITECTURE D4), and an SSO row gaining a password here would bypass it.

Guards (both test-pinned): the platform can never lose its last active
Administrator, and no org can lose its last active Organization
Administrator — even to an Administrator actor (promote a replacement
first).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from workflow_platform.auth import Role, UserIdentity, require_roles
from workflow_platform.auth.local import canonical_email
from workflow_platform.auth.passwords import hash_password
from workflow_platform.auth.provisioning import current_issuer
from workflow_platform.persistence import LOCAL_ISSUER, AuditEntry, Repositories, User

VALID_ROLES = {r.value for r in Role}


class CreateUserRequest(BaseModel):
    email: str
    password: str
    roles: list[str] = []
    display_name: str | None = None
    org_id: str | None = None  # honored for Administrators only


class UpdateUserRequest(BaseModel):
    roles: list[str] | None = None
    is_active: bool | None = None
    display_name: str | None = None
    password: str | None = None
    org_id: str | None = None  # move between orgs — Administrators only (S3)


class ActorScope(BaseModel):
    """Resolved authorization context: platform-wide for Administrators,
    one org for Organization Administrators."""

    identity_sub: str
    is_administrator: bool
    org_id: str | None  # None = unscoped (Administrator)


def _public(user: User) -> dict[str, Any]:
    out = user.model_dump(mode="json", exclude={"password_hash"})
    out["has_password"] = user.password_hash is not None
    return out


def _check_roles(roles: list[str]) -> None:
    bad = sorted(set(roles) - VALID_ROLES)
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown role(s): {bad}")


def _check_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")


def build_users_router(repositories: Repositories) -> APIRouter:
    router = APIRouter(prefix="/api")

    async def _scope(
        actor: UserIdentity = Depends(require_roles(Role.ADMINISTRATOR, Role.ORG_ADMIN)),
    ) -> ActorScope:
        if Role.ADMINISTRATOR.value in actor.roles:
            return ActorScope(identity_sub=actor.sub, is_administrator=True, org_id=None)
        row = await repositories.users.get_by_identity(current_issuer(), actor.sub)
        if row is None:
            # An Org Admin with no platform row has no org to administer.
            raise HTTPException(status_code=403, detail="No platform user record")
        return ActorScope(identity_sub=actor.sub, is_administrator=False, org_id=row.org_id)

    async def _audit(scope: ActorScope, action: str, detail: dict[str, Any]) -> None:
        await repositories.audit.append(
            AuditEntry(actor_type="user", actor_id=scope.identity_sub, action=action, detail=detail)
        )

    async def _other_active_administrators(excluding_id: str) -> int:
        users = await repositories.users.list_all()
        return sum(
            1
            for u in users
            if u.id != excluding_id
            and u.iss == LOCAL_ISSUER
            and u.is_active
            and Role.ADMINISTRATOR.value in u.roles
        )

    async def _other_active_org_admins(org_id: str, excluding_id: str) -> int:
        users = await repositories.users.list_all()
        return sum(
            1
            for u in users
            if u.id != excluding_id
            and u.org_id == org_id
            and u.is_active
            and Role.ORG_ADMIN.value in u.roles
        )

    def _forbid_administrator_grant(scope: ActorScope, roles: list[str]) -> None:
        if not scope.is_administrator and Role.ADMINISTRATOR.value in roles:
            raise HTTPException(
                status_code=403,
                detail="Only an Administrator can grant Administrator",
            )

    @router.get("/users")
    async def list_users(scope: ActorScope = Depends(_scope)) -> list[dict[str, Any]]:
        users = await repositories.users.list_all()
        if scope.org_id is not None:
            users = [u for u in users if u.org_id == scope.org_id]
        users.sort(key=lambda u: u.created_at)
        return [_public(u) for u in users]

    @router.post("/users", status_code=201)
    async def create_user(
        body: CreateUserRequest, scope: ActorScope = Depends(_scope)
    ) -> dict[str, Any]:
        _check_roles(body.roles)
        _check_password(body.password)
        _forbid_administrator_grant(scope, body.roles)
        email = canonical_email(body.email)
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="A valid email is required")
        if await repositories.users.get_by_login_email(email) is not None:
            raise HTTPException(status_code=409, detail="A user with this email already exists")

        if scope.is_administrator:
            actor_row = await repositories.users.get_by_identity(
                current_issuer(), scope.identity_sub
            )
            org_id = body.org_id or (actor_row.org_id if actor_row else "default")
            if await repositories.organizations.get(org_id) is None:
                raise HTTPException(status_code=400, detail=f"No such organization: {org_id}")
        else:
            # Organization Administrators create users in their org, full stop.
            org_id = scope.org_id or "default"
            if body.org_id is not None and body.org_id != org_id:
                raise HTTPException(
                    status_code=403, detail="Cannot create users in another organization"
                )

        user = User(
            iss=LOCAL_ISSUER,
            sub="",
            email=email,
            display_name=body.display_name,
            roles=list(body.roles),
            org_id=org_id,
        )
        user.sub = user.id  # stable sub = row id (AUTH_PLAN §4)
        user.password_hash = await asyncio.to_thread(hash_password, body.password)
        await repositories.users.save(user)
        await _audit(scope, "user_created", {"user_id": user.id, "email": email, "org_id": org_id})
        return _public(user)

    @router.patch("/users/{user_id}")
    async def update_user(
        user_id: str, body: UpdateUserRequest, scope: ActorScope = Depends(_scope)
    ) -> dict[str, Any]:
        user = await repositories.users.get(user_id)
        # Cross-org targets are invisible, not forbidden (no existence leak).
        if user is None or (scope.org_id is not None and user.org_id != scope.org_id):
            raise HTTPException(status_code=404, detail="No such user")
        if not scope.is_administrator and Role.ADMINISTRATOR.value in user.roles:
            raise HTTPException(
                status_code=403, detail="Only an Administrator can modify an Administrator"
            )
        wants_credential_write = (
            body.roles is not None or body.is_active is not None or body.password is not None
        )
        if wants_credential_write and user.iss != LOCAL_ISSUER:
            raise HTTPException(
                status_code=400,
                detail="Roles/credentials for SSO users are managed by the IdP (D4)",
            )

        changed: list[str] = []
        revoke = False
        was_active_administrator = user.is_active and Role.ADMINISTRATOR.value in user.roles
        was_active_org_admin = user.is_active and Role.ORG_ADMIN.value in user.roles
        old_org = user.org_id

        if body.org_id is not None and body.org_id != user.org_id:
            # Moving a user between orgs changes what they can see — that's
            # a platform decision, not a tenant one.
            if not scope.is_administrator:
                raise HTTPException(
                    status_code=403, detail="Only an Administrator can move users between orgs"
                )
            if await repositories.organizations.get(body.org_id) is None:
                raise HTTPException(status_code=400, detail=f"No such organization: {body.org_id}")
            user.org_id = body.org_id
            changed.append("org_id")
            revoke = True

        if body.roles is not None:
            _check_roles(body.roles)
            _forbid_administrator_grant(scope, body.roles)
            if body.roles != user.roles:
                user.roles = list(body.roles)
                changed.append("roles")
                revoke = True
        if body.is_active is not None and body.is_active != user.is_active:
            user.is_active = body.is_active
            changed.append("is_active")
            if not body.is_active:
                revoke = True
        if body.display_name is not None and body.display_name != user.display_name:
            user.display_name = body.display_name
            changed.append("display_name")
        if body.password is not None:
            _check_password(body.password)
            user.password_hash = await asyncio.to_thread(hash_password, body.password)
            changed.append("password")
            revoke = True

        # Never leave the platform without an Administrator (AUTH_PLAN §9.6)…
        if (
            was_active_administrator
            and not (user.is_active and Role.ADMINISTRATOR.value in user.roles)
            and await _other_active_administrators(user.id) == 0
        ):
            raise HTTPException(
                status_code=409, detail="Refusing to remove the last active Administrator"
            )
        # …and never leave an org without one (ROLES_PLAN §2.2). Applies to
        # Administrator actors too: promote a replacement first.
        org_admin_lost_for_old_org = was_active_org_admin and (
            not (user.is_active and Role.ORG_ADMIN.value in user.roles) or user.org_id != old_org
        )
        if org_admin_lost_for_old_org and await _other_active_org_admins(old_org, user.id) == 0:
            raise HTTPException(
                status_code=409,
                detail="Refusing to remove this organization's last active "
                "Organization Administrator",
            )

        if changed:
            await repositories.users.save(user)
            if revoke:
                await repositories.auth_sessions.delete_by_user(user.id)
            await _audit(
                scope,
                "user_updated",
                {"user_id": user.id, "changed": changed, "sessions_revoked": revoke},
            )
        return _public(user)

    return router
