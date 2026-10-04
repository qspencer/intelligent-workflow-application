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
  interval so the failure doesn't tight-loop. The TRANSITIONS are hooks —
  `on_auth_revoked` fires once when polling first fails this way,
  `on_auth_restored` once when a poll next succeeds — so the orchestrator
  can alert on the edge rather than on every back-off. Before each retry
  `reload_credentials` runs, so re-running the consent CLI is enough to
  recover: until 2026-09-28 the fresh token sat on disk unread and the
  only cure was a restart the log line didn't mention.
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
import functools
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from workflow_platform.connectors.email.auth_results import authentication_pass
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
        annotate_reply_status: bool = False,
        annotate_auth_result: bool = False,
        mark_read_after_success: bool = False,
        body_max_chars: int | None = None,
        cursor_store: TriggerCursorRepo | None = None,
        cursor_key: str | None = None,
        on_auth_revoked: Callable[[], Awaitable[None]] | None = None,
        on_auth_restored: Callable[[float], Awaitable[None]] | None = None,
        reload_credentials: Callable[[], bool] | None = None,
        lookback_hours: float = 0.0,
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
        # TWO_AXIS §3: when set, each payload gains `reply_status` ∈
        # {replied, not_replied, unknown} via a metadata-only thread check —
        # one extra Gmail call per message, so opt-in per workflow. `unknown`
        # (lookup failure) suppresses awaiting-reply downstream rather than
        # manufacturing a response obligation; delivery is never blocked.
        self.annotate_reply_status = annotate_reply_status
        # CODIFY_PLAN §6: when set, payloads gain `auth_pass` — Gmail's own
        # Authentication-Results verdict (trusted-authserv policy). Computed
        # BEFORE slimming (slim_payload drops raw headers).
        self.annotate_auth_result = annotate_auth_result
        # Mutating: after a run COMPLETEs, drop UNREAD so the mailbox's unread
        # count becomes a real backlog signal. Only an explicit True from the
        # callback marks; a failed, unknown or errored run stays unread.
        self.mark_read_after_success = mark_read_after_success
        # Full-coverage mail includes very long newsletter bodies; a
        # classification prompt needs the head, not 30k tokens of it.
        self.body_max_chars = body_max_chars
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
        self.on_auth_revoked = on_auth_revoked
        self.on_auth_restored = on_auth_restored
        self.reload_credentials = reload_credentials
        # Re-ask for mail this far behind the cursor on every poll. For
        # senders whose messages arrive LATE carrying an EARLIER timestamp —
        # Google's DMARC reports are stamped with the report window's end
        # and delivered 1-4 days later, out of order (the 09-10 report
        # arrived after the 09-12 one). Without it, a report stamped before
        # the cursor is never listed again. The seen-id ring absorbs the
        # re-listing, so the window must hold fewer messages than the ring
        # (500): fine for a report mailbox, NOT for a busy inbox.
        self.lookback = timedelta(hours=lookback_hours)
        # Set on the first revoked poll, cleared by the next success: the edge
        # the hooks fire on. Per trigger, per process.
        self._auth_revoked_since: datetime | None = None
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
                    since=(self._cursor - self.lookback) if self._cursor else None,
                    label=self.label,
                    max_messages=self.max_messages,
                    query=self.query,
                    # The cursor below advances to the newest returned, so
                    # the batch must be the OLDEST pending, not the newest.
                    oldest_first=True,
                    # Before the cap: a look-back window full of processed
                    # mail must not crowd out the new message.
                    skip_ids=self._seen_ids,
                )
            except GmailAuthRevoked:
                logger.error(
                    "Gmail auth revoked for account %r — backing off %.0fs. "
                    "Re-run backend/tools/gmail_auth.py; the trigger picks up the "
                    "new token on its next retry.",
                    self.connector.account,
                    self.auth_revoked_backoff_seconds,
                )
                if self._auth_revoked_since is None:
                    self._auth_revoked_since = datetime.now(UTC)
                    await self._run_hook("on_auth_revoked", self.on_auth_revoked)
                if await self._wait_or_stop(self.auth_revoked_backoff_seconds):
                    return
                self._reload_credentials()
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

            if self._auth_revoked_since is not None:
                revoked_for = (datetime.now(UTC) - self._auth_revoked_since).total_seconds()
                self._auth_revoked_since = None
                logger.info(
                    "Gmail auth restored for account %r after %.0fs.",
                    self.connector.account,
                    revoked_for,
                )
                if self.on_auth_restored is not None:
                    await self._run_hook(
                        "on_auth_restored", functools.partial(self.on_auth_restored, revoked_for)
                    )

            if messages:
                # Never backwards: a late message stamped before the cursor
                # (the look-back case) must not rewind it.
                newest = max(m.received_at for m in messages)
                self._cursor = max(self._cursor, newest) if self._cursor else newest

            dispatched = False
            for msg in messages:
                if self._stop.is_set():
                    return
                if msg.message_id in self._seen_ids:
                    continue
                self._mark_seen(msg.message_id)
                dispatched = True
                try:
                    processed = await on_event(await self._build_payload(msg))
                except Exception:
                    logger.exception(
                        "Trigger callback failed for Gmail message %s; loop continues.",
                        msg.message_id,
                    )
                else:
                    if self.mark_read_after_success and processed is True:
                        await self._mark_read(msg.message_id)

            if dispatched:
                await self._persist_cursor()

            if await self._wait_or_stop(self.poll_interval_seconds):
                return

    async def _run_hook(self, name: str, hook: Callable[[], Awaitable[None]] | None) -> None:
        """An alert hook that fails must not stop the poller it reports on —
        the failure it describes would then be the only thing still running."""
        if hook is None:
            return
        try:
            await hook()
        except Exception:
            logger.exception("Gmail trigger %s hook failed; polling continues.", name)

    def _reload_credentials(self) -> None:
        if self.reload_credentials is None:
            return
        try:
            if self.reload_credentials():
                logger.info(
                    "Loaded a new Gmail refresh token for account %r; retrying now.",
                    self.connector.account,
                )
        except Exception:
            logger.exception("Reloading Gmail credentials failed; will retry.")

    async def _mark_read(self, message_id: str) -> None:
        """Drop Gmail's UNREAD label after the workflow processed the message.

        Best-effort by design: the report is already delivered, so a failure
        here must not fail the run or stop the loop — it only leaves the
        message unread, which is the safe direction (it will be re-marked on a
        later successful run, and `remove_labels` is idempotent)."""
        try:
            await self.connector.remove_labels(message_id, ["UNREAD"])
        except Exception:
            logger.exception("Could not mark Gmail message %s read; it stays unread.", message_id)

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
        if self.annotate_auth_result:
            payload["auth_pass"] = authentication_pass(msg.auth_results, msg.from_address.address)
        payload.pop("auth_results", None)
        body_text = payload.get("body_text")
        if (
            self.body_max_chars
            and isinstance(body_text, str)
            and len(body_text) > self.body_max_chars
        ):
            payload["body_text"] = (
                body_text[: self.body_max_chars] + "\n…[body truncated for triage]"
            )
        if self.annotate_reply_status:
            try:
                if msg.thread_id is None:
                    payload["reply_status"] = "not_replied"  # single-message, no thread
                else:
                    replied = await self.connector.thread_has_newer_sent_message(
                        msg.thread_id, msg.received_at
                    )
                    payload["reply_status"] = "replied" if replied else "not_replied"
            except Exception:
                logger.warning(
                    "Thread reply-check failed for message %s; reply_status=unknown.",
                    msg.message_id,
                )
                payload["reply_status"] = "unknown"
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
