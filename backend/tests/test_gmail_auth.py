"""Tests for `GmailOAuthProvider` — the concrete refresh-token-driven
`GmailAuthProvider` implementation.

Uses `httpx.MockTransport` to fake Google's `/token` endpoint without
ever touching the network. The interactive consent CLI at
`backend/tools/gmail_auth.py` is not unit-tested here — its happy path
is the manual Gate 4 verification (`docs/EMAIL_CONNECTOR_PLAN.md`) and
will get end-to-end coverage on Day 6 via the `GMAIL_LIVE=1` test.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from workflow_platform.connectors.email.gmail_auth import (
    EXPIRY_BUFFER_SECONDS,
    GmailAuthError,
    GmailAuthMisconfigured,
    GmailAuthRevoked,
    GmailOAuthProvider,
)
from workflow_platform.secrets import EnvSecretStore

ACCOUNT = "intelligent.workflow.engine@quentinspencer.com"
CLIENT_ID = "client-id-abc.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-secret"
REFRESH_TOKEN = "1//refresh-abc"


async def _seed_secrets(
    store: EnvSecretStore,
    *,
    account: str = ACCOUNT,
    client_id: str = CLIENT_ID,
    client_secret: str = CLIENT_SECRET,
    refresh_token: str = REFRESH_TOKEN,
    with_credentials: bool = True,
    with_refresh_token: bool = True,
) -> None:
    """Pre-populate SecretStore with the keys GmailOAuthProvider expects."""
    if with_credentials:
        await store.put(
            f"gmail/{account}/client_credentials",
            json.dumps(
                {
                    "installed": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "project_id": "test-project",
                    }
                }
            ),
        )
    if with_refresh_token:
        await store.put(f"gmail/{account}/refresh_token", refresh_token)


async def _cleanup_secrets(store: EnvSecretStore, account: str = ACCOUNT) -> None:
    await store.delete(f"gmail/{account}/client_credentials")
    await store.delete(f"gmail/{account}/refresh_token")


def _ok_token_response(
    access_token: str = "ya29.access-1", expires_in: int = 3600
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": access_token,
            "expires_in": expires_in,
            "token_type": "Bearer",
            "scope": "https://mail.google.com/",
        },
    )


def _make_mock_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _decode_form_body(request: httpx.Request) -> dict[str, str]:
    """Decode a urlencoded request body, returning the first value per key."""
    parsed = parse_qs(request.content.decode())
    return {k: v[0] for k, v in parsed.items()}


@pytest.fixture
async def store() -> AsyncIterator[EnvSecretStore]:
    s = EnvSecretStore()
    await _seed_secrets(s)
    try:
        yield s
    finally:
        await _cleanup_secrets(s)


# ---------- refresh happy path ----------


async def test_access_token_refresh_happy_path(store: EnvSecretStore) -> None:
    """First call hits the token endpoint with the right form-encoded body
    and returns the parsed access token."""
    posted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return _ok_token_response("ya29.first")

    async with _make_mock_client(handler) as http:
        provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store, http_client=http)
        token = await provider.access_token()

    assert token == "ya29.first"
    assert len(posted) == 1
    body = _decode_form_body(posted[0])
    assert body["client_id"] == CLIENT_ID
    assert body["client_secret"] == CLIENT_SECRET
    assert body["refresh_token"] == REFRESH_TOKEN
    assert body["grant_type"] == "refresh_token"
    assert str(posted[0].url) == "https://oauth2.googleapis.com/token"


async def test_access_token_returns_cached_when_still_valid(store: EnvSecretStore) -> None:
    """Second call within the cache window does NOT hit the token endpoint."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _ok_token_response("ya29.cached", expires_in=3600)

    async with _make_mock_client(handler) as http:
        provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store, http_client=http)
        t1 = await provider.access_token()
        t2 = await provider.access_token()
        t3 = await provider.access_token()

    assert t1 == t2 == t3 == "ya29.cached"
    assert call_count == 1, "cached token should be re-used"


async def test_access_token_refreshes_when_cached_token_expires(store: EnvSecretStore) -> None:
    """When the cache window closes, the next call refreshes."""
    tokens = iter(["ya29.first", "ya29.second"])

    def handler(request: httpx.Request) -> httpx.Response:
        return _ok_token_response(next(tokens))

    async with _make_mock_client(handler) as http:
        provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store, http_client=http)
        t1 = await provider.access_token()
        # Manually expire the cache: push expires_at into the past.
        assert provider._cached is not None
        provider._cached.expires_at = time.monotonic() - 1
        t2 = await provider.access_token()

    assert t1 == "ya29.first"
    assert t2 == "ya29.second"


