"""Startup user seeding (auth/bootstrap.py): the permanent Administrator and
the opt-in per-role test accounts. Both idempotent; existing accounts are
never modified by a restart."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.auth.bootstrap import TEST_ACCOUNTS, ensure_seed_users
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "WORKFLOW_PLATFORM_ADMIN_EMAIL",
        "WORKFLOW_PLATFORM_ADMIN_PASSWORD",
        "WORKFLOW_PLATFORM_SEED_TEST_USERS",
        "WORKFLOW_PLATFORM_TEST_USER_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)


def test_noop_without_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    repos = in_memory_repositories()
    asyncio.run(ensure_seed_users(repos))
    assert asyncio.run(repos.users.list_all()) == []


def test_permanent_admin_created_once_never_modified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKFLOW_PLATFORM_ADMIN_EMAIL", "Admin@Example.com")
    monkeypatch.setenv("WORKFLOW_PLATFORM_ADMIN_PASSWORD", "very-secret-1")
    repos = in_memory_repositories()
    asyncio.run(ensure_seed_users(repos))

    admin = asyncio.run(repos.users.get_by_login_email("admin@example.com"))
    assert admin is not None and admin.roles == ["Administrator"]
    original_hash = admin.password_hash

    # A rotated env password must not silently reset the account on reboot.
    monkeypatch.setenv("WORKFLOW_PLATFORM_ADMIN_PASSWORD", "different-secret")
    asyncio.run(ensure_seed_users(repos))
    users = asyncio.run(repos.users.list_all())
    assert len(users) == 1
    assert users[0].password_hash == original_hash


def test_short_admin_password_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKFLOW_PLATFORM_ADMIN_EMAIL", "a@b.c")
    monkeypatch.setenv("WORKFLOW_PLATFORM_ADMIN_PASSWORD", "short")
    repos = in_memory_repositories()
    asyncio.run(ensure_seed_users(repos))
    assert asyncio.run(repos.users.list_all()) == []


def test_test_accounts_seed_behind_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("WORKFLOW_PLATFORM_SEED_TEST_USERS", "1")
    repos = in_memory_repositories()
    asyncio.run(ensure_seed_users(repos))
    asyncio.run(ensure_seed_users(repos))  # idempotent

    users = {u.email: u for u in asyncio.run(repos.users.list_all())}
    assert len(users) == len(TEST_ACCOUNTS) == 4
    for email, role in TEST_ACCOUNTS:
        assert users[email].roles == [role.value]
    entries = asyncio.run(repos.audit.list_recent())
    assert sum(1 for e in entries if e.action == "user_created") == 4


def test_seeded_accounts_can_log_in_with_their_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("WORKFLOW_PLATFORM_SEED_TEST_USERS", "1")
    repos: Any = in_memory_repositories()
    # TestClient's context manager runs the lifespan (where seeding lives).
    with TestClient(create_app(repositories=repos)) as client:
        login = client.post(
            "/api/auth/login",
            json={"email": "org-viewer@test.local", "password": "test-password"},
        )
        assert login.status_code == 200
        me = client.get("/api/me").json()
        assert me["identity"]["roles"] == ["Organization Viewer"]
        # And the viewer matrix holds: reads yes, spend no.
        assert client.get("/api/workflows").status_code == 200
        assert client.post("/api/workflows").status_code == 403
