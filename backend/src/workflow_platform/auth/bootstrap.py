"""Startup user seeding for `AUTH_MODE=local`.

Two independent pieces, both idempotent and both no-ops unless configured:

- **Permanent Administrator** — `WORKFLOW_PLATFORM_ADMIN_EMAIL` +
  `WORKFLOW_PLATFORM_ADMIN_PASSWORD`: ensured to exist on every boot.
  Created if missing; an existing account is NEVER modified (a rotated
  password must not be silently reset by a restart). Together with the
  last-active-Administrator guard this makes the account permanent: it
  can't be deleted through the API, and it reappears if the table is
  wiped. Not a migration on purpose — a password hash must never live in
  the repo, and every deployment needs its own credential.

- **Test accounts** — `WORKFLOW_PLATFORM_SEED_TEST_USERS=1`: one account
  per role (admin/org-admin/org-user/org-viewer `@test.local`), password
  from `WORKFLOW_PLATFORM_TEST_USER_PASSWORD` (default `test-password`).
  Known credentials, so this is an explicit opt-in that must never be set
  on a network-reachable deployment; `run-local-be.sh --local-auth` sets
  it for the local loop.
"""

from __future__ import annotations

import asyncio
import logging
import os

from workflow_platform.auth.local import canonical_email
from workflow_platform.auth.passwords import hash_password
from workflow_platform.auth.rbac import Role
from workflow_platform.persistence import LOCAL_ISSUER, AuditEntry, Repositories, User

logger = logging.getLogger(__name__)

TEST_ACCOUNTS: list[tuple[str, Role]] = [
    ("admin@test.local", Role.ADMINISTRATOR),
    ("org-admin@test.local", Role.ORG_ADMIN),
    ("org-user@test.local", Role.ORG_USER),
    ("org-viewer@test.local", Role.ORG_VIEWER),
]


async def _ensure_user(
    repositories: Repositories, email: str, password: str, role: Role, *, origin: str
) -> bool:
    """Create the local user if absent. Returns True when created; an
    existing account is left exactly as it is."""
    email = canonical_email(email)
    if await repositories.users.get_by_login_email(email) is not None:
        return False
    user = User(iss=LOCAL_ISSUER, sub="", email=email, roles=[role.value])
    user.sub = user.id
    user.password_hash = await asyncio.to_thread(hash_password, password)
    await repositories.users.save(user)
    await repositories.audit.append(
        AuditEntry(
            actor_type="system",
            actor_id="user_bootstrap",
            action="user_created",
            detail={"user_id": user.id, "email": email, "origin": origin},
        )
    )
    return True


async def ensure_seed_users(repositories: Repositories) -> None:
    """Called from the app lifespan on every boot. Failures log and never
    block startup — a seeding hiccup must not take the platform down."""
    try:
        admin_email = os.environ.get("WORKFLOW_PLATFORM_ADMIN_EMAIL")
        admin_password = os.environ.get("WORKFLOW_PLATFORM_ADMIN_PASSWORD")
        if admin_email and admin_password:
            if len(admin_password) < 8:
                logger.error("WORKFLOW_PLATFORM_ADMIN_PASSWORD is under 8 chars; not seeding")
            elif await _ensure_user(
                repositories,
                admin_email,
                admin_password,
                Role.ADMINISTRATOR,
                origin="permanent_admin",
            ):
                logger.info("permanent Administrator %s created", canonical_email(admin_email))

        if os.environ.get("WORKFLOW_PLATFORM_SEED_TEST_USERS") == "1":
            password = os.environ.get("WORKFLOW_PLATFORM_TEST_USER_PASSWORD", "test-password")
            created = [
                email
                for email, role in TEST_ACCOUNTS
                if await _ensure_user(repositories, email, password, role, origin="test_seed")
            ]
            if created:
                logger.info("test accounts created: %s", ", ".join(created))
    except Exception:
        logger.exception("user seeding failed; continuing startup")