async def test_access_token_refreshes_when_inside_expiry_buffer(store: EnvSecretStore) -> None:
    """The 60s buffer means a token with 30s of life left still refreshes."""
    tokens = iter(["ya29.first", "ya29.second"])

    def handler(request: httpx.Request) -> httpx.Response:
        return _ok_token_response(next(tokens))

    async with _make_mock_client(handler) as http:
        provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store, http_client=http)
        await provider.access_token()
        # 30s of life left — inside the EXPIRY_BUFFER_SECONDS=60 window.
        assert provider._cached is not None
        provider._cached.expires_at = time.monotonic() + (EXPIRY_BUFFER_SECONDS - 30)
        t2 = await provider.access_token()

    assert t2 == "ya29.second"


# ---------- revoked / failure paths ----------


async def test_invalid_grant_response_raises_gmail_auth_revoked(store: EnvSecretStore) -> None:
    """Google's invalid_grant means refresh token is dead — operator must
    re-consent. The error message tells them how."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            },
        )

    async with _make_mock_client(handler) as http:
        provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store, http_client=http)
        with pytest.raises(GmailAuthRevoked) as exc_info:
            await provider.access_token()

    assert ACCOUNT in str(exc_info.value)
    assert "re-consent" in str(exc_info.value) or "gmail_auth.py" in str(exc_info.value)


async def test_other_400_error_raises_gmail_auth_error(store: EnvSecretStore) -> None:
    """Non-invalid_grant 4xx errors are config problems, not revocation."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "invalid_client", "error_description": "Bad client_id."},
        )

    async with _make_mock_client(handler) as http:
        provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store, http_client=http)
        with pytest.raises(GmailAuthError) as exc_info:
            await provider.access_token()

    # Not the revoked subclass — generic GmailAuthError instead.
    assert not isinstance(exc_info.value, GmailAuthRevoked)
    assert "invalid_client" in str(exc_info.value)


async def test_500_response_raises_gmail_auth_error(store: EnvSecretStore) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops")

    async with _make_mock_client(handler) as http:
        provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store, http_client=http)
        with pytest.raises(GmailAuthError) as exc_info:
            await provider.access_token()

    assert "500" in str(exc_info.value)


# ---------- misconfiguration paths ----------


async def test_missing_client_credentials_raises_misconfigured() -> None:
    store = EnvSecretStore()
    await _seed_secrets(store, with_credentials=False)
    try:
        provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store)
        with pytest.raises(GmailAuthMisconfigured, match="Gate 3"):
            await provider.access_token()
    finally:
        await _cleanup_secrets(store)


async def test_missing_refresh_token_raises_misconfigured() -> None:
    store = EnvSecretStore()
    await _seed_secrets(store, with_refresh_token=False)
    try:
        provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store)
        with pytest.raises(GmailAuthMisconfigured, match="Gate 4"):
            await provider.access_token()
    finally:
        await _cleanup_secrets(store)


async def test_malformed_client_credentials_raises_misconfigured() -> None:
    store = EnvSecretStore()
    # Missing the "installed" wrapper — wrong shape.
    await store.put(
        f"gmail/{ACCOUNT}/client_credentials",
        json.dumps({"web": {"client_id": "x", "client_secret": "y"}}),
    )
    await store.put(f"gmail/{ACCOUNT}/refresh_token", "r")
    try:
        provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store)
        with pytest.raises(GmailAuthMisconfigured, match="installed"):
            await provider.access_token()
    finally:
        await _cleanup_secrets(store)


# ---------- concurrency ----------


async def test_concurrent_access_token_calls_share_one_refresh(store: EnvSecretStore) -> None:
    """Cache-miss thundering herd: ten concurrent first-call awaiters should
    trigger exactly one token refresh. The internal `_refresh_lock`
    serializes the refresh, and re-checking under the lock means waiters
    pick up the freshly-cached value."""
    call_count = 0

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        # Brief await to make the race observable if the lock weren't there.
        await asyncio.sleep(0.05)
        return _ok_token_response("ya29.shared")

    # httpx.MockTransport supports an async handler too.
    transport = httpx.MockTransport(slow_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store, http_client=http)
        results = await asyncio.gather(*(provider.access_token() for _ in range(10)))

    assert all(r == "ya29.shared" for r in results)
    assert call_count == 1, "lock should collapse 10 concurrent refreshes into 1"


# ---------- key construction ----------


async def test_secret_keys_use_account_namespace() -> None:
    """Confirms the key shape matches docs/EMAIL_CONNECTOR_PLAN.md
    `gmail/<account>/...` convention."""
    provider = GmailOAuthProvider(account="alice@example.com", secret_store=EnvSecretStore())
    assert provider.client_credentials_key == "gmail/alice@example.com/client_credentials"
    assert provider.refresh_token_key == "gmail/alice@example.com/refresh_token"
