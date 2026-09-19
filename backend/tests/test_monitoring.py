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


# --- alert_stale_trigger (2026-07-30, the dmarc silent-blindness lesson) ---


def _email_definition(defn_id: str, trigger_type: str) -> Any:
    from workflow_platform.workflow import WorkflowDefinition

    return WorkflowDefinition.model_validate(
        {
            "id": defn_id,
            "name": defn_id,
            "trigger": {"type": trigger_type, "config": {"account": "a@b.c"}},
            "steps": [
                {
                    "id": "s1",
                    "name": "s1",
                    "type": "agentic",
                    "goal": "g",
                    "model": "claude-haiku-4-5",
                }
            ],
            "edges": [],
        }
    )


async def test_stale_email_trigger_alerts_once() -> None:
    repos = in_memory_repositories()
    service = MonitoringService(repos, config=MonitoringConfig())
    now = datetime.now(UTC)

    stale = _email_definition("stale-mail", "email")
    fresh = _email_definition("fresh-mail", "email")
    never = _email_definition("never-ran", "email")
    webhook = _email_definition("hook", "webhook")
    for d in (stale, fresh, never, webhook):
        await repos.definitions.save(d)

    await repos.instances.create(
        WorkflowInstance(
            workflow_id="stale-mail",
            state=WorkflowInstanceState.COMPLETED,
            started_at=now - timedelta(days=5),
        )
    )
    await repos.instances.create(
        WorkflowInstance(
            workflow_id="fresh-mail",
            state=WorkflowInstanceState.COMPLETED,
            started_at=now - timedelta(hours=2),
        )
    )

    alerts = await service.run_once(now=now)
    stale_alerts = [a for a in alerts if a["action"] == "alert_stale_trigger"]
    assert {a["workflow_id"] for a in stale_alerts} == {"stale-mail", "never-ran"}
    never_alert = next(a for a in stale_alerts if a["workflow_id"] == "never-ran")
    assert never_alert["last_run_at"] is None

    # Once per process: a second sweep stays quiet.
    again = await service.run_once(now=now)
    assert not [a for a in again if a["action"] == "alert_stale_trigger"]

    entries = await repos.audit.list_recent(limit=20)
    assert sum(1 for e in entries if e.action == "alert_stale_trigger") == 2


# --- the audit chokepoint (2026-09-19) -------------------------------------
#
# The service was the one continuous audit writer outside the engine's
# projection-and-vault path. The v12 ownership registry made the consequence
# legible: it classified `alert_stale_trigger.account` withheld, the read
# path honoured that, and the stored row still held the live mailbox address
# in plaintext. These pin the fix — and, more usefully, pin the RULE that
# replaces widening the vault to an org-level space.


async def test_an_instance_less_alert_carries_nothing_projection_would_strip() -> None:
    """THE invariant, stated over the emitters rather than over one field.

    Four of the five alerts have no instance, and the vault is
    instance-scoped, so there is nowhere to put raw. Instead of growing a
    second vault space for them, the rule is that their details must be
    projection-lossless. This asserts it for every alert the service can
    actually emit, so a future alert that adds a raw field fails here rather
    than in production.
    """
    from workflow_platform.trace_projection import project_audit_detail_at_rest

    repos = in_memory_repositories()
    now = datetime.now(UTC)
    # Every check firing at once: stuck + stale + error rate + queue + burn.
    for _ in range(6):
        await repos.instances.create(
            WorkflowInstance(
                workflow_id="wf",
                state=WorkflowInstanceState.PENDING,
                created_at=now,
            )
        )
    await repos.instances.create(
        WorkflowInstance(
            workflow_id="wf",
            state=WorkflowInstanceState.RUNNING,
            created_at=now - timedelta(minutes=30),
            started_at=now - timedelta(minutes=30),
        )
    )
    for state in (WorkflowInstanceState.FAILED,) * 3:
        await repos.instances.create(
            WorkflowInstance(workflow_id="wf", state=state, created_at=now, completed_at=now)
        )
    await repos.definitions.save(_email_definition("stale-mail", "email"))

    service = MonitoringService(repos, config=_config())
    await service.run_once(now=now)

    entries = await repos.audit.list_recent(limit=50)
    alerts = [e for e in entries if e.action.startswith("alert_")]
    assert alerts, "no alerts fired — the fixture no longer exercises the checks"
    for entry in alerts:
        if entry.workflow_instance_id is not None:
            continue
        projected = project_audit_detail_at_rest(entry.action, entry.detail)
        assert projected == entry.detail, (
            f"{entry.action} is instance-less and carries "
            f"{sorted(set(entry.detail) - set(projected))} that projection removes. "
            "There is no instance to vault it against, so the detail must not "
            "carry it — see workflow_platform.audit_writer."
        )


