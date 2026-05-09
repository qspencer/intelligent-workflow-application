"""ScheduleTrigger — fires a workflow on a cron schedule (or fixed interval).

Two modes:
- `cron="0 9 * * *"` — full cron expression via `croniter`. Optional `timezone`
  string (POSIX). Default UTC.
- `interval_seconds=300` — simpler periodic shorthand. Either `cron` or
  `interval_seconds` must be set; not both.

Each fire calls the registered callback with `{"triggered_at": ISO datetime,
"schedule": <expression>}`.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta, timezone
from typing import ClassVar
from zoneinfo import ZoneInfo

from croniter import croniter

from workflow_platform.triggers.base import Trigger, TriggerCallback


class ScheduleTrigger(Trigger):
    type: ClassVar[str] = "schedule"

    def __init__(
        self,
        *,
        cron: str | None = None,
        interval_seconds: float | None = None,
        timezone_name: str | None = None,
    ) -> None:
        if (cron is None) == (interval_seconds is None):
            raise ValueError("ScheduleTrigger requires exactly one of `cron` or `interval_seconds`")
        self.cron = cron
        self.interval_seconds = interval_seconds
        self.tz: timezone | ZoneInfo = ZoneInfo(timezone_name) if timezone_name else UTC
        if cron is not None and not croniter.is_valid(cron):
            raise ValueError(f"Invalid cron expression: {cron!r}")
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self, on_event: TriggerCallback) -> None:
        if self._task is not None:
            return
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
            now = datetime.now(self.tz)
            next_fire = self._next_fire_after(now)
            wait = max(0.0, (next_fire - now).total_seconds())
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
                return  # stop was signalled
            except TimeoutError:
                pass
            payload = {
                "triggered_at": datetime.now(self.tz).isoformat(),
                "schedule": self.cron or f"every {self.interval_seconds}s",
            }
            try:
                await on_event(payload)
            except Exception:
                # Don't let a misbehaving callback kill the schedule loop.
                continue

    def _next_fire_after(self, now: datetime) -> datetime:
        if self.cron is not None:
            it = croniter(self.cron, now)
            return it.get_next(datetime)
        assert self.interval_seconds is not None
        return now + timedelta(seconds=self.interval_seconds)
