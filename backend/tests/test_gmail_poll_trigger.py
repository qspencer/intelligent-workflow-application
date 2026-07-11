"""Tests for `GmailPollTrigger` — wraps a GmailConnector in the Trigger
plugin shape and fires `on_event` once per new message.

Uses the shared `_email_fakes` to keep all real-network paths out.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from tests._email_fakes import (
    FakeAuthProvider,
    FakeGmailService,
    stage_gmail_message,
)
from workflow_platform.connectors.email import GmailConnector
from workflow_platform.connectors.email.gmail_auth import (
    GmailAuthMisconfigured,
    GmailAuthRevoked,
)
from workflow_platform.triggers import GmailPollTrigger


def _make_trigger(
    svc: FakeGmailService | None = None,
    *,
    poll_interval_seconds: float = 0.05,
    auth_revoked_backoff_seconds: float = 0.05,
    label: str | None = "INBOX",
) -> tuple[GmailPollTrigger, FakeGmailService]:
    svc = svc or FakeGmailService()
    conn = GmailConnector(
        account="intelligent.workflow.engine@quentinspencer.com",
        auth_provider=FakeAuthProvider(),
        service=svc,
    )
    trig = GmailPollTrigger(
        connector=conn,
        poll_interval_seconds=poll_interval_seconds,
        label=label,
        auth_revoked_backoff_seconds=auth_revoked_backoff_seconds,
    )
    # Pre-set cursor to a far-past epoch so staged messages aren't filtered
    # by Gmail's `after:` query — the fake doesn't honor `since` but the
    # connector still constructs the query, and we want the test to observe
    # the dispatched messages.
    trig._cursor = datetime(2000, 1, 1, tzinfo=UTC)
    return trig, svc


async def _wait_for(predicate: Any, timeout: float = 2.0) -> None:
    """Spin until `predicate()` is truthy or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("predicate never became truthy")


# ---------- happy path ----------


async def test_fires_on_each_message_in_first_poll() -> None:
    svc = FakeGmailService()
    svc.list_response = {"messages": [{"id": "m-1"}, {"id": "m-2"}]}
    svc.get_responses["m-1"] = stage_gmail_message("m-1", subject="One")
    svc.get_responses["m-2"] = stage_gmail_message("m-2", subject="Two")

    fired: list[dict[str, Any]] = []

    async def on_event(payload: dict[str, Any]) -> None:
        fired.append(payload)

    trig, _ = _make_trigger(svc)
    await trig.start(on_event)
    try:
        await _wait_for(lambda: len(fired) >= 2)
    finally:
        await trig.stop()

    subjects = sorted(p["subject"] for p in fired)
    assert subjects == ["One", "Two"]
    # Each payload is a full EmailMessage dict — the agent's trigger payload.
    assert fired[0]["provider"] == "gmail"
    assert fired[0]["message_id"] in {"m-1", "m-2"}


async def test_message_fires_exactly_once_across_polls() -> None:
    """Regression: Gmail's `after:` is second-granular and inclusive, so a
    message stamped exactly at the cursor re-matches on every poll — the
    live-validation run processed one newsletter 11 times. The id dedupe
    must make each message fire exactly once no matter how many polls
    re-return it."""
    svc = FakeGmailService()
    # Static list response: every poll returns the same message — exactly
    # what the cursor boundary does in production.
    svc.list_response = {"messages": [{"id": "m-dup"}]}
    svc.get_responses["m-dup"] = stage_gmail_message("m-dup", subject="Once only")

    fired: list[dict[str, Any]] = []

    async def on_event(payload: dict[str, Any]) -> None:
        fired.append(payload)

    trig, _ = _make_trigger(svc)  # 0.05s poll interval
    await trig.start(on_event)
    try:
        await _wait_for(lambda: len(fired) >= 1)
        # Let several more poll cycles run; the same message keeps coming back.
        await asyncio.sleep(0.3)
    finally:
        await trig.stop()

    assert len(fired) == 1
    assert fired[0]["subject"] == "Once only"


async def test_slim_payload_drops_html_and_headers() -> None:
    """slim_payload strips body_html + raw headers — the bulk that made a
    triage agent burn ~40k input tokens on a newsletter — while keeping
    body_text and everything else intact."""
    svc = FakeGmailService()
    svc.list_response = {"messages": [{"id": "m-slim"}]}
    svc.get_responses["m-slim"] = stage_gmail_message(
        "m-slim",
        subject="Weekly digest",
        body_text="plain text body",
        body_html="<html>" + "x" * 5000 + "</html>",
    )

    fired: list[dict[str, Any]] = []

    async def on_event(payload: dict[str, Any]) -> None:
        fired.append(payload)

    trig, _ = _make_trigger(svc)
    trig.slim_payload = True
    await trig.start(on_event)
    try:
        await _wait_for(lambda: len(fired) >= 1)
    finally:
        await trig.stop()

    payload = fired[0]
    assert payload["body_html"] is None
    assert payload["headers"] == {}
    assert payload["body_text"] == "plain text body"
    assert payload["subject"] == "Weekly digest"


