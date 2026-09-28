"""Deliver `alert_*` audit entries to a human, by email.

Until 2026-09-28 every alert the platform raised was an audit row, and an
audit row is read by whoever happens to open the audit page. The email
classifier was offline for 3 days before `alert_stale_trigger` fired, and
nobody read that either — the operator found it during a performance
review. An alert nobody receives is a log line with extra steps.

Delivery goes through the TOOLS account (`WORKFLOW_PLATFORM_GMAIL_ACCOUNT`),
a Workspace mailbox on an Internal consent screen with no 7-day clock, so
the alert about a mailbox's consent dying cannot be carried by that
mailbox. Opt-in: nothing is sent unless `WORKFLOW_PLATFORM_ALERT_EMAIL_TO`
is set.

Every send is audited (`notification_sent` / `notification_failed`) —
outbound mail is an outward-facing effect, and the audit log is where
effects are recorded. Neither entry carries error text: they are
instance-less, so they must be projection-lossless; the exception goes to
the log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from workflow_platform.audit_writer import AuditWriter
from workflow_platform.connectors.email import EmailAddress, EmailSendRequest, GmailConnector
from workflow_platform.events import EventBus

logger = logging.getLogger(__name__)

#: Every alert the platform raises, and so every action this delivers. Closed
#: on purpose — the `notification_*` entries record which alert they carried,
#: and that field is validated against this set. A test pins it to the
#: `alert_*` actions in the projection registry, so a new alert that is not
#: delivered fails the build instead of going quietly unheard.
ALERT_ACTIONS: tuple[str, ...] = (
    "alert_abandoned_pause",
    "alert_high_error_rate",
    "alert_high_queue_depth",
    "alert_high_token_burn",
    "alert_stale_trigger",
    "alert_stuck_workflow",
    "alert_trigger_auth_revoked",
    "alert_trigger_consent_expiring",
)

NOTIFICATION_CHANNELS: tuple[str, ...] = ("email",)

_REMEDIATION: dict[str, str] = {
    "alert_trigger_auth_revoked": (
        "The mailbox's OAuth consent is gone and the trigger has stopped reading "
        "mail. Re-run `uv run python tools/gmail_auth.py --account <account>` for "
        "the account in this workflow's trigger config; the trigger picks the new "
        "token up on its next retry, no restart needed."
    ),
    "alert_trigger_consent_expiring": (
        "Google's Testing-status clock will revoke this mailbox's consent at "
        "`expires_at`. Re-run `uv run python tools/gmail_auth.py --account "
        "<account>` before then to reset the 7 days."
    ),
    "alert_stale_trigger": (
        "This email trigger has dispatched nothing for longer than the threshold. "
        "Check auth first (`alert_trigger_auth_revoked`), then the trigger's query "
        "and label filters."
    ),
}


class AlertEmailNotifier:
    """Subscribe to the event bus; email each alert, rate-limited.

    Two limits, because the failure being guarded against is a flood — the
    stuck-workflow check once wrote 26,462 alerts about the same 171 runs:

    - the same (action, subject) at most once per `repeat_interval`;
    - at most `max_per_hour` emails in total, whatever they are.

    Anything past a limit is still in the audit log; it is only not mailed.
    """

    def __init__(
        self,
        *,
        events: EventBus,
        connector: GmailConnector,
        to: str,
        audit_writer: AuditWriter,
        repeat_interval: timedelta = timedelta(hours=6),
        max_per_hour: int = 12,
    ) -> None:
        self.events = events
        self.connector = connector
        self.to = to
        self.audit_writer = audit_writer
        self.repeat_interval = repeat_interval
        self.max_per_hour = max_per_hour
        self._last_sent: dict[tuple[str, str], datetime] = {}
        self._recent: deque[datetime] = deque()
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._queue = self.events.subscribe()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(BaseException):
            await self._task
        self._task = None
        if self._queue is not None:
            self.events.unsubscribe(self._queue)
            self._queue = None

    async def _loop(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            try:
                await self.handle(event)
            except Exception:
                logger.exception("Alert notifier failed on an event; continuing.")

    async def handle(self, event: dict[str, Any], *, now: datetime | None = None) -> bool:
        """Deliver one bus event if it is an alert within the limits.
        Returns whether an email was sent. Public for tests."""
        action = event.get("action")
        if action not in ALERT_ACTIONS:
            return False
        now = now or datetime.now(UTC)
        detail = event.get("detail") or {}
        subject_key = str(
            detail.get("workflow_id")
            or detail.get("instance_id")
            or event.get("workflow_instance_id")
            or ""
        )
        last = self._last_sent.get((action, subject_key))
        if last is not None and now - last < self.repeat_interval:
            return False
        while self._recent and now - self._recent[0] > timedelta(hours=1):
            self._recent.popleft()
        if len(self._recent) >= self.max_per_hour:
            logger.warning(
                "Alert email cap (%d/h) reached; %s not mailed.", self.max_per_hour, action
            )
            return False

        entry_id = str(event.get("id") or "")
        try:
            await self.connector.send_email(self._compose(action, subject_key, event))
        except Exception:
            logger.exception("Emailing alert %s failed.", action)
            await self._record("notification_failed", action, entry_id)
            return False
        self._last_sent[(action, subject_key)] = now
        self._recent.append(now)
        await self._record("notification_sent", action, entry_id)
        return True

    def _compose(self, action: str, subject_key: str, event: dict[str, Any]) -> EmailSendRequest:
        detail = event.get("detail") or {}
        lines = [
            f"Alert: {action}",
            f"At: {event.get('timestamp', '')}",
            f"Audit entry: {event.get('id', '')}",
            "",
        ]
        remediation = _REMEDIATION.get(action)
        if remediation:
            lines += [remediation, ""]
        # The STORED detail: already projected at rest, so it holds nothing a
        # viewer of the audit page could not read.
        lines += [f"  {k}: {v}" for k, v in sorted(detail.items())]
        suffix = f" — {subject_key}" if subject_key else ""
        return EmailSendRequest(
            to=[EmailAddress(address=self.to)],
            subject=f"[workflow-platform] {action}{suffix}",
            body_text="\n".join(lines),
        )

    async def _record(self, action: str, alert_action: str, entry_id: str) -> None:
        try:
            await self.audit_writer.append(
                action,
                actor_type="system",
                actor_id="alert_notifier",
                detail={
                    "alert_action": alert_action,
                    "alert_entry_id": entry_id,
                    "channel": "email",
                },
            )
        except Exception:
            logger.exception("Could not audit %s for %s.", action, alert_action)
