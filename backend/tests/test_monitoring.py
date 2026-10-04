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


# --- abandoned pauses (2026-09-19) -----------------------------------------
#
# Fixing one blindness created another. Until today an interrupted run was
# stranded RUNNING — wrong, but LOUD: `alert_stuck_workflow` fired on it
# forever. Marking those runs PAUSED made the state correct and the signal
# vanish, because the five existing checks watch RUNNING and PENDING and
# nothing watched PAUSED. These pin the replacement signal.


async def _paused(repos: Any, *, age_hours: float, with_step: bool = True) -> WorkflowInstance:
    now = datetime.now(UTC)
    at = now - timedelta(hours=age_hours)
    instance: WorkflowInstance = await repos.instances.create(
        WorkflowInstance(
            workflow_id="wf",
            state=WorkflowInstanceState.PAUSED,
            created_at=at,
            started_at=at,
        )
    )
    if with_step:
        await repos.steps.create(
            StepExecution(
                instance_id=instance.id,
                step_id="s1",
                attempt=1,
                state=StepExecutionState.CANCELLED,
                started_at=at,
                completed_at=at,
            )
        )
    return instance


async def test_a_pause_nobody_resumed_alerts_after_the_threshold() -> None:
    repos = in_memory_repositories()
    old = await _paused(repos, age_hours=4)
    service = MonitoringService(repos, config=_config())

    alerts = [a for a in await service.run_once() if a["action"] == "alert_abandoned_pause"]
    assert len(alerts) == 1
    assert alerts[0]["instance_id"] == old.id
    assert alerts[0]["paused_for_seconds"] > 3 * 3600
    assert alerts[0]["threshold_seconds"] == 10_800.0


async def test_a_recent_pause_is_not_an_abandoned_one() -> None:
    """PAUSED is a legitimate state. The signal is that nobody came back,
    not that someone paused."""
    repos = in_memory_repositories()
    await _paused(repos, age_hours=1)
    service = MonitoringService(repos, config=_config())
    assert not [a for a in await service.run_once() if a["action"] == "alert_abandoned_pause"]


async def test_an_abandoned_pause_alerts_once_per_process() -> None:
    repos = in_memory_repositories()
    await _paused(repos, age_hours=4)
    service = MonitoringService(repos, config=_config())

    assert [a for a in await service.run_once() if a["action"] == "alert_abandoned_pause"]
    again = [a for a in await service.run_once() if a["action"] == "alert_abandoned_pause"]
    assert not again, "the same abandoned pause alerted twice in one process"


async def test_an_instance_interrupted_before_any_step_persisted_still_alerts() -> None:
    """58 of the 2026-09-19 orphans had an instance row and no step rows at
    all — the accept-before-persist window. Measuring the age from the last
    step would skip exactly those."""
    repos = in_memory_repositories()
    bare = await _paused(repos, age_hours=4, with_step=False)
    service = MonitoringService(repos, config=_config())

    alerts = [a for a in await service.run_once() if a["action"] == "alert_abandoned_pause"]
    assert [a["instance_id"] for a in alerts] == [bare.id]


async def test_the_age_is_measured_from_the_last_STEP_not_the_run_start() -> None:
    """A long run that paused a minute ago is not abandoned. `started_at`
    alone would call it one — the run has existed for hours."""
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    instance = await repos.instances.create(
        WorkflowInstance(
            workflow_id="wf",
            state=WorkflowInstanceState.PAUSED,
            created_at=now - timedelta(hours=9),
            started_at=now - timedelta(hours=9),
        )
    )
    await repos.steps.create(
        StepExecution(
            instance_id=instance.id,
            step_id="s1",
            attempt=1,
            state=StepExecutionState.COMPLETED,
            started_at=now - timedelta(minutes=2),
            completed_at=now - timedelta(minutes=1),
        )
    )
    service = MonitoringService(repos, config=_config())
    assert not [a for a in await service.run_once() if a["action"] == "alert_abandoned_pause"]


