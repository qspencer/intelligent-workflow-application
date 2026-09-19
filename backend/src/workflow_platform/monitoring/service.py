"""Passive monitoring loop.

Background asyncio task that runs every `interval_seconds` and checks for
operational problems:

- Stuck workflows: instances that have been RUNNING for too long.
- Abandoned pauses: instances PAUSED for too long with nobody resuming.
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

from workflow_platform.audit_writer import AuditWriter, InstanceLessRawAudit
from workflow_platform.events import EventBus
from workflow_platform.persistence import (
    Repositories,
    StepExecutionState,
    WorkflowInstanceState,
)
from workflow_platform.trace_flip import trace_safe_only_from_env

logger = logging.getLogger(__name__)


class MonitoringConfig(BaseModel):
    interval_seconds: float = 30.0

    stuck_threshold_seconds: float = 600.0  # alert RUNNING longer than 10 min
    # PAUSED is a legitimate state — a budget pause, an operator pause, or an
    # interrupted run the engine parked for resume — so the signal is not that
    # an instance is paused but that NOBODY CAME BACK for it.
    #
    # This check exists because fixing one blindness created another. Until
    # 2026-09-19 an interrupted run was stranded RUNNING, which was wrong but
    # LOUD: `alert_stuck_workflow` fired on it forever (26,462 rows, the
    # largest action in the audit log). Marking those runs PAUSED made the
    # state correct and the signal disappear — the five existing checks watch
    # RUNNING and PENDING, and nothing watched PAUSED. A correct-but-silent
    # state is how the original problem survived two months.
    #
    # 3 hours: long enough that a deliberate pause-and-investigate is not
    # nagged, short enough that a day of accumulation cannot pass unseen
    # (`alert_stale_trigger`'s 3 days is the right scale for "is the poller
    # blind", not for "did an operator forget a run").
    abandoned_pause_threshold_seconds: float = 10_800.0
    error_rate_window_seconds: float = 600.0
    error_rate_threshold: float = 0.5
    error_rate_min_sample: int = 5  # require at least N terminal instances
    queue_depth_threshold: int = 100
    token_burn_window_seconds: float = 600.0
    token_burn_threshold: int = 1_000_000

    # A registered email trigger that dispatches nothing for this long is
    # indistinguishable from a broken one (the 2026-07-30 dmarc-ingest
    # lesson: a mailbox filter blinded the poller for ten days with zero
    # errors). 3 days catches it while tolerating quiet weekends.
    stale_trigger_threshold_seconds: float = 259_200.0

    instance_sample_limit: int = 500
    step_sample_limit: int = 1000


class MonitoringService:
    def __init__(
        self,
        repositories: Repositories,
        *,
        events: EventBus | None = None,
        config: MonitoringConfig | None = None,
        audit_writer: AuditWriter | None = None,
    ) -> None:
        self.repositories = repositories
        self.events = events
        self.config = config or MonitoringConfig()
        # Alerts go through the SHARED chokepoint, not
        # `repositories.audit.append`. Until 2026-09-19 this service was the
        # one continuous writer outside it, so its details were stored
        # unprojected while the read path redacted them — see
        # `audit_writer` module docstring. Injectable so a caller that
        # already has a writer (and therefore a vault and a cipher) shares
        # it; built here otherwise, because the service is constructed in
        # places that have no engine.
        self._audit = audit_writer or AuditWriter(
            repositories,
            events=events,
            trace_safe_only=trace_safe_only_from_env(),
        )
        self._task: asyncio.Task[None] | None = None
        # Created lazily in start(): create_app() is sync and may run under a
        # different (or no) event loop than the app's lifespan — an Event
        # constructed here binds wrong and stop() raises "bound to a
        # different event loop" (seen in the schemathesis job, which builds
        # several apps per process).
        self._stop_event: asyncio.Event | None = None
        # Avoid spamming the same alert: remember which stuck instances we've
        # already alerted on (per process). Resets when a process restarts.
        self._alerted_stuck: set[str] = set()
        self._alerted_stale: set[str] = set()
        self._alerted_paused: set[str] = set()
        self._last_high_error_alert_at: datetime | None = None
        self._last_high_queue_alert_at: datetime | None = None
        self._last_high_burn_alert_at: datetime | None = None

    # --- lifecycle ---

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except TimeoutError:
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        assert self._stop_event is not None
        stop_event = self._stop_event
        while not stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("Monitoring check failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.config.interval_seconds)
            except TimeoutError:
                continue

    # --- one-shot orchestration (also useful for tests) ---

    async def run_once(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC)
        alerts: list[dict[str, Any]] = []
        alerts.extend(await self._check_stuck_workflows(now))
        alerts.extend(await self._check_abandoned_pauses(now))
        alerts.extend(await self._check_error_rate(now))
        alerts.extend(await self._check_queue_depth(now))
        alerts.extend(await self._check_token_burn(now))
        alerts.extend(await self._check_stale_email_triggers(now))
        return alerts

    # --- checks ---

    async def _check_abandoned_pauses(self, now: datetime) -> list[dict[str, Any]]:
        """A PAUSED instance nobody resumed. One alert per instance per process.

        Queries by STATE rather than by recency: an abandoned pause is old by
        definition, and `list_recent`'s window is exactly what would hide it.
        Oldest first, so a backlog is reported worst-first.

        "How long paused" has no column — `_mark_instance` stamps
        `completed_at` only for COMPLETED/FAILED. The last step row is the
        closest honest answer, so the age is measured from the newest step
        activity, falling back to the run's own start when a run was
        interrupted before any step persisted (58 of the 2026-09-19 orphans
        were exactly that: an instance row and nothing else).
        """
        threshold = timedelta(seconds=self.config.abandoned_pause_threshold_seconds)
        paused = await self.repositories.instances.list_by_state(
            [WorkflowInstanceState.PAUSED.value], limit=self.config.instance_sample_limit
        )
        emitted: list[dict[str, Any]] = []
        for instance in paused:
            if instance.id in self._alerted_paused:
                continue
            started = instance.started_at or instance.created_at
            # Short-circuit before touching the steps table: an instance
            # cannot have been paused longer than it has existed.
            if now - started < threshold:
                continue
            steps = await self.repositories.steps.list_by_instance(instance.id)
            stamps = [s.completed_at or s.started_at for s in steps]
            last_activity = max([t for t in stamps if t is not None], default=started)
            if now - last_activity < threshold:
                continue
            self._alerted_paused.add(instance.id)
            detail = {
                "instance_id": instance.id,
                "workflow_id": instance.workflow_id,
                "paused_for_seconds": (now - last_activity).total_seconds(),
                "threshold_seconds": self.config.abandoned_pause_threshold_seconds,
            }
            # NOT the instance's `error` string: on an interrupted run it is
            # engine-authored, but on a budget pause or a failure-then-retry
            # it can carry step error text. The action plus the instance id is
            # enough to find the run; the reason is one click away.
            await self._emit_alert("alert_abandoned_pause", detail, instance.id)
            emitted.append({"action": "alert_abandoned_pause", **detail})
        return emitted

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

    async def _check_stale_email_triggers(self, now: datetime) -> list[dict[str, Any]]:
        """Silent-blindness detector: an email-triggered workflow whose newest
        run is older than the threshold (or that has no runs at all) gets one
        `alert_stale_trigger` per process. Polls that error already log; this
        catches the poller that runs CLEAN against the wrong view — a label
        filter, a bad query, a deleted history."""
        threshold = timedelta(seconds=self.config.stale_trigger_threshold_seconds)
        emitted: list[dict[str, Any]] = []
        for definition in await self.repositories.definitions.list_all():
            if definition.trigger.type not in ("email", "gmail_poll"):
                continue
            if definition.id in self._alerted_stale:
                continue
            instances = await self.repositories.instances.list_by_workflow(definition.id)
            newest = max((i.started_at or i.created_at for i in instances), default=None)
            if newest is not None and now - newest < threshold:
                continue
            self._alerted_stale.add(definition.id)
            # NO `account`. It is the polled mailbox address, so the v12
            # ownership registry classifies it withheld — and this alert has
            # no instance, so there is nowhere to vault what projection would
            # take. Emitting it wrote a live mailbox address into
            # `audit_log.detail` in plaintext while the read path correctly
            # redacted it (1,432 production rows; found 2026-09-19).
            #
            # Dropping it costs nothing: `account` is a field of the workflow
            # DEFINITION that `workflow_id` names, readable under the same
            # authorization. That is the reviewer's Q1 rule — visibility
            # elsewhere suffices for the same authoritative value under
            # equivalent authorization — and here it holds exactly, because
            # the definition is the authority and this alert was only ever
            # copying it.
            detail = {
                "workflow_id": definition.id,
                "trigger_type": definition.trigger.type,
                "last_run_at": newest.isoformat() if newest else None,
                "threshold_seconds": self.config.stale_trigger_threshold_seconds,
            }
            await self._emit_alert("alert_stale_trigger", detail, None)
            emitted.append({"action": "alert_stale_trigger", **detail})
        return emitted

    async def _check_error_rate(self, now: datetime) -> list[dict[str, Any]]:
        window = timedelta(seconds=self.config.error_rate_window_seconds)
        recent = await self.repositories.instances.list_recent(
            limit=self.config.instance_sample_limit, since=now - window
        )
        # Dry runs (C6.1) are interactive experiments — a designer testing a
        # broken draft shouldn't trip the production error-rate alert.
        terminal = [
            i
            for i in recent
            if i.state
            in (
                WorkflowInstanceState.COMPLETED,
                WorkflowInstanceState.FAILED,
                WorkflowInstanceState.KILLED,
            )
            and not (i.context or {}).get("dry_run")
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
        """Emit one alert through the shared audit chokepoint.

        Four of the five alerts are instance-less, and the vault is
        instance-scoped, so the writer REFUSES an instance-less detail that
        projection would strip. That refusal must not stop monitoring —
        losing the stuck-workflow detector because one alert is
        misconstructed trades a small problem for a large one — so it is
        caught and logged. The entry is NOT written: an alert whose raw
        cannot be vaulted is exactly the one that must not be stored
        half-projected, with the removed half gone and nothing recording
        that it existed.

        `exc_info` and the action name make it a fix-the-detail bug report
        rather than a mystery; `test_an_instance_less_alert_carrying_raw_is_
        refused_not_projected_away` pins the behaviour.
        """
        self._audit.events = self.events
        try:
            await self._audit.append(
                action,
                actor_type="monitoring",
                actor_id="monitoring_service",
                instance_id=instance_id,
                detail=detail,
            )
        except InstanceLessRawAudit:
            logger.error(
                "alert %s carries raw with no instance to vault it against; "
                "the entry was NOT written. Its detail must be made "
                "projection-lossless (see workflow_platform.audit_writer).",
                action,
                exc_info=True,
            )