async def test_the_stale_trigger_alert_does_not_carry_the_mailbox_address() -> None:
    """The specific field this closed. `account` is a property of the
    workflow DEFINITION that `workflow_id` names, readable under the same
    authorization, so the alert names the workflow and stops there."""
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    await repos.definitions.save(_email_definition("stale-mail", "email"))
    service = MonitoringService(repos, config=_config())
    alerts = await service.run_once(now=now)

    stale = next(a for a in alerts if a["action"] == "alert_stale_trigger")
    assert "account" not in stale
    assert stale["workflow_id"] == "stale-mail"
    entries = await repos.audit.list_recent(limit=20)
    stored = next(e for e in entries if e.action == "alert_stale_trigger")
    assert "a@b.c" not in str(stored.detail)


async def test_an_instance_less_alert_carrying_raw_is_refused_not_projected_away(
    monkeypatch: Any,
) -> None:
    """The fail-closed half, seen to fail (rule R-b).

    If a future alert does carry raw with no instance, the writer refuses
    and the entry is not written. The alternative — storing it projected —
    destroys the raw with nothing recording that it existed, which is the
    one outcome the whole vault design exists to prevent. Monitoring itself
    survives: one bad alert may not take the stuck-workflow detector with
    it.
    """
    from workflow_platform.audit_writer import AuditWriter

    repos = in_memory_repositories()
    service = MonitoringService(
        repos,
        config=_config(),
        audit_writer=AuditWriter(repos, trace_safe_only=True),
    )

    # `error` is raw by taint under every schema version.
    await service._emit_alert("alert_stuck_workflow", {"error": "SYNTHETIC exception text"}, None)

    entries = await repos.audit.list_recent(limit=10)
    assert not entries, "the entry was written despite its raw being unvaultable"

    # And the loop keeps working afterwards.
    await service._emit_alert("alert_high_queue_depth", {"depth": 7, "threshold": 5}, None)
    entries = await repos.audit.list_recent(limit=10)
    assert [e.action for e in entries] == ["alert_high_queue_depth"]


async def test_an_alert_WITH_an_instance_vaults_its_raw() -> None:
    """The other branch: an instance-scoped alert that carries raw is
    vaulted, not refused. The instance-less rule is a consequence of where
    the vault is addressed, not a blanket ban on raw in alerts."""
    from workflow_platform.audit_writer import AuditWriter

    repos = in_memory_repositories()
    instance = await repos.instances.create(
        WorkflowInstance(workflow_id="wf", state=WorkflowInstanceState.RUNNING)
    )
    service = MonitoringService(
        repos,
        config=_config(),
        audit_writer=AuditWriter(repos, trace_safe_only=True),
    )
    await service._emit_alert(
        "alert_stuck_workflow", {"error": "SYNTHETIC exception text"}, instance.id
    )

    entries = await repos.audit.list_recent(limit=10)
    assert len(entries) == 1
    assert "SYNTHETIC" not in str(entries[0].detail), "raw was stored inline"
    assert entries[0].projector_version is not None, "no vault pointer on the entry"
    rows = await repos.raw_trace_vault.list_by_instance(instance.id)
    assert any("SYNTHETIC" in str(r.payload) for r in rows), "the raw was not vaulted"