async def test_the_alert_does_not_carry_the_instance_error_text() -> None:
    """The error string is engine-authored on an interrupted run, but on a
    budget pause or failure-then-retry it can carry step error text."""
    repos = in_memory_repositories()
    instance = await _paused(repos, age_hours=4)
    instance.error = "SYNTHETIC raw step failure text"
    await repos.instances.update(instance)

    service = MonitoringService(repos, config=_config())
    await service.run_once()
    entries = await repos.audit.list_recent(limit=20)
    stored = next(e for e in entries if e.action == "alert_abandoned_pause")
    assert "SYNTHETIC" not in str(stored.detail)


async def test_a_resumed_old_run_is_not_immediately_stuck() -> None:
    """`_check_stuck_workflows` measured from `started_at`, and resume does
    not reset it — so both 2026-09-19 resumes alerted eleven seconds in, on
    runs that had just started working again. Age comes from the last step
    activity instead."""
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    old_start = now - timedelta(hours=6)
    instance = await repos.instances.create(
        WorkflowInstance(
            workflow_id="wf",
            state=WorkflowInstanceState.RUNNING,
            created_at=old_start,
            started_at=old_start,
        )
    )
    # The interrupted attempt, plus the freshly-started resumed one.
    await repos.steps.create(
        StepExecution(
            instance_id=instance.id,
            step_id="s1",
            attempt=1,
            state=StepExecutionState.CANCELLED,
            started_at=old_start,
            completed_at=old_start,
        )
    )
    await repos.steps.create(
        StepExecution(
            instance_id=instance.id,
            step_id="s1",
            attempt=2,
            state=StepExecutionState.RUNNING,
            started_at=now - timedelta(seconds=11),
        )
    )
    service = MonitoringService(repos, config=_config())
    assert not [a for a in await service.run_once() if a["action"] == "alert_stuck_workflow"]


async def test_a_genuinely_stuck_run_still_alerts() -> None:
    """The counterpart. A run whose in-flight step started hours ago is
    stuck by the same measure, so the change loses no detection."""
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    old = now - timedelta(hours=6)
    instance = await repos.instances.create(
        WorkflowInstance(
            workflow_id="wf",
            state=WorkflowInstanceState.RUNNING,
            created_at=old,
            started_at=old,
        )
    )
    await repos.steps.create(
        StepExecution(
            instance_id=instance.id,
            step_id="s1",
            attempt=1,
            state=StepExecutionState.RUNNING,
            started_at=old,
        )
    )
    service = MonitoringService(repos, config=_config())
    alerts = [a for a in await service.run_once() if a["action"] == "alert_stuck_workflow"]
    assert len(alerts) == 1
    assert alerts[0]["running_for_seconds"] > 5 * 3600


# --- alert_trigger_consent_expiring (review R1, 2026-09-28) ---


async def test_consent_expiry_warns_once_per_consent_inside_the_window() -> None:
    """Google's Testing clock stopped the classifier four times, and each
    time the outage was the only notice. Warn inside the window, once per
    consent; a re-consent moves the expiry and re-arms it. Run with the flip
    ON (production): the entry is instance-less, so a detail projection
    would strip is refused rather than stored."""
    from workflow_platform.audit_writer import AuditWriter

    repos = in_memory_repositories()
    now = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    expiry = {"a@b.c": now + timedelta(hours=20)}
    service = MonitoringService(
        repos,
        config=MonitoringConfig(),
        audit_writer=AuditWriter(repos, trace_safe_only=True),
        consent_expires_at=lambda account: expiry.get(account),
    )
    await repos.definitions.save(_email_definition("mail", "email"))
    await repos.definitions.save(_email_definition("hook", "webhook"))

    first = [a for a in await service.run_once(now=now) if "consent" in a["action"]]
    assert [a["workflow_id"] for a in first] == ["mail"]
    assert not [a for a in await service.run_once(now=now) if "consent" in a["action"]]

    entries = [e for e in await repos.audit.list_recent(limit=50) if "consent" in e.action]
    assert len(entries) == 1
    assert entries[0].detail == {
        "workflow_id": "mail",
        "trigger_type": "email",
        "expires_at": (now + timedelta(hours=20)).isoformat(),
        "warn_before_seconds": 86_400.0,
    }
    assert "a@b.c" not in str(entries[0].detail)

    expiry["a@b.c"] = now + timedelta(days=7, hours=-2)  # re-consented
    later = now + timedelta(days=6)
    rearmed = [a for a in await service.run_once(now=later) if "consent" in a["action"]]
    assert [a["workflow_id"] for a in rearmed] == ["mail"]