async def test_download_dir_spools_attachments_onto_payload(tmp_path: Any) -> None:
    """With `download_dir` set, the trigger downloads each attachment before
    firing and the payload carries their local paths (`attachment_paths`) —
    deterministic steps can't reach the connector, so the trigger delivers
    files, not ids."""
    import base64

    svc = FakeGmailService()
    svc.list_response = {"messages": [{"id": "m-dmarc"}]}
    svc.get_responses["m-dmarc"] = stage_gmail_message(
        "m-dmarc",
        subject="Report domain: example.com",
        attachments=[
            {"filename": "report.zip", "attachmentId": "att-1", "size": 3},
            # A hostile filename must be flattened, never written outside the spool.
            {"filename": "../../evil.zip", "attachmentId": "att-2", "size": 3},
        ],
    )
    for att_id, content in (("att-1", b"one"), ("att-2", b"two")):
        svc.attachment_responses[("m-dmarc", att_id)] = {
            "data": base64.urlsafe_b64encode(content).decode("ascii")
        }

    fired: list[dict[str, Any]] = []

    async def on_event(payload: dict[str, Any]) -> None:
        fired.append(payload)

    trig, _ = _make_trigger(svc)
    trig.download_dir = str(tmp_path)
    await trig.start(on_event)
    try:
        await _wait_for(lambda: len(fired) >= 1)
    finally:
        await trig.stop()

    paths = fired[0]["attachment_paths"]
    assert len(paths) == 2
    assert all(p.startswith(str(tmp_path)) for p in paths)
    assert (tmp_path / "m-dmarc" / "report.zip").read_bytes() == b"one"
    # Flattened: written as evil.zip inside the message dir, not up the tree.
    assert (tmp_path / "m-dmarc" / "evil.zip").read_bytes() == b"two"
    assert not (tmp_path.parent / "evil.zip").exists()


async def test_advances_cursor_so_second_poll_uses_after_query() -> None:
    svc = FakeGmailService()
    # Two distinct internal dates: 1000ms then 2000ms.
    svc.get_responses["m-1"] = stage_gmail_message("m-1", internal_ms=1_000)
    svc.list_response = {"messages": [{"id": "m-1"}]}

    fired: list[dict[str, Any]] = []

    async def on_event(payload: dict[str, Any]) -> None:
        fired.append(payload)
        # After the first message fires, drain the inbox so subsequent polls
        # return empty (and we can observe the new `after:` query).
        svc.list_response = {"messages": []}

    trig, _ = _make_trigger(svc, poll_interval_seconds=0.02)
    await trig.start(on_event)
    try:
        # Wait for at least 2 list calls total (the first one with messages,
        # and at least one subsequent empty poll using the advanced cursor).
        await _wait_for(lambda: sum(1 for c in svc.calls if c[0] == "messages.list") >= 2)
    finally:
        await trig.stop()

    list_calls = [kw for (m, kw) in svc.calls if m == "messages.list"]
    # The second poll's `q` should carry `after:<epoch>` derived from the
    # advanced cursor (which is the received_at of m-1: 2025-05-25T10:00:00Z = 1748169600).
    # m-1's internal_ms is overridden to 1000, so received_at = 1.0s after epoch.
    assert "after:1 " in list_calls[1]["q"] + " " or list_calls[1]["q"].startswith("after:1 ")


async def test_stop_cancels_loop_cleanly() -> None:
    svc = FakeGmailService()
    svc.list_response = {"messages": []}
    fired: list[Any] = []

    async def on_event(payload: dict[str, Any]) -> None:
        fired.append(payload)

    trig, _ = _make_trigger(svc, poll_interval_seconds=0.02)
    await trig.start(on_event)
    await asyncio.sleep(0.05)
    await trig.stop()
    # Snapshot the call count, wait a beat, ensure no further polling.
    count_after_stop = sum(1 for c in svc.calls if c[0] == "messages.list")
    await asyncio.sleep(0.1)
    assert sum(1 for c in svc.calls if c[0] == "messages.list") == count_after_stop


async def test_start_is_idempotent() -> None:
    svc = FakeGmailService()
    svc.list_response = {"messages": []}

    async def on_event(payload: dict[str, Any]) -> None:
        pass

    trig, _ = _make_trigger(svc, poll_interval_seconds=0.5)
    await trig.start(on_event)
    task = trig._task
    await trig.start(on_event)  # second call should be a no-op
    assert trig._task is task
    await trig.stop()


# ---------- failure modes ----------


