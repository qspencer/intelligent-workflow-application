#!/usr/bin/env python3
"""Create or update a local-auth user (docs/AUTH_PLAN.md §6 bootstrap).

The deliberate, auditable alternative to "first user is admin" magic — and
the break-glass recovery path (runs against DATABASE_URL directly, so it
works before any login exists and after an admin lockout).

Usage:
    DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \\
        uv run python tools/create_user.py alice@example.com --roles Admin
    # prompts for the password; --display-name, --inactive optional
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.auth.local import canonical_email
from workflow_platform.auth.passwords import hash_password
from workflow_platform.auth.rbac import Role
from workflow_platform.persistence import LOCAL_ISSUER, User
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories

VALID_ROLES = {r.value for r in Role}


async def run(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set.")
        return 2
    bad = [r for r in args.roles if r not in VALID_ROLES]
    if bad:
        print(f"Unknown role(s): {bad}. Valid: {sorted(VALID_ROLES)}")
        return 2

    email = canonical_email(args.email)
    password = args.password or getpass.getpass(f"Password for {email}: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return 2

    db_engine = make_engine(url)
    repos = postgres_repositories(make_session_factory(db_engine))
    try:
        user = await repos.users.get_by_login_email(email)
        created = user is None
        if user is None:
            user = User(iss=LOCAL_ISSUER, sub="", email=email)
            user.sub = user.id  # stable sub = row id (AUTH_PLAN §4)
        user.email = email
        user.password_hash = hash_password(password)
        user.roles = list(args.roles)
        user.is_active = not args.inactive
        if args.display_name:
            user.display_name = args.display_name
        await repos.users.save(user)
        if not created:
            revoked = await repos.auth_sessions.delete_by_user(user.id)
            if revoked:
                print(f"revoked {revoked} existing session(s)")
        print(
            f"{'created' if created else 'updated'}: {email} "
            f"(id={user.id}, roles={user.roles}, active={user.is_active})"
        )
    finally:
        await db_engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--roles", nargs="+", default=["Viewer"], help="one or more role names")
    parser.add_argument("--display-name")
    parser.add_argument("--password", help="omit to be prompted (preferred)")
    parser.add_argument("--inactive", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
