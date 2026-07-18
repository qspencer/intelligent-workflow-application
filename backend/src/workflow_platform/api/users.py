"""User management API (docs/AUTH_PLAN.md §6). Admin-gated.

Mounted in every mode (viewing who exists is mode-agnostic), but
credential/role/active writes only apply to local-issuer rows — in `oidc`
mode the IdP is the sole role authority (ARCHITECTURE D4), and an SSO row
gaining a password here would bypass it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from workflow_platform.auth import Role, UserIdentity, require_roles
from workflow_platform.auth.local import canonical_email
from workflow_platform.auth.passwords import hash_password
from workflow_platform.persistence import LOCAL_ISSUER, AuditEntry, Repositories, User

VALID_ROLES = {r.value for r in Role}


class CreateUserRequest(BaseModel):
    email: str
    password: str
    roles: list[str] = []
    display_name: str | None = None


class UpdateUserRequest(BaseModel):
    roles: list[str] | None = None
    is_active: bool | None = None
    display_name: str | None = None
    password: str | None = None


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

    async def _audit(actor: UserIdentity, action: str, detail: dict[str, Any]) -> None:
        await repositories.audit.append(
            AuditEntry(actor_type="user", actor_id=actor.sub, action=action, detail=detail)
        )

    async def _other_active_admins(excluding_id: str) -> int:
        users = await repositories.users.list_all()
        return sum(
            1
            for u in users
            if u.id != excluding_id
            and u.iss == LOCAL_ISSUER
            and u.is_active
            and Role.ADMIN.value in u.roles
        )

    @router.get("/users")
    async def list_users(
        _: UserIdentity = Depends(require_roles(Role.ADMIN)),
    ) -> list[dict[str, Any]]:
        users = await repositories.users.list_all()
        users.sort(key=lambda u: u.created_at)
        return [_public(u) for u in users]

    @router.post("/users", status_code=201)
    async def create_user(
        body: CreateUserRequest,
        actor: UserIdentity = Depends(require_roles(Role.ADMIN)),
    ) -> dict[str, Any]:
        _check_roles(body.roles)
        _check_password(body.password)
        email = canonical_email(body.email)
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="A valid email is required")
        if await repositories.users.get_by_login_email(email) is not None:
            raise HTTPException(status_code=409, detail="A user with this email already exists")

        user = User(
            iss=LOCAL_ISSUER,
            sub="",
            email=email,
            display_name=body.display_name,
            roles=list(body.roles),
        )
        user.sub = user.id  # stable sub = row id (AUTH_PLAN §4)
        user.password_hash = await asyncio.to_thread(hash_password, body.password)
        await repositories.users.save(user)
        await _audit(actor, "user_created", {"user_id": user.id, "email": email})
        return _public(user)

    @router.patch("/users/{user_id}")
    async def update_user(
        user_id: str,
        body: UpdateUserRequest,
        actor: UserIdentity = Depends(require_roles(Role.ADMIN)),
    ) -> dict[str, Any]:
        user = await repositories.users.get(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="No such user")
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
        was_active_admin = user.is_active and Role.ADMIN.value in user.roles

        if body.roles is not None:
            _check_roles(body.roles)
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

        # Never leave the deployment without a working Admin (AUTH_PLAN §9.6).
        is_active_admin_after = user.is_active and Role.ADMIN.value in user.roles
        demoting_last_admin = (
            was_active_admin
            and not is_active_admin_after
            and await _other_active_admins(user.id) == 0
        )
        if demoting_last_admin:
            raise HTTPException(status_code=409, detail="Refusing to remove the last active Admin")

        if changed:
            await repositories.users.save(user)
            if revoke:
                await repositories.auth_sessions.delete_by_user(user.id)
            await _audit(
                actor,
                "user_updated",
                {"user_id": user.id, "changed": changed, "sessions_revoked": revoke},
            )
        return _public(user)

    return router
