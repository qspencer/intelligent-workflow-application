"""Phase 2 success-criteria verification.

Four criteria from BUILD_PLAN.md:

1. Three workflows of distinct shapes run concurrently against a mix of
   local files and S3.
2. An operator gets paged when error rate spikes; the audit trail explains why.
3. The system stays under budget when a workflow misbehaves; the misbehaving
   instance is paused, others continue.
4. Total spend is visible per workflow per day.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import fitz
import pytest

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.connectors import ConnectorRegistry, S3Connector
from workflow_platform.cost import CostReportService
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
    pdf_extract,
)
from workflow_platform.monitoring import MonitoringConfig, MonitoringService
from workflow_platform.persistence import (
    StepExecution,
    StepExecutionState,
    WorkflowInstance,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.tools import ConnectorSendTool
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"


def _make_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


# --- Criterion 1: three distinct workflow shapes run concurrently ---


async def test_criterion_1_three_workflows_concurrent_local_and_s3(tmp_path: Path) -> None:
    """A PDF (file_watch) workflow, a webhook-triggered workflow, and a
    scheduled workflow all complete concurrently. The PDF flow reads from the
    local filesystem; the webhook flow writes to a mocked S3 connector; the
    scheduled flow does pure compute. None of them interferes with the others.
    """
    repos = in_memory_repositories()
    fns = FunctionRegistry()
    fns.register("pdf_extract", pdf_extract)

    async def echo(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"timestamp": ctx.trigger.get("triggered_at", "")}

    fns.register("echo", echo)

    # Workflow A: PDF processing (local file).
    pdf_def = load_definition(
        {
            "id": "pdf-flow",
            "name": "PDF",
            "trigger": {"type": "file_watch"},
            "steps": [
                {
                    "id": "extract",
                    "type": "deterministic",
                    "function": "pdf_extract",
                    "config": {"filepath_from": "trigger.file_path"},
                }
            ],
            "edges": [],
        }
    )

    # Workflow B: webhook → agent writes to S3 connector.
    fake_s3 = MagicMock()
    fake_s3.put_object.return_value = {}
    s3_registry = ConnectorRegistry({"out": S3Connector(bucket="b", client=fake_s3)})

    webhook_def = load_definition(
        {
            "id": "webhook-flow",
            "name": "Webhook",
            "trigger": {"type": "webhook"},
            "steps": [
                {
                    "id": "send",
                    "type": "agentic",
                    "goal": "send",
                    "model": HAIKU,
                    "tools": ["connector_send"],
                }
            ],
            "edges": [],
        }
    )

    # Workflow C: scheduled (pure compute).
    schedule_def = load_definition(
        {
            "id": "schedule-flow",
            "name": "Scheduled",
            "trigger": {"type": "schedule", "config": {"interval_seconds": 60}},
            "steps": [{"id": "echo", "type": "deterministic", "function": "echo"}],
            "edges": [],
        }
    )

    pdf_world = mock_world()
    webhook_world = mock_world()
    schedule_world = mock_world()

    pdf_engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=pdf_world,
    )
    webhook_engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog([ConnectorSendTool(s3_registry)]),
        bedrock=FakeBedrock(
            [
                tool_use_response(
                    tool_uses=[
                        (
                            "c1",
                            "connector_send",
                            {
                                "connector_id": "out",
                                "payload": {"key": "x.txt", "body": "hi"},
                            },
                        )
                    ]
                ),
                text_response("done"),
            ]
        ),
        world=webhook_world,
    )
    schedule_engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=schedule_world,
    )

    pdf_path = tmp_path / "invoice.pdf"
    _make_pdf(pdf_path, "Invoice text")

    pdf_run = pdf_engine.run(pdf_def, trigger_payload={"file_path": str(pdf_path)})
    webhook_run = webhook_engine.run(webhook_def, trigger_payload={"src": "ui-test"})
    schedule_run = schedule_engine.run(
        schedule_def, trigger_payload={"triggered_at": "2026-05-09T10:00:00Z"}
    )
    pdf_inst, webhook_inst, schedule_inst = await asyncio.gather(pdf_run, webhook_run, schedule_run)

    assert pdf_inst.state == WorkflowInstanceState.COMPLETED
    assert webhook_inst.state == WorkflowInstanceState.COMPLETED
    assert schedule_inst.state == WorkflowInstanceState.COMPLETED

    # Each instance is distinct and independent.
    ids = {pdf_inst.id, webhook_inst.id, schedule_inst.id}
    assert len(ids) == 3

    # PDF flow read the actual file.
    assert "Invoice" in pdf_inst.context["steps"]["extract"]["text"]
    # Webhook flow used S3.
    fake_s3.put_object.assert_called_once()
    # Schedule flow echoed the trigger payload.
    assert schedule_inst.context["steps"]["echo"]["timestamp"] == "2026-05-09T10:00:00Z"


# --- Criterion 2: high error rate triggers alert with explanation ---


async def test_criterion_2_high_error_rate_alert_with_audit_explanation() -> None:
    repos = in_memory_repositories()
    now = datetime.now(UTC)
    # 4 failed + 1 completed = 80% error rate.
    for _ in range(4):
        await repos.instances.create(
            WorkflowInstance(
                workflow_id="flaky",
                state=WorkflowInstanceState.FAILED,
                created_at=now - timedelta(seconds=10),
            )
        )
    await repos.instances.create(
        WorkflowInstance(
            workflow_id="flaky",
            state=WorkflowInstanceState.COMPLETED,
            created_at=now - timedelta(seconds=10),
        )
    )

    monitor = MonitoringService(
        repos,
        config=MonitoringConfig(
            interval_seconds=1.0,
            error_rate_threshold=0.5,
            error_rate_min_sample=3,
        ),
    )
    alerts = await monitor.run_once(now=now)

    matching = [a for a in alerts if a["action"] == "alert_high_error_rate"]
    assert len(matching) == 1
    detail = matching[0]
    # The audit trail explains what happened.
    assert detail["failed"] == 4
    assert detail["total_terminal"] == 5
    assert detail["rate"] == 0.8
    assert detail["threshold"] == 0.5

    # Persisted in the audit log.
    audit = await repos.audit.list_recent()
    assert any(e.action == "alert_high_error_rate" for e in audit)


# --- Criterion 3: misbehaving instance pauses, siblings continue ---


async def test_criterion_3_runaway_workflow_paused_others_continue() -> None:
    repos = in_memory_repositories()

    # Workflow A: tight budget that the agent will blow through.
    runaway_def = load_definition(
        {
            "id": "runaway",
            "name": "Runaway",
            "trigger": {"type": "manual"},
            "policies": {"max_total_tokens": 100, "budget_action": "pause"},
            "steps": [
                {
                    "id": "burn",
                    "type": "agentic",
                    "goal": "burn",
                    "model": HAIKU,
                    "tools": [],
                },
                {
                    "id": "burn2",
                    "type": "agentic",
                    "goal": "burn more",
                    "model": HAIKU,
                    "tools": [],
                },
            ],
            "edges": [{"from": "burn", "to": "burn2"}],
        }
    )

    # Workflow B: well-behaved.
    good_def = load_definition(
        {
            "id": "well-behaved",
            "name": "Good",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "x",
                    "model": HAIKU,
                    "tools": [],
                }
            ],
            "edges": [],
        }
    )

    runaway_engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=FakeBedrock(
            [
                text_response("over", input_tokens=600, output_tokens=400),
                text_response("never reached"),
            ]
        ),
        world=mock_world(),
    )
    good_engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=FakeBedrock([text_response("done", input_tokens=10, output_tokens=5)]),
        world=mock_world(),
    )

    runaway_inst, good_inst = await asyncio.gather(
        runaway_engine.run(runaway_def),
        good_engine.run(good_def),
    )
    assert runaway_inst.state == WorkflowInstanceState.PAUSED
    assert good_inst.state == WorkflowInstanceState.COMPLETED

    # The misbehaving instance has the budget audit; the good one doesn't.
    runaway_audit = await repos.audit.list_by_instance(runaway_inst.id)
    good_audit = await repos.audit.list_by_instance(good_inst.id)
    assert any(e.action == "budget_exceeded" for e in runaway_audit)
    assert all(e.action != "budget_exceeded" for e in good_audit)


# --- Criterion 4: total spend visible per workflow per day ---


async def test_criterion_4_cost_report_per_workflow_per_day() -> None:
    repos = in_memory_repositories()
    now = datetime.now(UTC)

    inst_a = WorkflowInstance(workflow_id="wf-a", state=WorkflowInstanceState.COMPLETED)
    inst_b = WorkflowInstance(workflow_id="wf-b", state=WorkflowInstanceState.COMPLETED)
    await repos.instances.create(inst_a)
    await repos.instances.create(inst_b)

    await repos.steps.create(
        StepExecution(
            instance_id=inst_a.id,
            step_id="x",
            state=StepExecutionState.COMPLETED,
            output={"model": HAIKU, "cost_usd": 0.5, "usage": {"total_tokens": 100_000}},
            started_at=now,
        )
    )
    await repos.steps.create(
        StepExecution(
            instance_id=inst_b.id,
            step_id="y",
            state=StepExecutionState.COMPLETED,
            output={"model": HAIKU, "cost_usd": 1.25, "usage": {"total_tokens": 250_000}},
            started_at=now,
        )
    )

    service = CostReportService(repos)

    by_workflow = {r.key: r for r in await service.by_workflow()}
    assert by_workflow["wf-a"].total_cost_usd == pytest.approx(0.5)
    assert by_workflow["wf-b"].total_cost_usd == pytest.approx(1.25)

    today = now.date().isoformat()
    by_day = {r.key: r for r in await service.by_day()}
    assert today in by_day
    assert by_day[today].total_cost_usd == pytest.approx(1.75)
    assert by_day[today].step_count == 2
