"""GmailPollTrigger — fires a workflow on new Gmail messages.

Wraps a `GmailConnector` in the `Trigger` plugin shape: starts a background
asyncio task that polls Gmail every `poll_interval_seconds`, invoking
`on_event` once per new message with that message's `EmailMessage` JSON.

Cursor semantics: on `start()`, the cursor initializes from the persisted
`TriggerCursorState` when a `cursor_store` + `cursor_key` are wired (G9),
falling back to "now" so historical mail does *not* flood the engine on a
true first start. Each poll advances the cursor to the latest received_at
seen and persists cursor + the seen-id ring, so a restart picks up where
the previous process left off: mail that arrived while the daemon was down
fires on the first poll, and the persisted ids absorb the boundary overlap
(Gmail's `after:` is second-granular and inclusive, so the last processed
message always re-matches). Without a store the cursor is process-local,
as before.

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
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from workflow_platform.connectors.email.gmail import GmailConnector
from workflow_platform.connectors.email.gmail_auth import (
    GmailAuthMisconfigured,
    GmailAuthRevoked,
)
from workflow_platform.connectors.email.models import EmailMessage
from workflow_platform.persistence import TriggerCursorRepo, TriggerCursorState
from workflow_platform.triggers.base import Trigger, TriggerCallback

logger = logging.getLogger(__name__)


def summarize_html_structure(html: str, *, max_items: int = 8, max_chars: int = 600) -> str:
    """A bounded, fetch-free summary of an HTML body's structure: title,
    link domains, image count, and image alt texts. Gives the triage agent
    signal on image-only marketing mail without ever requesting a remote
    resource (alt texts and domains are still third-party-authored text —
    same trust level as the body they stand in for)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    parts: list[str] = []
    title = soup.title.get_text(strip=True) if soup.title else ""
    if title:
        parts.append(f"title: {title}")
    domains: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if href.startswith(("http://", "https://")):
            domain = href.split("/", 3)[2].lower().removeprefix("www.")
            if domain and domain not in domains:
                domains.append(domain)
    if domains:
        shown = ", ".join(domains[:max_items])
        more = f" (+{len(domains) - max_items} more)" if len(domains) > max_items else ""
        parts.append(f"link domains: {shown}{more}")
    images = soup.find_all("img")
    if images:
        parts.append(f"images: {len(images)}")
        alts: list[str] = []
        for img in images:
            alt = str(img.get("alt") or "").strip()
            if alt and alt not in alts:
                alts.append(alt)
        if alts:
            shown = "; ".join(alts[:max_items])
            parts.append(f"image alt texts: {shown}")
    text = soup.get_text(" ", strip=True)
    if text:
        parts.append(f"visible text: {text[:200]}")
    summary = " | ".join(parts)
    return summary[:max_chars] if summary else "(no structure extracted)"


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
        cursor_store: TriggerCursorRepo | None = None,
        cursor_key: str | None = None,
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
        # G9: when both are set, the poll position survives restarts. The key
        # names the trigger identity (workflow + account) so a workflow
        # re-pointed at a different mailbox starts fresh instead of inheriting
        # a stale cursor.
        self.cursor_store = cursor_store
        self.cursor_key = cursor_key
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._cursor: datetime | None = None
        # Dedupe: Gmail's `after:` is second-granular and inclusive (and we
        # truncate with int()), so a message stamped exactly at the cursor
        # re-matches on every poll. Track recently-fired ids so each message
        # fires exactly once. Bounded ring so memory stays flat.
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=500)

    async def start(self, on_event: TriggerCallback) -> None:
        if self._task is not None:
            return
        # Initialize from the persisted state (G9) so a restart resumes where
        # the previous process stopped; fall back to "now" so historical mail
        # doesn't flood on a true first start. Tests can pre-set `_cursor`
        # before calling start. A store failure degrades to the old
        # process-local behavior instead of blocking startup.
        if self._cursor is None and self.cursor_store is not None and self.cursor_key:
            try:
                state = await self.cursor_store.get(self.cursor_key)
            except Exception:
                logger.exception(
                    "Failed to load poll cursor %r; starting from now.", self.cursor_key
                )
                state = None
            if state is not None:
                self._cursor = state.cursor
                for message_id in state.seen_ids:
                    self._mark_seen(message_id)
                logger.info(
                    "Resuming Gmail poll %r from persisted cursor %s (%d seen ids).",
                    self.cursor_key,
                    state.cursor.isoformat(),
                    len(state.seen_ids),
                )
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

            dispatched = False
            for msg in messages:
                if self._stop.is_set():
                    return
                if msg.message_id in self._seen_ids:
                    continue
                self._mark_seen(msg.message_id)
                dispatched = True
                try:
                    await on_event(await self._build_payload(msg))
                except Exception:
                    logger.exception(
                        "Trigger callback failed for Gmail message %s; loop continues.",
                        msg.message_id,
                    )

            if dispatched:
                await self._persist_cursor()

            if await self._wait_or_stop(self.poll_interval_seconds):
                return

    async def _persist_cursor(self) -> None:
        """Best-effort write of the poll position (G9). A persistence failure
        never interrupts polling — the trigger just degrades to process-local
        cursor semantics until the store recovers."""
        if self.cursor_store is None or not self.cursor_key or self._cursor is None:
            return
        try:
            await self.cursor_store.set(
                self.cursor_key,
                TriggerCursorState(cursor=self._cursor, seen_ids=list(self._seen_order)),
            )
        except Exception:
            logger.exception("Failed to persist poll cursor %r; continuing.", self.cursor_key)

    def _mark_seen(self, message_id: str) -> None:
        if len(self._seen_order) == self._seen_order.maxlen:
            self._seen_ids.discard(self._seen_order[0])
        self._seen_order.append(message_id)
        self._seen_ids.add(message_id)

    async def _build_payload(self, msg: EmailMessage) -> dict[str, Any]:
        """The trigger's event payload: the message JSON, plus — when
        `download_dir` is set — its attachments spooled to disk and their
        local paths under `attachment_paths`. A single failed download is
        logged and skipped rather than sinking the whole message."""
        payload: dict[str, Any] = msg.model_dump(mode="json")
        # Image-only mail has no text part: before (possibly) discarding the
        # HTML, derive a safe structural summary — link domains, image count,
        # alt texts — so the triage agent isn't blind on it. Derivation is
        # pure parsing: nothing is fetched, so no tracking pixels fire and no
        # remote content reaches the model.
        if not str(payload.get("body_text") or "").strip() and msg.body_html:
            payload["body_structure"] = summarize_html_structure(msg.body_html)
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
