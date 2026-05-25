"""OAuth refresh-token management for `GmailConnector`.

Implements the structural `GmailAuthProvider` protocol (defined in
`gmail.py`): returns a live access token, refreshing via Google's token
endpoint when the cached one is near expiry. Refresh tokens themselves
live in `SecretStore`; cached access tokens are per-process.

The token endpoint is hit directly via `httpx` (async-native) rather
than through `google.oauth2.credentials.Credentials.refresh()` (sync,
needs `requests`). Same net result, no extra `to_thread` wrapping.

The one-shot interactive consent flow that *produces* the refresh
token in the first place lives in `backend/tools/gmail_auth.py` —
that's the operator-facing Gate 4 helper, separate from this
machine-facing runtime path.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from workflow_platform.secrets import SecretNotFoundError, SecretStore

TOKEN_URL = "https://oauth2.googleapis.com/token"

# Refresh the access token when it has fewer than this many seconds left.
# Google access tokens last ~3600s; a 60s buffer keeps the polling loop from
# hitting expiry mid-request.
EXPIRY_BUFFER_SECONDS = 60


class GmailAuthError(Exception):
    """Base for Gmail OAuth errors."""


class GmailAuthRevoked(GmailAuthError):
    """Refresh token is dead — operator must re-run the consent flow.

    Per `docs/EMAIL_CONNECTOR_PLAN.md` Risk #4 / Open Question (resolved),
    the trigger / connector layer should route this to
    `RequestHumanReviewTool` as an `escalation_requested` audit entry.
    """


class GmailAuthMisconfigured(GmailAuthError):
    """A required SecretStore key is missing or malformed.

    Distinct from `GmailAuthRevoked`: this is an operator-setup gap (Gate 3
    or Gate 4 not completed), not a runtime token failure.
    """


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float  # `time.monotonic()` reference


class GmailOAuthProvider:
    """Provides access tokens for one Gmail account.

    Reads:
      - `gmail/<account>/client_credentials` — JSON downloaded from GCP console
        (the OAuth client ID, Gate 3)
      - `gmail/<account>/refresh_token` — the user's refresh token
        (produced by `backend/tools/gmail_auth.py`, Gate 4)
    """

    def __init__(
        self,
        *,
        account: str,
        secret_store: SecretStore,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.account = account
        self.secret_store = secret_store
        self._http_client = http_client
        self._cached: _CachedToken | None = None
        # Serializes refresh calls — without this, concurrent pollers could
        # all hit the token endpoint when the cache expires.
        self._refresh_lock = asyncio.Lock()

    @property
    def client_credentials_key(self) -> str:
        return f"gmail/{self.account}/client_credentials"

    @property
    def refresh_token_key(self) -> str:
        return f"gmail/{self.account}/refresh_token"

    async def access_token(self) -> str:
        """Return a valid access token. Refreshes via Google when needed.

        Cache hits are lock-free (fast path). Misses serialize on
        `_refresh_lock` so the second-arriving caller picks up the fresh
        token rather than racing for another refresh.
        """
        cached = self._cached
        now = time.monotonic()
        if cached and cached.expires_at > now + EXPIRY_BUFFER_SECONDS:
            return cached.access_token

        async with self._refresh_lock:
            # Re-check under the lock — another waiter may have refreshed
            # while we were queued.
            cached = self._cached
            now = time.monotonic()
            if cached and cached.expires_at > now + EXPIRY_BUFFER_SECONDS:
                return cached.access_token
            self._cached = await self._refresh()
            return self._cached.access_token

    async def _refresh(self) -> _CachedToken:
        client_id, client_secret = await self._load_client_credentials()
        refresh_token = await self._load_refresh_token()

        data = await self._post_token_request(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )

        access_token = str(data["access_token"])
        expires_in = int(data.get("expires_in", 3600))
        return _CachedToken(
            access_token=access_token,
            expires_at=time.monotonic() + expires_in,
        )

    async def _load_client_credentials(self) -> tuple[str, str]:
        try:
            creds = await self.secret_store.get_json(self.client_credentials_key)
        except SecretNotFoundError as exc:
            raise GmailAuthMisconfigured(
                f"Client credentials not in SecretStore at "
                f"{self.client_credentials_key!r}. See Gate 3 in "
                f"docs/EMAIL_CONNECTOR_PLAN.md."
            ) from exc

        installed = creds.get("installed") if isinstance(creds, dict) else None
        if not isinstance(installed, dict):
            raise GmailAuthMisconfigured(
                f"Client credentials at {self.client_credentials_key!r} "
                f"missing 'installed' section (expected Desktop-app OAuth JSON)."
            )
        client_id = installed.get("client_id")
        client_secret = installed.get("client_secret")
        if not (isinstance(client_id, str) and client_id):
            raise GmailAuthMisconfigured(
                f"Client credentials at {self.client_credentials_key!r} "
                f"missing or empty 'client_id'."
            )
        if not (isinstance(client_secret, str) and client_secret):
            raise GmailAuthMisconfigured(
                f"Client credentials at {self.client_credentials_key!r} "
                f"missing or empty 'client_secret'."
            )
        return client_id, client_secret

    async def _load_refresh_token(self) -> str:
        try:
            return await self.secret_store.get(self.refresh_token_key)
        except SecretNotFoundError as exc:
            raise GmailAuthMisconfigured(
                f"Refresh token not in SecretStore at "
                f"{self.refresh_token_key!r}. Run "
                f"`backend/tools/gmail_auth.py --account {self.account}` "
                f"to complete Gate 4."
            ) from exc

    async def _post_token_request(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, Any]:
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.post(
                TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code == 200:
            body = response.json()
            if not isinstance(body, dict):
                raise GmailAuthError(f"Token endpoint returned non-dict JSON: {body!r}")
            return body

        # Non-200: parse error body, map invalid_grant → GmailAuthRevoked.
        error_body: dict[str, Any] = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                error_body = parsed
        except ValueError:
            pass

        if error_body.get("error") == "invalid_grant":
            raise GmailAuthRevoked(
                f"Refresh token for {self.account!r} is revoked or expired. "
                f"Run `backend/tools/gmail_auth.py --account {self.account}` "
                f"to re-consent. Detail: {error_body.get('error_description', '<none>')}"
            )

        raise GmailAuthError(
            f"Token refresh failed for {self.account!r} "
            f"(status {response.status_code}): "
            f"{error_body.get('error', response.text[:200])}"
        )