async def test_callback_exception_does_not_kill_loop(caplog: pytest.LogCaptureFixture) -> None:
    """A misbehaving callback for one message must not stop the trigger."""
    svc = FakeGmailService()
    svc.list_response = {"messages": [{"id": "m-1"}, {"id": "m-2"}]}
    svc.get_responses["m-1"] = stage_gmail_message("m-1", internal_ms=1_000)
    svc.get_responses["m-2"] = stage_gmail_message("m-2", internal_ms=2_000)

    seen: list[str] = []

    async def on_event(payload: dict[str, Any]) -> None:
        seen.append(payload["message_id"])
        if payload["message_id"] == "m-1":
            raise RuntimeError("simulated callback failure")

    caplog.set_level(logging.ERROR)
    trig, _ = _make_trigger(svc)
    await trig.start(on_event)
    try:
        await _wait_for(lambda: "m-2" in seen)
    finally:
        await trig.stop()

    # Both messages dispatched even though m-1 raised.
    assert seen == ["m-1", "m-2"]
    assert any("callback failed" in rec.message for rec in caplog.records)


async def test_auth_revoked_logs_and_backs_off(caplog: pytest.LogCaptureFixture) -> None:
    """When refresh token dies, the loop logs ERROR and keeps polling
    (no tight loop, no callback fires)."""

    class _AuthRevokedService:
        """Minimal service that always raises HttpError-like as if 401 — but
        we wrap the auth-revoked at the poll_inbox level instead via the
        connector's auth_provider. Easier: monkeypatch poll_inbox."""

    svc = FakeGmailService()
    svc.list_response = {"messages": []}
    trig, _ = _make_trigger(svc, auth_revoked_backoff_seconds=0.05)

    # Monkeypatch poll_inbox to raise GmailAuthRevoked the first time,
    # then succeed (empty) — verifies the backoff completes and polling resumes.
    call_count = 0
    original_poll = trig.connector.poll_inbox

    async def flaky_poll(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise GmailAuthRevoked("revoked")
        return await original_poll(*args, **kwargs)

    trig.connector.poll_inbox = flaky_poll  # type: ignore[method-assign]

    fired: list[Any] = []

    async def on_event(payload: dict[str, Any]) -> None:
        fired.append(payload)

    caplog.set_level(logging.ERROR)
    await trig.start(on_event)
    try:
        await _wait_for(lambda: call_count >= 2)
    finally:
        await trig.stop()

    assert any("auth revoked" in rec.message.lower() for rec in caplog.records)
    assert fired == []  # nothing dispatched — list was empty after backoff


async def test_generic_poll_error_logs_and_retries(caplog: pytest.LogCaptureFixture) -> None:
    """Random poll failures (network blips, 500s) should log and continue."""
    svc = FakeGmailService()
    svc.list_response = {"messages": []}
    trig, _ = _make_trigger(svc)

    call_count = 0
    original_poll = trig.connector.poll_inbox

    async def flaky_poll(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("network blip")
        return await original_poll(*args, **kwargs)

    trig.connector.poll_inbox = flaky_poll  # type: ignore[method-assign]

    async def on_event(payload: dict[str, Any]) -> None:
        pass

    caplog.set_level(logging.ERROR)
    await trig.start(on_event)
    try:
        await _wait_for(lambda: call_count >= 2)
    finally:
        await trig.stop()

    assert any("Gmail poll failed" in rec.message for rec in caplog.records)


async def test_misconfigured_auth_logs_once_and_stops(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A permanent config error (missing credentials) logs a single WARNING
    and stops the loop — it must not retry or dump a traceback every interval.
    This is the common dev case: a bundled example ships a `gmail_poll` trigger
    but Gmail isn't configured locally."""
    trig, _ = _make_trigger()

    call_count = 0

    async def broken_poll(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        raise GmailAuthMisconfigured("Client credentials not in SecretStore.")

    trig.connector.poll_inbox = broken_poll  # type: ignore[method-assign]

    async def on_event(payload: dict[str, Any]) -> None:
        pass

    caplog.set_level(logging.WARNING)
    await trig.start(on_event)
    try:
        await _wait_for(lambda: call_count >= 1)
        # Wait several poll intervals (0.05s each); a retrying loop would call
        # again. The misconfig handler should have stopped it after one call.
        await asyncio.sleep(0.2)
    finally:
        await trig.stop()

    assert call_count == 1, "loop should stop on misconfiguration, not retry"
    assert any(
        rec.levelno == logging.WARNING and "disabled" in rec.message for rec in caplog.records
    )
    # Did NOT fall through to the generic retry-with-traceback handler.
    assert not any("Gmail poll failed" in rec.message for rec in caplog.records)


# ---------- constructor validation ----------


def test_construct_rejects_zero_interval() -> None:
    svc = FakeGmailService()
    conn = GmailConnector(account="a@b.com", auth_provider=FakeAuthProvider(), service=svc)
    with pytest.raises(ValueError, match="poll_interval_seconds"):
        GmailPollTrigger(connector=conn, poll_interval_seconds=0.0)


def test_type_attribute() -> None:
    """The `type` class attribute must be `gmail_poll` so the orchestrator
    can dispatch on it from YAML."""
    assert GmailPollTrigger.type == "gmail_poll"
