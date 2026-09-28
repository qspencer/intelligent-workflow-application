"""`AlertEmailNotifier` — alerts reach a human, not only the audit table.

Review R1 (2026-09-28): the classifier was offline three days before any
alert fired, and that alert was a row nobody read.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

from tests._email_fakes import FakeAuthProvider, FakeGmailService
from workflow_platform.audit_writer import AuditWriter
from workflow_platform.connectors.email import GmailConnector
from workflow_platform.events import EventBus
from workflow_platform.monitoring import AlertEmailNotifier
from workflow_platform.persistence import Repositories, in_memory_repositories

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)


def _notifier(
    svc: FakeGmailService | None = None, **kwargs: Any
) -> tuple[AlertEmailNotifier, FakeGmailService, Repositories]:
    svc = svc or FakeGmailService()
    repos = in_memory_repositories()
    connector = GmailConnector(
        account="tools@example.com", auth_provider=FakeAuthProvider(), service=svc
    )
    notifier = AlertEmailNotifier(
        events=EventBus(),
        connector=connector,
        to="operator@example.com",
        # Flip ON, as in production: the notification entries are
        # instance-less and must survive projection intact.
        audit_writer=AuditWriter(repos, trace_safe_only=True),
        **kwargs,
    )
    return notifier, svc, repos


def _event(
    action: str, workflow_id: str = "email-triage-apply", entry_id: str = "e1"
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "action": action,
        "timestamp": NOW.isoformat(),
        "workflow_instance_id": None,
        "detail": {"workflow_id": workflow_id, "trigger_type": "email"},
    }


def _sent(svc: FakeGmailService) -> list[str]:
    """Decoded raw MIME of every message the fake was asked to send."""
    out = []
    for name, kwargs in svc.calls:
        if name == "messages.send":
            raw = kwargs["body"]["raw"]
            out.append(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode())
    return out


async def test_an_alert_is_mailed_with_its_remedy_and_the_send_is_audited() -> None:
    notifier, svc, repos = _notifier()
    assert await notifier.handle(_event("alert_trigger_auth_revoked"), now=NOW) is True

    [mime] = _sent(svc)
    assert "operator@example.com" in mime
    assert "alert_trigger_auth_revoked" in mime
    assert "gmail_auth.py" in mime  # the operator is told what to do
    entries = await repos.audit.list_recent(limit=5)
    assert [(e.action, e.detail) for e in entries] == [
        (
            "notification_sent",
            {
                "alert_action": "alert_trigger_auth_revoked",
                "alert_entry_id": "e1",
                "channel": "email",
            },
        )
    ]


async def test_non_alerts_and_the_notifiers_own_entries_are_never_mailed() -> None:
    """`notification_*` rides the same bus; mailing it would be a loop."""
    notifier, svc, _ = _notifier()
    for action in ("step_completed", "notification_sent", "notification_failed", "alert_SYNTHETIC"):
        assert await notifier.handle(_event(action), now=NOW) is False
    assert _sent(svc) == []


async def test_the_same_alert_is_not_repeated_within_the_interval() -> None:
    notifier, svc, _ = _notifier(repeat_interval=timedelta(hours=6))
    assert await notifier.handle(_event("alert_stale_trigger"), now=NOW)
    assert not await notifier.handle(_event("alert_stale_trigger"), now=NOW + timedelta(hours=1))
    # A different subject is a different alert.
    assert await notifier.handle(
        _event("alert_stale_trigger", workflow_id="dmarc-ingest"), now=NOW + timedelta(hours=1)
    )
    assert await notifier.handle(_event("alert_stale_trigger"), now=NOW + timedelta(hours=7))
    assert len(_sent(svc)) == 3


async def test_a_flood_is_capped_per_hour() -> None:
    """The stuck-workflow check once wrote 26,462 alerts about 171 runs."""
    notifier, _, _ = _notifier(max_per_hour=3)
    mailed = [
        await notifier.handle(_event("alert_stuck_workflow", workflow_id=f"wf-{i}"), now=NOW)
        for i in range(10)
    ]
    assert mailed.count(True) == 3
    assert await notifier.handle(
        _event("alert_stuck_workflow", workflow_id="wf-late"), now=NOW + timedelta(minutes=61)
    )


async def test_a_failed_send_is_audited_without_error_text() -> None:
    notifier, _, repos = _notifier()

    async def boom(req: Any) -> str:
        raise RuntimeError("SYNTHETIC smtp failure with secret-looking text")

    notifier.connector.send_email = boom  # type: ignore[method-assign]
    assert await notifier.handle(_event("alert_trigger_auth_revoked"), now=NOW) is False
    [entry] = await repos.audit.list_recent(limit=5)
    assert entry.action == "notification_failed"
    assert "SYNTHETIC" not in str(entry.detail)
    # A failure does not count against the repeat interval: the next one retries.
    notifier.connector.send_email = GmailConnector.send_email.__get__(notifier.connector)  # type: ignore[method-assign]
    assert await notifier.handle(_event("alert_trigger_auth_revoked"), now=NOW)


async def test_the_notifier_consumes_the_bus_end_to_end() -> None:
    """Wired, not just callable: an entry appended through the chokepoint
    reaches the mailbox with no other call."""
    import asyncio

    notifier, svc, repos = _notifier()
    writer = AuditWriter(repos, events=notifier.events, trace_safe_only=True)
    await notifier.start()
    try:
        await writer.append(
            "alert_trigger_auth_revoked",
            actor_type="system",
            actor_id="trigger_orchestrator",
            detail={"workflow_id": "email-triage-apply", "trigger_type": "email"},
        )
        for _ in range(100):
            if _sent(svc):
                break
            await asyncio.sleep(0.01)
    finally:
        await notifier.stop()
    assert len(_sent(svc)) == 1


def test_main_builds_no_notifier_unless_a_recipient_is_configured(monkeypatch: Any) -> None:
    from workflow_platform.main import _build_alert_notifier
    from workflow_platform.secrets import EnvSecretStore

    monkeypatch.delenv("WORKFLOW_PLATFORM_ALERT_EMAIL_TO", raising=False)
    repos = in_memory_repositories()
    assert _build_alert_notifier(EventBus(), EnvSecretStore(), AuditWriter(repos)) is None
    # A recipient without a credentialed sender stays audit-only, with a warning.
    monkeypatch.setenv("WORKFLOW_PLATFORM_ALERT_EMAIL_TO", "operator@example.com")
    monkeypatch.delenv("WORKFLOW_PLATFORM_GMAIL_ACCOUNT", raising=False)
    assert _build_alert_notifier(EventBus(), EnvSecretStore(), AuditWriter(repos)) is None
