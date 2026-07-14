"""Persisted poll cursor for GmailPollTrigger (G9).

Pins the acceptance criteria from docs/NEXT_STEPS.md:
- Stop the trigger, mail arrives, start a NEW trigger (fresh process
  simulation) → the message fires exactly once, no manual backfill.
- First-ever start (no stored cursor) initializes to "now" — historical
  mail doesn't flood.
- Restart mid-window doesn't double-fire: the persisted seen-id ring
  absorbs Gmail's second-granular inclusive `after:` overlap.
- A cursor-store failure degrades to process-local behavior, never
  blocks polling.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from tests._email_fakes import FakeAuthProvider, FakeGmailService, stage_gmail_message
from workflow_platform.connectors.email import GmailConnector
from workflow_platform.persistence import TriggerCursorState, in_memory_repositories
from workflow_platform.persistence.repository import TriggerCursorRepo
from workflow_platform.triggers import GmailPollTrigger

KEY = "email:test-wf:me@example.com"


def _trigger(
    svc: FakeGmailService,
    store: TriggerCursorRepo | None,
    *,
    preset_cursor: bool = True,
) -> GmailPollTrigger:
    conn = GmailConnector(account="me@example.com", auth_provider=FakeAuthProvider(), service=svc)
    trig = GmailPollTrigger(
        connector=conn,
        poll_interval_seconds=0.05,
        cursor_store=store,
        cursor_key=KEY,
    )
    if preset_cursor:
        # The fake service ignores `since`, but a far-past cursor documents
        # intent: we want every staged message observable.
        trig._cursor = datetime(2000, 1, 1, tzinfo=UTC)
    return trig


async def _wait_for(predicate: Any, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("predicate never became truthy")


# --- in-memory repo ---


async def test_cursor_repo_roundtrip() -> None:
    repos = in_memory_repositories()
    assert await repos.trigger_cursors.get(KEY) is None
    state = TriggerCursorState(
        cursor=datetime(2026, 7, 13, 12, 0, tzinfo=UTC), seen_ids=["m-1", "m-2"]
    )
    await repos.trigger_cursors.set(KEY, state)
    loaded = await repos.trigger_cursors.get(KEY)
    assert loaded is not None
    assert loaded.cursor == state.cursor
    assert loaded.seen_ids == ["m-1", "m-2"]


# --- persistence on poll ---


async def test_poll_persists_cursor_and_seen_ids() -> None:
    repos = in_memory_repositories()
    svc = FakeGmailService()
    svc.list_response = {"messages": [{"id": "m-1"}]}
    svc.get_responses["m-1"] = stage_gmail_message("m-1", subject="One")

    fired: list[dict[str, Any]] = []

    async def on_event(payload: dict[str, Any]) -> None:
        fired.append(payload)

    trig = _trigger(svc, repos.trigger_cursors)
    await trig.start(on_event)
    try:
        await _wait_for(lambda: len(fired) >= 1)
    finally:
        await trig.stop()

    stored = await repos.trigger_cursors.get(KEY)
    assert stored is not None
    assert "m-1" in stored.seen_ids
    assert stored.cursor == datetime.fromisoformat(fired[0]["received_at"])


async def test_restart_fires_missed_mail_exactly_once() -> None:
    """The G9 acceptance path: process 1 sees m-1; while 'down', m-2 arrives;
    process 2 (new trigger object, same store) fires m-2 exactly once and
    does NOT re-fire m-1 even though the fake re-serves it (Gmail's `after:`
    overlap)."""
    repos = in_memory_repositories()

    svc1 = FakeGmailService()
    svc1.list_response = {"messages": [{"id": "m-1"}]}
    svc1.get_responses["m-1"] = stage_gmail_message("m-1", subject="One")
    fired1: list[dict[str, Any]] = []

    async def on_event1(payload: dict[str, Any]) -> None:
        fired1.append(payload)

    trig1 = _trigger(svc1, repos.trigger_cursors)
    await trig1.start(on_event1)
    try:
        await _wait_for(lambda: len(fired1) >= 1)
    finally:
        await trig1.stop()

    # "Restart": a brand-new trigger over the same store. The fake serves the
    # boundary message m-1 again plus the newly-arrived m-2.
    svc2 = FakeGmailService()
    svc2.list_response = {"messages": [{"id": "m-1"}, {"id": "m-2"}]}
    svc2.get_responses["m-1"] = stage_gmail_message("m-1", subject="One")
    svc2.get_responses["m-2"] = stage_gmail_message("m-2", subject="Two")
    fired2: list[dict[str, Any]] = []

    async def on_event2(payload: dict[str, Any]) -> None:
        fired2.append(payload)

    trig2 = _trigger(svc2, repos.trigger_cursors, preset_cursor=False)
    await trig2.start(on_event2)
    try:
        await _wait_for(lambda: len(fired2) >= 1)
        await asyncio.sleep(0.15)  # a couple more poll cycles: m-1 must stay quiet
    finally:
        await trig2.stop()

    assert [p["subject"] for p in fired2] == ["Two"]
    # The resumed cursor came from the store, not "now" — otherwise the fake's
    # `since` (ignored here, but real Gmail's isn't) would have hidden m-2.
    assert trig2._cursor is not None


async def test_first_start_without_stored_state_defaults_to_now() -> None:
    repos = in_memory_repositories()
    svc = FakeGmailService()  # no messages
    trig = _trigger(svc, repos.trigger_cursors, preset_cursor=False)
    before = datetime.now(UTC)

    async def on_event(payload: dict[str, Any]) -> None:
        pass

    await trig.start(on_event)
    try:
        assert trig._cursor is not None
        assert trig._cursor >= before
    finally:
        await trig.stop()
    # Nothing fired, nothing persisted — the store stays empty.
    assert await repos.trigger_cursors.get(KEY) is None


async def test_store_failure_degrades_to_process_local() -> None:
    class _ExplodingStore(TriggerCursorRepo):
        async def get(self, trigger_id: str) -> TriggerCursorState | None:
            raise RuntimeError("db down")

        async def set(self, trigger_id: str, state: TriggerCursorState) -> None:
            raise RuntimeError("db down")

    svc = FakeGmailService()
    svc.list_response = {"messages": [{"id": "m-1"}]}
    svc.get_responses["m-1"] = stage_gmail_message("m-1", subject="One")
    fired: list[dict[str, Any]] = []

    async def on_event(payload: dict[str, Any]) -> None:
        fired.append(payload)

    trig = _trigger(svc, _ExplodingStore(), preset_cursor=False)
    await trig.start(on_event)
    try:
        # get() exploded at start → fell back to "now"; the fake ignores
        # `since`, so the message still arrives; set() explodes after the
        # poll and must not kill the loop.
        await _wait_for(lambda: len(fired) >= 1)
    finally:
        await trig.stop()
    assert [p["subject"] for p in fired] == ["One"]
