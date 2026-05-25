"""End-to-end smoke test against the real Gmail account.

Five checks of increasing scope:

1. Load credentials from disk (validates Gate 3 + Gate 4 are complete).
2. Refresh the access token (validates GmailOAuthProvider + the refresh_token
   is still valid against Google).
3. GmailConnector.authenticate via users.getProfile (validates scope +
   API enablement).
4. Poll inbox for the most recent message (validates list + get + MIME parsing).
5. Send-to-self + poll-and-receive roundtrip (the headline — validates
   send + receive end-to-end). Posts a single short message with a unique
   marker so it's findable in the inbox afterward.

On failure, classifies the error against the gates in
docs/EMAIL_CONNECTOR_PLAN.md and stops — later steps would fail the same
way.

Costs nothing (Gmail API is free at solo-dev volumes). Posts one short
message to the account itself on the last step; safe to delete.

Usage:
    cd backend
    uv run python tools/smoke_gmail.py
    uv run python tools/smoke_gmail.py --account other@example.com
    uv run python tools/smoke_gmail.py --skip-send   # auth + poll only
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from googleapiclient.errors import HttpError

from workflow_platform.connectors.email import (
    EmailAddress,
    EmailSendRequest,
    GmailConnector,
    GmailOAuthProvider,
)
from workflow_platform.connectors.email.gmail_auth import (
    GmailAuthMisconfigured,
    GmailAuthRevoked,
)
from workflow_platform.secrets import EnvSecretStore

DEFAULT_ACCOUNT = "intelligent.workflow.engine@quentinspencer.com"
SECRETS_ROOT = Path(__file__).resolve().parent.parent.parent / ".secrets" / "gmail"

BAR = "=" * 64
INDENT = "      "


def _diagnose(exc: BaseException) -> tuple[str, str] | None:
    """Map a known Gmail/OAuth error to (cause, action). None if unknown."""
    if isinstance(exc, GmailAuthRevoked):
        return (
            "Gate 4 — refresh token is revoked or expired.",
            "Re-run `uv run python tools/gmail_auth.py --account <email>` and accept "
            "the consent screen again.",
        )
    if isinstance(exc, GmailAuthMisconfigured):
        return (
            "Gate 3 or Gate 4 — required credentials are missing from SecretStore.",
            "See the FAIL message above for the specific key; check "
            "docs/EMAIL_CONNECTOR_PLAN.md operator setup.",
        )
    if isinstance(exc, FileNotFoundError):
        return (
            "Credential file missing on disk.",
            "Complete Gates 3+4 in docs/EMAIL_CONNECTOR_PLAN.md. Files live under "
            ".secrets/gmail/<account>/ (chmod 0600, gitignored).",
        )
    if isinstance(exc, HttpError):
        status = exc.resp.status
        body = exc.content.decode("utf-8", errors="replace") if exc.content else ""
        text = body.lower()
        if status == 401:
            return (
                "Google rejected the access token (401).",
                "Most often the refresh token is dead — re-run gmail_auth.py. "
                "Less commonly, the access token wasn't sent — check OAuth provider.",
            )
        if status == 403:
            if "gmail" in text and "not enabled" in text:
                return (
                    "Gate 1 — Gmail API is not enabled on the GCP project.",
                    "Console: APIs & Services → Library → Gmail API → Enable. "
                    "Or: `gcloud services enable gmail.googleapis.com --project=<id>`.",
                )
            if "insufficient" in text or "scope" in text:
                return (
                    "OAuth scope is wrong (403 / insufficient).",
                    "Re-run gmail_auth.py with the configured scopes "
                    "(https://mail.google.com/ for full mailbox).",
                )
            return (
                f"Gmail returned 403: {body[:200]}",
                "Check API enablement (Gate 1) and consent-screen scopes (Gate 2).",
            )
        if status == 404:
            return (
                f"Gmail returned 404: {body[:200]}",
                "A message id was wrong — usually internal. Re-run the smoke "
                "test; if persistent, file a bug.",
            )
        if status == 429:
            return (
                "Gmail rate limit exceeded (429).",
                "Wait a minute and re-run. Default quota is 1 billion units/day "
                "per project; far above solo-dev usage so this would be unusual.",
            )
        return (
            f"Gmail HTTP {status}: {body[:200]}",
            "Check docs/EMAIL_CONNECTOR_PLAN.md or the Gmail API error reference.",
        )
    return None


def _row(label: str, value: str) -> None:
    print(f"{INDENT}{label:<10} {value}")


# --- credential loading + shared state -------------------------------


class _SmokeState:
    """Threads connector + setup across step functions."""

    def __init__(self, account: str) -> None:
        self.account = account
        self.client_credentials_path = SECRETS_ROOT / account / "client_credentials.json"
        self.refresh_token_path = SECRETS_ROOT / account / "refresh_token"
        self.store: EnvSecretStore | None = None
        self.provider: GmailOAuthProvider | None = None
        self.connector: GmailConnector | None = None


async def step_1_load_credentials(state: _SmokeState) -> None:
    if not state.client_credentials_path.exists():
        raise FileNotFoundError(state.client_credentials_path)
    if not state.refresh_token_path.exists():
        raise FileNotFoundError(state.refresh_token_path)

    store = EnvSecretStore()
    await store.put(
        f"gmail/{state.account}/client_credentials",
        state.client_credentials_path.read_text(),
    )
    await store.put(
        f"gmail/{state.account}/refresh_token",
        state.refresh_token_path.read_text().strip(),
    )
    state.store = store
    _row("creds:", str(state.client_credentials_path))
    _row("token:", str(state.refresh_token_path))


async def step_2_refresh_access_token(state: _SmokeState) -> None:
    assert state.store is not None
    provider = GmailOAuthProvider(account=state.account, secret_store=state.store)
    token = await provider.access_token()
    state.provider = provider
    _row("token:", f"{token[:18]}... ({len(token)} chars)")


async def step_3_authenticate(state: _SmokeState) -> None:
    assert state.provider is not None
    connector = GmailConnector(account=state.account, auth_provider=state.provider)
    await connector.authenticate()
    state.connector = connector
    _row("api:", "users.getProfile OK")


async def step_4_poll_inbox(state: _SmokeState) -> None:
    assert state.connector is not None
    # Fetch just one message — enough to exercise list + get + parse.
    messages = await state.connector.poll_inbox(max_messages=1)
    if not messages:
        _row("inbox:", "empty (nothing to fetch — not a failure)")
        return
    msg = messages[0]
    _row("inbox:", f"{len(messages)} fetched")
    _row("from:", f"{msg.from_address.address}")
    _row("subject:", repr(msg.subject)[:60])
    _row(
        "body:",
        f"{len(msg.body_text)} chars text, {len(msg.body_html) if msg.body_html else 0} chars html",
    )
    _row("received:", msg.received_at.isoformat())


async def step_5_send_and_receive(state: _SmokeState) -> None:
    assert state.connector is not None
    marker = uuid.uuid4().hex[:12]
    subject = f"[smoke-gmail {marker}] roundtrip"
    body = (
        f"This message was sent by tools/smoke_gmail.py with marker {marker}.\n"
        "It is safe to delete."
    )
    sent_before = datetime.now(UTC) - timedelta(seconds=30)
    sent_id = await state.connector.send_email(
        EmailSendRequest(
            to=[EmailAddress(address=state.account)],
            subject=subject,
            body_text=body,
        )
    )
    _row("sent:", f"{sent_id}  marker={marker}")

    # Self-send delivery is fast but not instant. Poll for up to 60s.
    deadline = asyncio.get_event_loop().time() + 60.0
    polls = 0
    while asyncio.get_event_loop().time() < deadline:
        polls += 1
        messages = await state.connector.poll_inbox(since=sent_before, max_messages=20)
        for msg in messages:
            if marker in msg.subject:
                _row("delivered:", f"after {polls} poll(s)")
                _row(
                    "match:",
                    f"subject={msg.subject == subject}, body_marker_in={marker in msg.body_text}",
                )
                return
        await asyncio.sleep(2.0)

    raise RuntimeError(
        f"marker {marker!r} never appeared in inbox after 60s "
        f"({polls} polls). delivery slow, send didn't land, or filter wrong."
    )


# --- driver -----------------------------------------------------------


async def main(account: str, *, skip_send: bool) -> int:
    state = _SmokeState(account)
    print(BAR)
    print("Gmail smoke test")
    _row("account:", account)
    _row("secrets:", str(SECRETS_ROOT / account))
    if skip_send:
        _row("mode:", "skip-send (auth + poll only)")
    print(BAR)
    print()

    steps: list[tuple[str, Callable[[_SmokeState], Awaitable[None]]]] = [
        ("Load credentials from disk", step_1_load_credentials),
        ("Refresh access token", step_2_refresh_access_token),
        ("Authenticate (users.getProfile)", step_3_authenticate),
        ("Poll inbox (list + get)", step_4_poll_inbox),
    ]
    if not skip_send:
        steps.append(("Send-to-self + receive roundtrip", step_5_send_and_receive))

    for idx, (label, fn) in enumerate(steps, 1):
        print(f"[{idx}/{len(steps)}] {label}")
        start = time.perf_counter()
        try:
            await fn(state)
        except Exception as exc:
            elapsed = time.perf_counter() - start
            print(f"{INDENT}FAIL ({elapsed:.2f}s)")
            print()
            diag = _diagnose(exc)
            if diag is not None:
                cause, action = diag
                _row("cause:", cause)
                _row("action:", action)
            else:
                _row("error:", f"{type(exc).__name__}: {exc}")
            print()
            print(BAR)
            print(
                f"FAILED at step {idx}/{len(steps)}. "
                "Skipping the rest — they would fail the same way."
            )
            print(BAR)
            return 1
        elapsed = time.perf_counter() - start
        print(f"{INDENT}OK ({elapsed:.2f}s)")
        print()

    print(BAR)
    print("All checks passed. Gmail OAuth + connector are wired correctly.")
    if not skip_send:
        print("One smoke-test message was sent to the account; it's findable")
        print("by the marker shown above. Safe to delete.")
    print(BAR)
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--account", default=DEFAULT_ACCOUNT, help=f"Gmail address (default: {DEFAULT_ACCOUNT})"
    )
    p.add_argument(
        "--skip-send",
        action="store_true",
        help="Skip the send-to-self step. Use to verify auth + poll without filling the inbox.",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(asyncio.run(main(args.account, skip_send=args.skip_send)))
