"""First-party local authentication (`AUTH_MODE=local`, docs/AUTH_PLAN.md).

Email + Argon2id password against the `users` table; opaque server-side
sessions delivered as an HttpOnly cookie. The session token is 256 bits from
`secrets`; only its sha256 is stored — revocation is row deletion, effective
on the very next request.

Security invariants (test-pinned in tests/test_auth_local.py):
- Login failures are indistinguishable (unknown email burns a dummy verify
  so timing doesn't leak existence; same 401 shape for every cause).
- Tokens never land in logs or audit entries; only hashes are persisted.
- Deactivating a user or deleting a session revokes immediately.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from datetime import timedelta

from workflow_platform.auth.identity import UserIdentity
from workflow_platform.auth.passwords import dummy_verify, hash_password, verify_password
from workflow_platform.persistence.models import AuditEntry, AuthSession, _utcnow
from workflow_platform.persistence.repository import AuditRepo, AuthSessionRepo, UserRepo

SESSION_COOKIE = "wp_session"
_DEFAULT_TTL_HOURS = 24 * 7
_LAST_SEEN_THROTTLE_SECONDS = 300.0


def session_ttl_hours() -> float:
    raw = os.environ.get("AUTH_SESSION_TTL_HOURS")
    try:
        return float(raw) if raw else _DEFAULT_TTL_HOURS
    except ValueError:
        return _DEFAULT_TTL_HOURS


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def canonical_email(email: str) -> str:
    return email.strip().lower()


class LoginRateLimiter:
    """In-process sliding window: max `limit` attempts per key per `window`
    seconds. Keys are per-IP and per-email (the endpoint checks both).
    In-memory is fine single-process; a multi-process deployment moves this
    to the DB (noted in docs/AUTH_PLAN.md)."""

    def __init__(self, limit: int = 10, window_seconds: float = 900.0) -> None:
        self._limit = limit
        self._window = window_seconds
        self._attempts: dict[str, list[float]] = {}

    def check(self, key: str) -> float | None:
        """None when allowed; seconds-to-retry when limited. Callers record
        the attempt separately via `record` so a limited request doesn't
        extend its own lockout."""
        now = time.monotonic()
        recent = [t for t in self._attempts.get(key, []) if now - t < self._window]
        self._attempts[key] = recent
        if len(recent) >= self._limit:
            return self._window - (now - recent[0])
        return None

    def record(self, key: str) -> None:
        self._attempts.setdefault(key, []).append(time.monotonic())


class LocalAuthService:
    """Login/logout/session validation for local mode. Constructed once in
    `create_app` and shared by the middleware, the auth endpoints, and the
    WebSocket accept path."""

    def __init__(
        self,
        users: UserRepo,
        sessions: AuthSessionRepo,
        audit: AuditRepo,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._audit = audit
        self._last_seen_write: dict[str, float] = {}

    async def login(self, email: str, password: str, *, source_ip: str) -> str | None:
        """Verify credentials and mint a session. Returns the opaque token
        (for the cookie) or None on any failure — callers must not
        distinguish causes."""
        canonical = canonical_email(email)
        user = await self._users.get_by_login_email(canonical)
        if user is None or user.password_hash is None:
            await asyncio.to_thread(dummy_verify)
            await self._audit_failed(canonical, source_ip, "unknown")
            return None
        ok, needs_rehash = await asyncio.to_thread(verify_password, password, user.password_hash)
        if not ok:
            await self._audit_failed(canonical, source_ip, "bad_password")
            return None
        if not user.is_active:
            await self._audit_failed(canonical, source_ip, "inactive")
            return None
        if needs_rehash:
            user.password_hash = await asyncio.to_thread(hash_password, password)
            await self._users.save(user)

        token = secrets.token_urlsafe(32)
        now = _utcnow()
        await self._sessions.create(
            AuthSession(
                user_id=user.id,
                token_hash=hash_token(token),
                created_at=now,
                expires_at=now + timedelta(hours=session_ttl_hours()),
                last_seen_at=now,
            )
        )
        await self._audit.append(
            AuditEntry(
                actor_type="user",
                actor_id=user.sub,
                action="auth_login",
                detail={"email": canonical, "source_ip": source_ip},
            )
        )
        return token

    async def authenticate(self, token: str) -> UserIdentity | None:
        """Session cookie → UserIdentity, or None (invalid / expired /
        revoked / inactive user)."""
        session = await self._sessions.get_by_token_hash(hash_token(token))
        if session is None or session.expires_at <= _utcnow():
            return None
        user = await self._users.get(session.user_id)
        if user is None or not user.is_active:
            return None
        await self._touch(session)
        return UserIdentity(
            sub=user.sub,
            email=user.email,
            name=user.display_name,
            roles=list(user.roles),
        )

    async def logout(self, token: str) -> bool:
        session = await self._sessions.get_by_token_hash(hash_token(token))
        deleted = await self._sessions.delete_by_token_hash(hash_token(token))
        if deleted and session is not None:
            user = await self._users.get(session.user_id)
            await self._audit.append(
                AuditEntry(
                    actor_type="user",
                    actor_id=user.sub if user else session.user_id,
                    action="auth_logout",
                    detail={},
                )
            )
        return deleted

    async def _touch(self, session: AuthSession) -> None:
        now = time.monotonic()
        last = self._last_seen_write.get(session.id, -_LAST_SEEN_THROTTLE_SECONDS)
        if now - last < _LAST_SEEN_THROTTLE_SECONDS:
            return
        session.last_seen_at = _utcnow()
        await self._sessions.update(session)
        self._last_seen_write[session.id] = now

    async def _audit_failed(self, email: str, source_ip: str, cause: str) -> None:
        # Unauthenticated event: no user id is claimed, so actor_id can't
        # dangle. `cause` is operator-facing; the HTTP response never
        # differentiates.
        await self._audit.append(
            AuditEntry(
                actor_type="anonymous",
                actor_id="login",
                action="auth_login_failed",
                detail={"email": email, "source_ip": source_ip, "cause": cause},
            )
        )
