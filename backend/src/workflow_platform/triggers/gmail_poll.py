"""GmailPollTrigger — fires a workflow on new Gmail messages.

Wraps a `GmailConnector` in the `Trigger` plugin shape: starts a background
asyncio task that polls Gmail every `poll_interval_seconds`, invoking
`on_event` once per new message with that message's `EmailMessage` JSON.

Cursor semantics: on first `start()`, the cursor initializes to "now",
so historical mail does *not* flood the engine. Each poll advances the
cursor to the latest received_at seen, so subsequent polls fetch only
strictly newer messages. The cursor is process-local — restarting the
daemon means the trigger fires only on mail received *after* the restart.

Failure modes:
- `GmailAuthRevoked` (refresh token dead): logged at ERROR; the loop
  backs off by `auth_revoked_backoff_seconds` instead of the normal
  interval so the failure doesn't tight-loop. Operator must run the
  consent CLI to recover. Wiring this to `escalation_requested` audit
  entries lives one layer up in the orchestrator.
- `GmailAuthMisconfigured` (credentials absent/invalid in the
  SecretStore): a *permanent* configuration error — retrying can't fix
  it. Logged once at WARNING (no traceback) and the loop stops, instead
  of spewing a stack trace every interval. This is the common dev case
  where a bundled example ships a `gmail_poll` trigger but Gmail isn't
  configured locally. Configure credentials and restart to enable it.
- Any other exception during poll or callback dispatch: logged at
  EXCEPTION, loop continues at the normal interval. A misbehaving
  individual message doesn't kill the trigger.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from workflow_platform.connectors.email.gmail import GmailConnector
from workflow_platform.connectors.email.gmail_auth import (
    GmailAuthMisconfigured,
    GmailAuthRevoked,
)
from workflow_platform.connectors.email.models import EmailMessage
from workflow_platform.triggers.base import Trigger, TriggerCallback

logger = logging.getLogger(__name__)


class GmailPollTrigger(Trigger):
    type: ClassVar[str] = "gmail_poll"

    def __init__(
        self,
        *,
        connector: GmailConnector,
        poll_interval_seconds: float = 60.0,
        label: str | None = "INBOX",
        max_messages: int = 50,
        auth_revoked_backoff_seconds: float = 300.0,
        query: str | None = None,
        download_dir: str | None = None,
        slim_payload: bool = False,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.connector = connector
        self.poll_interval_seconds = poll_interval_seconds
        self.label = label
        self.max_messages = max_messages
        self.auth_revoked_backoff_seconds = auth_revoked_backoff_seconds
        # Extra Gmail search clause (e.g. `has:attachment filename:zip`) —
        # server-side filtering so the trigger only fires on matching mail.
        self.query = query
        # When set, each message's attachments are downloaded to
        # `<download_dir>/<message_id>/<filename>` before the callback fires,
        # and the payload gains `attachment_paths`. Deterministic steps can't
        # reach the connector, so the trigger delivers files, not ids.
        self.download_dir = download_dir
        # Drop body_html + raw headers from the payload. Newsletters carry
        # 100KB+ of HTML and multi-KB DKIM/ARC headers; a triage agent that
        # reads the payload verbatim burns ~40k input tokens per message on
        # content body_text already covers. Opt-in per workflow.
        self.slim_payload = slim_payload
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._cursor: datetime | None = None

    async def start(self, on_event: TriggerCallback) -> None:
        if self._task is not None:
            return
        # Initialize the cursor to "now" so historical mail doesn't flood
        # on first start. Tests can pre-set `_cursor` before calling start.
        if self._cursor is None:
            self._cursor = datetime.now(UTC)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(on_event))

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
        self._task = None

    async def _loop(self, on_event: TriggerCallback) -> None:
        while not self._stop.is_set():
            try:
                messages = await self.connector.poll_inbox(
                    since=self._cursor,
                    label=self.label,
                    max_messages=self.max_messages,
                    query=self.query,
                )
            except GmailAuthRevoked:
                logger.error(
                    "Gmail auth revoked for account %r — backing off %.0fs. "
                    "Operator must re-run backend/tools/gmail_auth.py to recover.",
                    self.connector.account,
                    self.auth_revoked_backoff_seconds,
                )
                if await self._wait_or_stop(self.auth_revoked_backoff_seconds):
                    return
                continue
            except GmailAuthMisconfigured as exc:
                # Permanent config error (e.g. credentials absent from the
                # SecretStore) — retrying can't fix it. Log once, plainly, and
                # stop this trigger instead of dumping a traceback every cycle.
                logger.warning(
                    "Gmail poll disabled for account %r: %s "
                    "Configure credentials and restart to enable it.",
                    self.connector.account,
                    exc,
                )
                return
            except Exception:
                logger.exception(
                    "Gmail poll failed for account %r; retrying after %.0fs.",
                    self.connector.account,
                    self.poll_interval_seconds,
                )
                if await self._wait_or_stop(self.poll_interval_seconds):
                    return
                continue

            if messages:
                self._cursor = max(m.received_at for m in messages)

            for msg in messages:
                if self._stop.is_set():
                    return
                try:
                    await on_event(await self._build_payload(msg))
                except Exception:
                    logger.exception(
                        "Trigger callback failed for Gmail message %s; loop continues.",
                        msg.message_id,
                    )

            if await self._wait_or_stop(self.poll_interval_seconds):
                return

    async def _build_payload(self, msg: EmailMessage) -> dict[str, Any]:
        """The trigger's event payload: the message JSON, plus — when
        `download_dir` is set — its attachments spooled to disk and their
        local paths under `attachment_paths`. A single failed download is
        logged and skipped rather than sinking the whole message."""
        payload: dict[str, Any] = msg.model_dump(mode="json")
        if self.slim_payload:
            payload["body_html"] = None
            payload["headers"] = {}
        if self.download_dir is None or not msg.attachments:
            return payload
        target = Path(self.download_dir) / msg.message_id
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        paths: list[str] = []
        for i, att in enumerate(msg.attachments):
            # Flatten any path components a hostile filename might carry.
            name = Path(att.filename).name or f"attachment-{i}"
            dest = target / name
            try:
                data = await self.connector.download_attachment(msg.message_id, att.attachment_id)
                await asyncio.to_thread(dest.write_bytes, data)
                paths.append(str(dest))
            except Exception:
                logger.exception(
                    "Failed to download attachment %r from Gmail message %s; skipping it.",
                    att.filename,
                    msg.message_id,
                )
        payload["attachment_paths"] = paths
        return payload

    async def _wait_or_stop(self, seconds: float) -> bool:
        """Sleep up to `seconds` or until `stop()` is called. Returns True
        if stop was signalled (caller should exit the loop)."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False
