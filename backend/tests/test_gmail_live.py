"""Live-Gmail smoke test, opt-in via `GMAIL_LIVE=1`.

Sends a mail to the project Gmail account itself, then polls the inbox
until the message arrives, and asserts the round-trip preserved the
unique marker subject + body. Costs nothing (Gmail API is free at
solo-dev volumes); never runs in CI by default.

Prerequisites (mirror `docs/EMAIL_CONNECTOR_PLAN.md` gates):
  - Gates 1-3 complete: GCP project + Gmail API enabled + OAuth client
    JSON downloaded to `.secrets/gmail/<account>/client_credentials.json`.
  - Gate 4 complete: `backend/tools/gmail_auth.py --account <account>`
    has produced `.secrets/gmail/<account>/refresh_token`.

Usage:
    cd backend
    GMAIL_LIVE=1 uv run pytest -m gmail_live

To target a different account (e.g. a future second project mailbox):
    GMAIL_LIVE=1 GMAIL_LIVE_ACCOUNT=other@example.com uv run pytest -m gmail_live
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from workflow_platform.connectors.email import (
    EmailAddress,
    EmailSendRequest,
    GmailConnector,
    GmailOAuthProvider,
)
from workflow_platform.secrets import EnvSecretStore

DEFAULT_ACCOUNT = "intelligent.workflow.engine@quentinspencer.com"
ACCOUNT = os.environ.get("GMAIL_LIVE_ACCOUNT", DEFAULT_ACCOUNT)

# `.secrets/` lives at the repo root, one above `backend/`.
SECRETS_ROOT = Path(__file__).resolve().parents[2] / ".secrets" / "gmail"
CLIENT_CREDENTIALS_PATH = SECRETS_ROOT / ACCOUNT / "client_credentials.json"
REFRESH_TOKEN_PATH = SECRETS_ROOT / ACCOUNT / "refresh_token"

# Two gates: the env var and the on-disk credentials. Both must hold for the
# tests to even attempt to run. The skip messages are explicit so a future
# operator sees exactly what's missing.
pytestmark = [
    pytest.mark.gmail_live,
    pytest.mark.skipif(
        os.environ.get("GMAIL_LIVE") != "1",
        reason="GMAIL_LIVE not set; skipping live Gmail tests",
    ),
    pytest.mark.skipif(
        not CLIENT_CREDENTIALS_PATH.exists(),
        reason=(
            f"Client credentials not found at {CLIENT_CREDENTIALS_PATH}. "
            "Complete Gate 3 in docs/EMAIL_CONNECTOR_PLAN.md."
        ),
    ),
    pytest.mark.skipif(
        not REFRESH_TOKEN_PATH.exists(),
        reason=(
            f"Refresh token not found at {REFRESH_TOKEN_PATH}. "
            f"Run `backend/tools/gmail_auth.py --account {ACCOUNT}` to complete Gate 4."
        ),
    ),
]


async def _seed_secret_store() -> EnvSecretStore:
    """Load on-disk credentials into a process-local EnvSecretStore.
    The on-disk files are the source of truth in dev; the SecretStore is
    seeded fresh per test session."""
    store = EnvSecretStore()
    await store.put(
        f"gmail/{ACCOUNT}/client_credentials",
        CLIENT_CREDENTIALS_PATH.read_text(),
    )
    await store.put(
        f"gmail/{ACCOUNT}/refresh_token",
        REFRESH_TOKEN_PATH.read_text().strip(),
    )
    return store


@pytest.fixture()
async def live_connector() -> GmailConnector:
    store = await _seed_secret_store()
    provider = GmailOAuthProvider(account=ACCOUNT, secret_store=store)
    return GmailConnector(account=ACCOUNT, auth_provider=provider)


async def test_authenticate_against_live_gmail(live_connector: GmailConnector) -> None:
    """Smallest possible live check: `authenticate()` hits `getProfile`. If
    OAuth is configured correctly, this succeeds; if the refresh token is
    revoked or the client credentials are wrong, it raises."""
    await live_connector.authenticate()


async def test_send_to_self_and_poll_inbox_roundtrip(live_connector: GmailConnector) -> None:
    """The headline live test: compose a unique-marker message, send it to
    the account itself, then poll the inbox until it arrives. Asserts the
    subject + body survive the round-trip."""
    # Unique marker prevents this test from picking up a previous run's
    # message or any other inbox traffic.
    marker = uuid.uuid4().hex[:12]
    subject = f"[live-test {marker}] gmail roundtrip"
    body = (
        f"This message was sent by tests/test_gmail_live.py with marker {marker}.\n"
        "It is safe to delete."
    )

    sent_before = datetime.now(UTC) - timedelta(seconds=30)

    sent_message_id = await live_connector.send_email(
        EmailSendRequest(
            to=[EmailAddress(address=ACCOUNT)],
            subject=subject,
            body_text=body,
        )
    )
    assert sent_message_id

    # Gmail's send → inbox delivery for self-send is fast but not instant.
    # Poll with a generous deadline; each poll asks for messages newer than
    # `sent_before`. The loop exits on first match.
    deadline = asyncio.get_event_loop().time() + 60.0
    while asyncio.get_event_loop().time() < deadline:
        messages = await live_connector.poll_inbox(since=sent_before, max_messages=20)
        for msg in messages:
            if marker in msg.subject:
                # Found it. Body comparison is `in` not `==` because Gmail
                # may append signatures or normalize whitespace.
                assert msg.subject == subject
                assert marker in msg.body_text
                assert msg.from_address.address.lower() == ACCOUNT.lower()
                # to[] is parsed from headers; should include the dest address.
                assert any(a.address.lower() == ACCOUNT.lower() for a in msg.to)
                return
        await asyncio.sleep(2.0)

    pytest.fail(
        f"Marker {marker!r} never appeared in inbox after 60s. "
        "Either delivery is unusually slow, the send didn't actually land, "
        "or the poll filter is wrong."
    )


async def test_apply_label_against_inbox_message(live_connector: GmailConnector) -> None:
    """Apply the `INBOX` system label to a recently-received message. INBOX
    is always present on every account; testing custom-label apply would
    require the operator to have created `triaged/urgent` etc., which we
    can't assume."""
    # Pull a recent message — any one will do.
    messages = await live_connector.poll_inbox(max_messages=1)
    if not messages:
        pytest.skip("Inbox is empty; nothing to apply a label to.")

    msg = messages[0]
    # INBOX is a system label; apply_labels is a no-op semantically
    # (the message is already in INBOX) but exercises the modify code path.
    await live_connector.apply_labels(msg.message_id, ["INBOX"])
