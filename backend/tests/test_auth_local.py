"""AUTH_MODE=local (docs/AUTH_PLAN.md).

Pins the §9 security acceptance criteria:
1. Login failures are indistinguishable (status, body; dummy verify burns
   comparable CPU on the unknown-email path).
2. Logout / deactivation revokes on the very next request.
3. Session tokens are never persisted raw — only hashes; audit entries never
   carry the token.
4. Cross-origin non-GET with a valid cookie is rejected (CSRF).
5. Dev headers are ignored entirely in local mode.
6. (Last-admin guard — tests/test_users_api.py, B3.)
7. WS upgrade with a valid cookie but mismatched Origin is rejected.
8. Logging in twice mints no duplicate user rows (no JIT in local mode).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.auth.local import SESSION_COOKIE, LocalAuthService, LoginRateLimiter
from workflow_platform.auth.passwords import hash_password, verify_password
from workflow_platform.main import create_app
from workflow_platform.persistence import (
    LOCAL_ISSUER,
    AuthSession,
    User,
    in_memory_repositories,
)
from workflow_platform.persistence.models import _utcnow


def _make_user(
    repos: Any,
    email: str = "alice@example.com",
    password: str = "correct horse",
    roles: list[str] | None = None,
    active: bool = True,
) -> User:
    user = User(
        iss=LOCAL_ISSUER,
        sub="",
        email=email,
        password_hash=hash_password(password),
        roles=roles if roles is not None else ["Administrator"],
        is_active=active,
    )
    user.sub = user.id
    asyncio.run(repos.users.save(user))
    return user


def _local_app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any]:
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    return TestClient(create_app(repositories=repos)), repos


def _login(
    client: TestClient, email: str = "alice@example.com", password: str = "correct horse"
) -> Any:
    return client.post("/api/auth/login", json={"email": email, "password": password})


# --- password hashing (B1) ---


def test_password_roundtrip_and_rehash_flag() -> None:
    h = hash_password("s3cret")
    assert h.startswith("$argon2id$")
    assert verify_password("s3cret", h) == (True, False)
    assert verify_password("wrong", h) == (False, False)
    assert verify_password("s3cret", "not-a-hash") == (False, False)


def test_login_email_lookup_is_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = in_memory_repositories()
    _make_user(repos, email="Alice@Example.com")
    found = asyncio.run(repos.users.get_by_login_email("  ALICE@example.COM "))
    assert found is not None
    # SSO rows without credentials are never login targets, even on email match.
    sso = User(iss="dev", sub="bob", email="bob@example.com")
    asyncio.run(repos.users.save(sso))
    assert asyncio.run(repos.users.get_by_login_email("bob@example.com")) is None


# --- login / session lifecycle ---


def test_login_sets_cookie_and_authenticates(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _local_app(monkeypatch)
    user = _make_user(repos, roles=["Organization User"])
    response = _login(client)
    assert response.status_code == 200
    assert SESSION_COOKIE in response.cookies

    me = client.get("/api/me")
    assert me.status_code == 200
    body = me.json()
    assert body["auth_mode"] == "local"
    assert body["identity"]["roles"] == ["Organization User"]
    assert body["user"]["id"] == user.id
    assert "password_hash" not in body["user"]


def test_login_failures_are_indistinguishable(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _local_app(monkeypatch)
    _make_user(repos)
    _make_user(repos, email="gone@example.com", password="pw-inactive", active=False)

    calls = {"dummy": 0}
    from workflow_platform.auth import local as local_mod
    from workflow_platform.auth.passwords import dummy_verify as real_dummy

    def counting_dummy() -> None:
        calls["dummy"] += 1
        real_dummy()

    monkeypatch.setattr(local_mod, "dummy_verify", counting_dummy)

    unknown = _login(client, email="nobody@example.com", password="whatever")
    wrong = _login(client, password="wrong password")
    inactive = _login(client, email="gone@example.com", password="pw-inactive")

    assert unknown.status_code == wrong.status_code == inactive.status_code == 401
    assert unknown.json() == wrong.json() == inactive.json()
    # Unknown email burned a dummy verify (timing comparability).
    assert calls["dummy"] == 1


def test_logout_revokes_on_next_request(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _local_app(monkeypatch)
    _make_user(repos)
    _login(client)
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_deactivation_revokes_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _local_app(monkeypatch)
    user = _make_user(repos)
    _login(client)
    assert client.get("/api/me").status_code == 200
    user.is_active = False
    asyncio.run(repos.users.save(user))
    assert client.get("/api/me").status_code == 401


def test_expired_session_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = in_memory_repositories()
    user = _make_user(repos)
    service = LocalAuthService(repos.users, repos.auth_sessions, repos.audit)
    from workflow_platform.auth.local import hash_token

    asyncio.run(
        repos.auth_sessions.create(
            AuthSession(
                user_id=user.id,
                token_hash=hash_token("stale-token"),
                expires_at=_utcnow() - timedelta(hours=1),
            )
        )
    )
    assert asyncio.run(service.authenticate("stale-token")) is None


def test_tokens_stored_hashed_and_absent_from_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _local_app(monkeypatch)
    _make_user(repos)
    token = _login(client).cookies[SESSION_COOKIE]

    # Raw token never matches a row; only its hash does.
    assert asyncio.run(repos.auth_sessions.get_by_token_hash(token)) is None
    from workflow_platform.auth.local import hash_token

    session = asyncio.run(repos.auth_sessions.get_by_token_hash(hash_token(token)))
    assert session is not None
    assert token not in session.token_hash

    entries = asyncio.run(repos.audit.list_recent())
    assert any(e.action == "auth_login" for e in entries)
    assert all(token not in str(e.detail) for e in entries)


def test_failed_login_audited_as_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _local_app(monkeypatch)
    _login(client, email="nobody@example.com")
    entries = asyncio.run(repos.audit.list_recent())
    failed = [e for e in entries if e.action == "auth_login_failed"]
    assert len(failed) == 1
    assert failed[0].actor_type == "anonymous"
    assert failed[0].detail["email"] == "nobody@example.com"


# --- CSRF + spoofing ---


def test_cross_origin_post_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _local_app(monkeypatch)
    _make_user(repos)
    _login(client)
    evil = client.post(
        "/api/workflows/import", content="{}", headers={"Origin": "https://evil.example"}
    )
    assert evil.status_code == 403
    same = client.post(
        "/api/workflows/import",
        content="not-json",
        headers={"Origin": "http://testserver", "Content-Type": "application/x-yaml"},
    )
    assert same.status_code != 403  # passes CSRF; fails later on content (400)


def test_dev_headers_ignored_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _local_app(monkeypatch)
    response = client.get("/api/me", headers={"X-Dev-User": "mallory", "X-Dev-Groups": "admins"})
    assert response.status_code == 401


# --- WebSocket ---


def test_ws_cookie_auth_and_origin_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.websockets import WebSocketDisconnect

    client, repos = _local_app(monkeypatch)
    _make_user(repos)

    # No cookie (before any login — the TestClient jar would auto-send one).
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/events"):
        pass

    token = _login(client).cookies[SESSION_COOKIE]
    cookie = {"Cookie": f"{SESSION_COOKIE}={token}"}

    with client.websocket_connect("/ws/events", headers=cookie):
        pass  # accepted

    # Valid cookie, hostile Origin → rejected (cross-site WS hijacking).
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            "/ws/events", headers={**cookie, "Origin": "https://evil.example"}
        ),
    ):
        pass


# --- provisioning + rate limiting ---


def test_no_duplicate_rows_from_local_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _local_app(monkeypatch)
    _make_user(repos)
    _login(client)
    client.get("/api/me")
    client.post("/api/auth/logout")
    _login(client)
    client.get("/api/me")
    users = asyncio.run(repos.users.list_all())
    assert len(users) == 1  # the JIT provisioner never ran


def test_login_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = LoginRateLimiter(limit=3, window_seconds=900)
    for _ in range(3):
        assert limiter.check("ip:1.2.3.4") is None
        limiter.record("ip:1.2.3.4")
    retry_in = limiter.check("ip:1.2.3.4")
    assert retry_in is not None and retry_in > 0


def test_login_endpoint_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    client, repos = _local_app(monkeypatch)
    _make_user(repos)
    for _ in range(10):
        _login(client, password="wrong")
    limited = _login(client)  # even the right password is limited now
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
