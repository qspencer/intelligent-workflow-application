"""Passive monitoring loop.

Background asyncio task that runs every `interval_seconds` and checks for
operational problems:

- Stuck workflows: instances that have been RUNNING for too long.
- High error rate: ratio of FAILED to terminal instances over a recent window.
- Queue depth: count of PENDING instances (trigger backlog).
- Token burn: total tokens consumed across recent agentic steps.

Each breach emits an `alert_*` audit entry and (if an EventBus is provided)
publishes the event so the dashboard sees it in real time.

Per BUILD_PLAN.md Week 9, this is *passive* — deterministic checks only. The
LLM-driven orchestrator (`docs/ARCHITECTURE.md` D1 active reasoning) lands when
there's enough running-workflow signal to make it useful.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from workflow_platform.events import EventBus
from workflow_platform.persistence import (
    AuditEntry,
    Repositories,
    StepExecutionState,
    WorkflowInstanceState,
)
from workflow_platform.persistence.models import _new_id

logger = logging.getLogger(__name__)


class MonitoringConfig(BaseModel):
    interval_seconds: float = 30.0

    stuck_threshold_seconds: float = 600.0  # alert RUNNING longer than 10 min
    error_rate_window_seconds: float = 600.0
    error_rate_threshold: float = 0.5
    error_rate_min_sample: int = 5  # require at least N terminal instances
    queue_depth_threshold: int = 100
    token_burn_window_seconds: float = 600.0
    token_burn_threshold: int = 1_000_000

    instance_sample_limit: int = 500
    step_sample_limit: int = 1000


class MonitoringService:
    def __init__(
        self,
        repositories: Repositories,
        *,
        events: EventBus | None = None,
        config: MonitoringConfig | None = None,
    ) -> None:
        self.repositories = repositories
        self.events = events
        self.config = config or MonitoringConfig()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        # Avoid spamming the same alert: remember which stuck instances we've
        # already alerted on (per process). Resets when a process restarts.
        self._alerted_stuck: set[str] = set()
        self._last_high_error_alert_at: datetime | None = None
        self._last_high_queue_alert_at: datetime | None = None
        self._last_high_burn_alert_at: datetime | None = None

    # --- lifecycle ---

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("Monitoring check failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.config.interval_seconds
                )
            except TimeoutError:
                continue

    # --- one-shot orchestration (also useful for tests) ---

    async def run_once(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC)
        alerts: list[dict[str, Any]] = []
        alerts.extend(await self._check_stuck_workflows(now))
        alerts.extend(await self._check_error_rate(now))
        alerts.extend(await self._check_queue_depth(now))
        alerts.extend(await self._check_token_burn(now))
        return alerts

    # --- checks ---

    async def _check_stuck_workflows(self, now: datetime) -> list[dict[str, Any]]:
        threshold = timedelta(seconds=self.config.stuck_threshold_seconds)
        recent = await self.repositories.instances.list_recent(
            limit=self.config.instance_sample_limit
        )
        emitted: list[dict[str, Any]] = []
        for instance in recent:
            if instance.state != WorkflowInstanceState.RUNNING:
                continue
            started = instance.started_at or instance.created_at
            if now - started < threshold:
                continue
            if instance.id in self._alerted_stuck:
                continue
            self._alerted_stuck.add(instance.id)
            detail = {
                "instance_id": instance.id,
                "workflow_id": instance.workflow_id,
                "running_for_seconds": (now - started).total_seconds(),
                "threshold_seconds": self.config.stuck_threshold_seconds,
            }
            await self._emit_alert("alert_stuck_workflow", detail, instance.id)
            emitted.append({"action": "alert_stuck_workflow", **detail})
        return emitted

    async def _check_error_rate(self, now: datetime) -> list[dict[str, Any]]:
        window = timedelta(seconds=self.config.error_rate_window_seconds)
        recent = await self.repositories.instances.list_recent(
            limit=self.config.instance_sample_limit, since=now - window
        )
        terminal = [
            i
            for i in recent
            if i.state
            in (
                WorkflowInstanceState.COMPLETED,
                WorkflowInstanceState.FAILED,
                WorkflowInstanceState.KILLED,
            )
        ]
        if len(terminal) < self.config.error_rate_min_sample:
            return []
        failed = sum(
            1
            for i in terminal
            if i.state in (WorkflowInstanceState.FAILED, WorkflowInstanceState.KILLED)
        )
        rate = failed / len(terminal)
        if rate < self.config.error_rate_threshold:
            self._last_high_error_alert_at = None
            return []
        if self._last_high_error_alert_at is not None and (
            now - self._last_high_error_alert_at < window
        ):
            return []
        self._last_high_error_alert_at = now
        detail = {
            "rate": round(rate, 4),
            "threshold": self.config.error_rate_threshold,
            "failed": failed,
            "total_terminal": len(terminal),
            "window_seconds": self.config.error_rate_window_seconds,
        }
        await self._emit_alert("alert_high_error_rate", detail, None)
        return [{"action": "alert_high_error_rate", **detail}]

    async def _check_queue_depth(self, now: datetime) -> list[dict[str, Any]]:
        recent = await self.repositories.instances.list_recent(
            limit=self.config.instance_sample_limit
        )
        pending = [i for i in recent if i.state == WorkflowInstanceState.PENDING]
        if len(pending) < self.config.queue_depth_threshold:
            self._last_high_queue_alert_at = None
            return []
        window = timedelta(seconds=self.config.error_rate_window_seconds)
        if self._last_high_queue_alert_at is not None and (
            now - self._last_high_queue_alert_at < window
        ):
            return []
        self._last_high_queue_alert_at = now
        detail = {
            "depth": len(pending),
            "threshold": self.config.queue_depth_threshold,
        }
        await self._emit_alert("alert_high_queue_depth", detail, None)
        return [{"action": "alert_high_queue_depth", **detail}]

    async def _check_token_burn(self, now: datetime) -> list[dict[str, Any]]:
        window = timedelta(seconds=self.config.token_burn_window_seconds)
        steps = await self.repositories.steps.list_recent(
            limit=self.config.step_sample_limit, since=now - window
        )
        total_tokens = 0
        total_cost = 0.0
        for s in steps:
            if s.state != StepExecutionState.COMPLETED:
                continue
            output = s.output or {}
            usage = output.get("usage") or {}
            total_tokens += int(usage.get("total_tokens", 0))
            total_cost += float(output.get("cost_usd", 0.0))
        if total_tokens < self.config.token_burn_threshold:
            self._last_high_burn_alert_at = None
            return []
        if self._last_high_burn_alert_at is not None and (
            now - self._last_high_burn_alert_at < window
        ):
            return []
        self._last_high_burn_alert_at = now
        detail = {
            "tokens": total_tokens,
            "cost_usd": round(total_cost, 4),
            "threshold_tokens": self.config.token_burn_threshold,
            "window_seconds": self.config.token_burn_window_seconds,
        }
        await self._emit_alert("alert_high_token_burn", detail, None)
        return [{"action": "alert_high_token_burn", **detail}]

    # --- alert emission ---

    async def _emit_alert(
        self, action: str, detail: dict[str, Any], instance_id: str | None
    ) -> None:
        entry = AuditEntry(
            id=_new_id(),
            actor_type="monitoring",
            actor_id="monitoring_service",
            action=action,
            workflow_instance_id=instance_id,
            detail=detail,
        )
        await self.repositories.audit.append(entry)
        if self.events is not None:
            await self.events.publish(entry.model_dump(mode="json"))