async def test_consent_outside_the_window_or_without_a_clock_is_quiet() -> None:
    repos = in_memory_repositories()
    now = datetime(2026, 10, 1, tzinfo=UTC)
    await repos.definitions.save(_email_definition("mail", "email"))
    far = MonitoringService(repos, consent_expires_at=lambda account: now + timedelta(days=3))
    no_clock = MonitoringService(repos, consent_expires_at=lambda account: None)
    unwired = MonitoringService(repos)
    for service in (far, no_clock, unwired):
        alerts = await service.run_once(now=now)
        assert not [a for a in alerts if "consent" in a["action"]]


# --- trigger-health noise (2026-09-28: six stale-trigger emails, all noise) ---


async def test_only_REGISTERED_triggers_are_checked_for_staleness() -> None:
    """Two DB-only drafts with email triggers — nothing polls for them —
    mailed the operator on every restart."""
    repos = in_memory_repositories()
    for d in ("polled", "draft"):
        await repos.definitions.save(_email_definition(d, "email"))
    service = MonitoringService(repos, registered_workflows=lambda: {"polled"})
    alerts = await service.run_once(now=datetime.now(UTC))
    assert [a["workflow_id"] for a in alerts if a["action"] == "alert_stale_trigger"] == ["polled"]


async def test_a_stale_episode_alerts_ONCE_across_restarts() -> None:
    """The in-process set reset on every restart, so every restart re-raised
    every stale trigger — and, with alert email, re-mailed it. The episode is
    keyed by the last run and remembered in the audit log."""
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    await repos.definitions.save(_email_definition("mail", "email"))
    await repos.instances.create(
        WorkflowInstance(
            workflow_id="mail",
            state=WorkflowInstanceState.COMPLETED,
            started_at=now - timedelta(days=5),
        )
    )

    def stale(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [a for a in alerts if a["action"] == "alert_stale_trigger"]

    assert len(stale(await MonitoringService(repos).run_once(now=now))) == 1
    # A fresh process — same episode — stays quiet.
    assert stale(await MonitoringService(repos).run_once(now=now)) == []
    # It runs again, then goes stale again: a NEW episode, which alerts.
    await repos.instances.create(
        WorkflowInstance(
            workflow_id="mail",
            state=WorkflowInstanceState.COMPLETED,
            started_at=now + timedelta(days=1),
        )
    )
    assert len(stale(await MonitoringService(repos).run_once(now=now + timedelta(days=5)))) == 1


async def test_a_sparse_trigger_sets_its_own_staleness_threshold() -> None:
    from workflow_platform.workflow import WorkflowDefinition

    repos = in_memory_repositories()
    now = datetime.now(UTC)
    sparse = _email_definition("dmarc", "email").model_dump()
    sparse["trigger"]["config"]["stale_alert_after_hours"] = 168
    await repos.definitions.save(WorkflowDefinition.model_validate(sparse))
    await repos.instances.create(
        WorkflowInstance(
            workflow_id="dmarc",
            state=WorkflowInstanceState.COMPLETED,
            started_at=now - timedelta(days=4),
        )
    )
    service = MonitoringService(repos)
    assert not [a for a in await service.run_once(now=now) if a["action"] == "alert_stale_trigger"]
    later = [
        a
        for a in await service.run_once(now=now + timedelta(days=4))
        if a["action"] == "alert_stale_trigger"
    ]
    assert [a["threshold_seconds"] for a in later] == [168 * 3600.0]


async def test_a_consent_warning_is_not_repeated_by_a_restart() -> None:
    repos = in_memory_repositories()
    now = datetime(2026, 10, 5, tzinfo=UTC)
    await repos.definitions.save(_email_definition("mail", "email"))

    def fresh() -> MonitoringService:
        return MonitoringService(repos, consent_expires_at=lambda account: now + timedelta(hours=3))

    first = await fresh().run_once(now=now)
    again = await fresh().run_once(now=now)
    assert len([a for a in first if "consent" in a["action"]]) == 1
    assert not [a for a in again if "consent" in a["action"]]


# --- alert_low_disk (2026-10-04: the root filesystem filled on 10-01) ---


def _disk(total_gb: float, free_gb: float) -> Any:
    def usage(path: str) -> tuple[int, int, int]:
        total, free = int(total_gb * 1e9), int(free_gb * 1e9)
        return total, total - free, free

    return usage


def _low(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in alerts if a["action"] == "alert_low_disk"]


async def test_low_disk_alerts_with_a_lossless_detail() -> None:
    """Under the flip (production): the entry is instance-less, so a detail
    projection would strip is refused — this proves it is stored whole."""
    from workflow_platform.audit_writer import AuditWriter

    repos = in_memory_repositories()
    service = MonitoringService(
        repos,
        audit_writer=AuditWriter(repos, trace_safe_only=True),
        disk_usage=_disk(193, 9.4),
    )
    [alert] = _low(await service.run_once(now=datetime.now(UTC)))
    assert alert["mount"] == "/" and alert["free_pct"] == 4.9 and alert["free_gb"] == 9.4
    [entry] = [e for e in await repos.audit.list_recent(limit=20) if e.action == "alert_low_disk"]
    assert entry.detail == {
        "mount": "/",
        "free_gb": 9.4,
        "total_gb": 193.0,
        "free_pct": 4.9,
        "min_free_gb": 10.0,
        "min_free_pct": 10.0,
    }


async def test_either_floor_triggers_and_healthy_disks_are_quiet() -> None:
    now = datetime.now(UTC)
    # 61 GB free of 193 (31%) — today's disk: quiet.
    assert not _low(
        await MonitoringService(in_memory_repositories(), disk_usage=_disk(193, 61)).run_once(
            now=now
        )
    )
    # 8 GB of 50 is 16% — over the percentage floor, under the GB floor.
    assert _low(
        await MonitoringService(in_memory_repositories(), disk_usage=_disk(50, 8)).run_once(now=now)
    )
    # 18 GB of 2000 is 0.9% — over the GB floor, under the percentage floor.
    assert _low(
        await MonitoringService(in_memory_repositories(), disk_usage=_disk(2000, 18)).run_once(
            now=now
        )
    )


async def test_low_disk_repeats_every_six_hours_not_every_poll_or_restart() -> None:
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    assert (
        len(_low(await MonitoringService(repos, disk_usage=_disk(193, 5)).run_once(now=now))) == 1
    )
    # Next poll, and a "restarted" process: the audit log remembers.
    assert not _low(await MonitoringService(repos, disk_usage=_disk(193, 5)).run_once(now=now))
    later = now + timedelta(hours=5)
    assert not _low(await MonitoringService(repos, disk_usage=_disk(193, 5)).run_once(now=later))
    # Still low six hours on: say so again.
    again = now + timedelta(hours=6, minutes=1)
    assert (
        len(_low(await MonitoringService(repos, disk_usage=_disk(193, 5)).run_once(now=again))) == 1
    )


async def test_an_unreadable_mount_is_skipped_not_fatal() -> None:
    def broken(path: str) -> tuple[int, int, int]:
        raise OSError("SYNTHETIC: no such mount")

    alerts = await MonitoringService(in_memory_repositories(), disk_usage=broken).run_once(
        now=datetime.now(UTC)
    )
    assert not _low(alerts)
