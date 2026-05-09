"""Tests for ScheduleTrigger."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from workflow_platform.triggers import ScheduleTrigger


def test_requires_exactly_one_of_cron_or_interval() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ScheduleTrigger()
    with pytest.raises(ValueError, match="exactly one"):
        ScheduleTrigger(cron="* * * * *", interval_seconds=1)


def test_invalid_cron_raises() -> None:
    with pytest.raises(ValueError, match="Invalid cron"):
        ScheduleTrigger(cron="not a cron")


def test_next_fire_after_interval() -> None:
    trigger = ScheduleTrigger(interval_seconds=60)
    now = datetime(2026, 5, 9, 10, 0, 0, tzinfo=UTC)
    next_fire = trigger._next_fire_after(now)
    assert (next_fire - now).total_seconds() == 60


def test_next_fire_after_cron() -> None:
    trigger = ScheduleTrigger(cron="0 12 * * *", timezone_name="UTC")
    now = datetime(2026, 5, 9, 10, 0, 0, tzinfo=UTC)
    next_fire = trigger._next_fire_after(now)
    assert next_fire.hour == 12
    assert next_fire.minute == 0
    # Same day if 'now' is before noon, next day otherwise.
    assert next_fire.date() == now.date()


async def test_interval_trigger_fires_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[dict[str, Any]] = []

    async def on_event(payload: dict[str, Any]) -> None:
        received.append(payload)

    trigger = ScheduleTrigger(interval_seconds=0.05)
    await trigger.start(on_event)
    try:
        # Wait long enough for at least one fire.
        await asyncio.sleep(0.18)
    finally:
        await trigger.stop()

    assert len(received) >= 1
    assert "triggered_at" in received[0]
    assert received[0]["schedule"] == "every 0.05s"


async def test_stop_is_idempotent() -> None:
    trigger = ScheduleTrigger(interval_seconds=60)
    await trigger.stop()  # never started

    async def cb(_: dict[str, Any]) -> None:
        return None

    await trigger.start(cb)
    await trigger.stop()
    await trigger.stop()


async def test_callback_exception_does_not_kill_loop() -> None:
    calls = {"n": 0}

    async def flaky(payload: dict[str, Any]) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first call fails")

    trigger = ScheduleTrigger(interval_seconds=0.05)
    await trigger.start(flaky)
    try:
        await asyncio.sleep(0.20)
    finally:
        await trigger.stop()

    # The loop survived the first failure and called again.
    assert calls["n"] >= 2
