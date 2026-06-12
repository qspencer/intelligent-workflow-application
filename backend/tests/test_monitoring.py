"""Tests for the passive MonitoringService."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from workflow_platform.events import EventBus
from workflow_platform.monitoring import MonitoringConfig, MonitoringService
from workflow_platform.persistence import (
    StepExecution,
    StepExecutionState,
    WorkflowInstance,
    WorkflowInstanceState,
    in_memory_repositories,
)


def _config(**overrides: Any) -> MonitoringConfig:
    base = {
        "interval_seconds": 0.05,
        "stuck_threshold_seconds": 60.0,
        "error_rate_window_seconds": 600.0,
        "error_rate_threshold": 0.5,
        "error_rate_min_sample": 3,
        "queue_depth_threshold": 5,
        "token_burn_window_seconds": 600.0,
        "token_burn_threshold": 1000,
    }
    base.update(overrides)
    return MonitoringConfig(**base)


# --- stuck workflows ---


async def test_stuck_workflow_alerts_once() -> None:
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    stuck = WorkflowInstance(
        workflow_id="wf",
        state=WorkflowInstanceState.RUNNING,
        created_at=now - timedelta(minutes=30),
        started_at=now - timedelta(minutes=30),
    )
    fresh = WorkflowInstance(
        workflow_id="wf",
        state=WorkflowInstanceState.RUNNING,
        created_at=now - timedelta(seconds=5),
        started_at=now - timedelta(seconds=5),
    )
    await repos.instances.create(stuck)
    await repos.instances.create(fresh)
    monitor = MonitoringService(repos, config=_config())

    alerts = await monitor.run_once(now=now)
    actions = [a["action"] for a in alerts]
    assert actions.count("alert_stuck_workflow") == 1
    assert alerts[0]["instance_id"] == stuck.id

    # Second run should NOT re-alert the same instance.
    alerts2 = await monitor.run_once(now=now)
    assert all(a["action"] != "alert_stuck_workflow" for a in alerts2)


# --- error rate ---


async def test_high_error_rate_alert_fires() -> None:
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    for _ in range(4):
        await repos.instances.create(
            WorkflowInstance(
                workflow_id="wf",
                state=WorkflowInstanceState.FAILED,
                created_at=now - timedelta(seconds=10),
            )
        )
    await repos.instances.create(
        WorkflowInstance(
            workflow_id="wf",
            state=WorkflowInstanceState.COMPLETED,
            created_at=now - timedelta(seconds=10),
        )
    )
    monitor = MonitoringService(repos, config=_config(error_rate_threshold=0.5))
    alerts = await monitor.run_once(now=now)
    matching = [a for a in alerts if a["action"] == "alert_high_error_rate"]
    assert len(matching) == 1
    assert matching[0]["failed"] == 4
    assert matching[0]["total_terminal"] == 5


async def test_error_rate_alert_skipped_below_min_sample() -> None:
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    await repos.instances.create(
        WorkflowInstance(
            workflow_id="wf",
            state=WorkflowInstanceState.FAILED,
            created_at=now - timedelta(seconds=10),
        )
    )
    monitor = MonitoringService(repos, config=_config(error_rate_min_sample=3))
    alerts = await monitor.run_once(now=now)
    assert all(a["action"] != "alert_high_error_rate" for a in alerts)


async def test_error_rate_ignores_dry_runs() -> None:
    """A designer hammering a broken draft via Test (C6.1) must not trip the
    production error-rate alert — dry-run instances are excluded."""
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    for _ in range(5):
        await repos.instances.create(
            WorkflowInstance(
                workflow_id="wf",
                state=WorkflowInstanceState.FAILED,
                context={"dry_run": True},
                created_at=now - timedelta(seconds=10),
            )
        )
    # Real traffic is healthy.
    for _ in range(5):
        await repos.instances.create(
            WorkflowInstance(
                workflow_id="wf",
                state=WorkflowInstanceState.COMPLETED,
                created_at=now - timedelta(seconds=10),
            )
        )
    monitor = MonitoringService(repos, config=_config(error_rate_threshold=0.5))
    alerts = await monitor.run_once(now=now)
    assert all(a["action"] != "alert_high_error_rate" for a in alerts)


# --- queue depth ---


async def test_queue_depth_alert() -> None:
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    for _ in range(6):
        await repos.instances.create(
            WorkflowInstance(workflow_id="wf", state=WorkflowInstanceState.PENDING)
        )
    monitor = MonitoringService(repos, config=_config(queue_depth_threshold=5))
    alerts = await monitor.run_once(now=now)
    matching = [a for a in alerts if a["action"] == "alert_high_queue_depth"]
    assert len(matching) == 1
    assert matching[0]["depth"] == 6


# --- token burn ---


async def test_high_token_burn_alert() -> None:
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    instance = WorkflowInstance(workflow_id="wf", state=WorkflowInstanceState.COMPLETED)
    await repos.instances.create(instance)
    await repos.steps.create(
        StepExecution(
            instance_id=instance.id,
            step_id="a",
            state=StepExecutionState.COMPLETED,
            output={"usage": {"total_tokens": 800_000}, "cost_usd": 0.1},
            started_at=now - timedelta(seconds=30),
        )
    )
    await repos.steps.create(
        StepExecution(
            instance_id=instance.id,
            step_id="b",
            state=StepExecutionState.COMPLETED,
            output={"usage": {"total_tokens": 500_000}, "cost_usd": 0.05},
            started_at=now - timedelta(seconds=20),
        )
    )
    monitor = MonitoringService(repos, config=_config(token_burn_threshold=1_000_000))
    alerts = await monitor.run_once(now=now)
    matching = [a for a in alerts if a["action"] == "alert_high_token_burn"]
    assert len(matching) == 1
    assert matching[0]["tokens"] == 1_300_000


# --- emit + event bus ---


async def test_alert_writes_audit_and_publishes_event() -> None:
    repos = in_memory_repositories()
    bus = EventBus()
    received: list[dict[str, Any]] = []

    queue = bus.subscribe()

    async def consume() -> None:
        # Drain whatever the test publishes.
        for _ in range(5):
            try:
                received.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

    now = datetime.now(UTC)
    stuck = WorkflowInstance(
        workflow_id="wf",
        state=WorkflowInstanceState.RUNNING,
        created_at=now - timedelta(minutes=30),
        started_at=now - timedelta(minutes=30),
    )
    await repos.instances.create(stuck)
    monitor = MonitoringService(repos, events=bus, config=_config())
    await monitor.run_once(now=now)
    await consume()

    audit = await repos.audit.list_recent()
    assert any(e.action == "alert_stuck_workflow" for e in audit)
    assert any(e.get("action") == "alert_stuck_workflow" for e in received)


# --- lifecycle ---


async def test_start_stop_runs_loop_at_least_once() -> None:
    repos = in_memory_repositories()
    monitor = MonitoringService(repos, config=_config(interval_seconds=0.05))
    await monitor.start()
    await asyncio.sleep(0.15)
    await monitor.stop()
    # No exceptions = success. The audit log will be empty (no breaches).


async def test_stop_is_idempotent() -> None:
    repos = in_memory_repositories()
    monitor = MonitoringService(repos)
    await monitor.stop()  # never started
    await monitor.start()
    await monitor.stop()
    await monitor.stop()  # second stop ok
