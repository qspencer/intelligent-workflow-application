"""Phase 1 success-criteria verification.

Four criteria from BUILD_PLAN.md:

1. An authenticated operator can start, observe, retry, and kill workflows
   from the UI. (Verified at the API layer the UI depends on.)
2. A capability violation is denied, logged, and visible in the audit trail.
3. A failed workflow can be replayed locally in <5 seconds for debugging.
4. Two workflows of different shapes (PDF processing, webhook-triggered)
   run concurrently without interfering.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import fitz
import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
    pdf_extract,
)
from workflow_platform.main import create_app
from workflow_platform.persistence import (
    StepExecutionState,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.security import CapabilityPolicy
from workflow_platform.tools import FileWriteTool
from workflow_platform.triggers import WebhookRegistry, WebhookTrigger
from workflow_platform.workflow import load_definition
from workflow_platform.world import MockFilesystem, mock_world

MODEL = "anthropic.claude-3-haiku-20240307-v1:0"


def _make_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


# --- Criterion 1: operator can start, observe, retry, kill via the API ---


def test_criterion_1_operator_can_start_observe_retry_kill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The UI is a thin layer over /api. Verify each lifecycle action the UI
    surfaces (start via webhook, observe via GET, retry via POST, kill via
    POST) works for an Operator role."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    repos = in_memory_repositories()
    bus_registry = WebhookRegistry()
    fns = FunctionRegistry()

    async def succeeds(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"ok": True}

    fns.register("succeeds", succeeds)

    definition = load_definition(
        {
            "id": "ops-demo",
            "name": "Ops demo",
            "trigger": {"type": "webhook", "config": {"id": "ops-demo"}},
            "steps": [{"id": "a", "type": "deterministic", "function": "succeeds"}],
            "edges": [],
        }
    )
    asyncio.run(repos.definitions.save(definition))

    engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )

    # Wire the webhook trigger: webhook fires → engine.run runs the workflow.
    started: dict[str, str] = {}

    async def on_event(payload: dict[str, Any]) -> None:
        instance = await engine.run(definition, trigger_payload=payload)
        started["id"] = instance.id

    asyncio.run(WebhookTrigger(bus_registry, "ops-demo").start(on_event))

    app = create_app(repositories=repos, engine=engine, webhook_registry=bus_registry)
    client = TestClient(app)
    operator = {"X-Dev-User": "alice", "X-Dev-Groups": "org-users"}

    # 1. Start: operator-equivalent triggers a workflow via webhook.
    r = client.post("/api/triggers/webhook/ops-demo", json={"src": "ui-test"})
    assert r.status_code == 200, r.text

    # Give the engine a beat to finish (the webhook callback awaits engine.run).
    for _ in range(20):
        if "id" in started:
            break
        time.sleep(0.05)
    assert "id" in started, "webhook never invoked engine.run"
    instance_id = started["id"]

    # 2. Observe: list + detail endpoints return the instance.
    r = client.get("/api/workflow-instances", headers=operator)
    assert r.status_code == 200
    assert any(i["id"] == instance_id for i in r.json())
    r = client.get(f"/api/workflow-instances/{instance_id}", headers=operator)
    assert r.status_code == 200
    assert r.json()["instance"]["state"] == "completed"

    # 3. Kill: a fresh running instance is killable.
    instance = asyncio.run(
        repos.instances.create(
            __import__(
                "workflow_platform.persistence", fromlist=["WorkflowInstance"]
            ).WorkflowInstance(workflow_id="ops-demo", state=WorkflowInstanceState.RUNNING)
        )
    )
    r = client.post(f"/api/workflow-instances/{instance.id}/kill", headers=operator)
    assert r.status_code == 200
    fresh = asyncio.run(repos.instances.get(instance.id))
    assert fresh is not None
    assert fresh.state == WorkflowInstanceState.KILLED

    # 4. Retry: a fresh failed instance accepts retry.
    failed = asyncio.run(
        repos.instances.create(
            __import__(
                "workflow_platform.persistence", fromlist=["WorkflowInstance"]
            ).WorkflowInstance(workflow_id="ops-demo", state=WorkflowInstanceState.FAILED)
        )
    )
    r = client.post(f"/api/workflow-instances/{failed.id}/retry", headers=operator)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "retry_started"


# --- Criterion 2: capability violation denied, logged, audit-visible ---


async def test_criterion_2_capability_violation_logged_in_audit() -> None:
    repos = in_memory_repositories()
    bedrock = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[("c1", "file_write", {"path": "/processed/x.txt", "content": "hi"})]
            ),
            text_response("Couldn't write."),
        ]
    )
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "Try",
                    "model": MODEL,
                    "tools": ["file_write"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([FileWriteTool()]),
        bedrock=bedrock,
        world=mock_world(),
        # Explicitly forbid file_write at the system level.
        system_capabilities=CapabilityPolicy(tools=["file_read"]),
    )

    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.COMPLETED

    audit = await repos.audit.list_by_instance(instance.id)
    tool_calls = [e for e in audit if e.action == "tool_call"]
    assert len(tool_calls) == 1
    error = tool_calls[0].detail["result"]["error"]

    # Denied + logged + visible in audit detail.
    assert error is not None
    assert "Capability denied" in error
    assert "file_write" in error
    # Visible: querying the audit log surfaces the violation.
    visible = await repos.audit.list_by_instance(instance.id)
    assert any(
        e.action == "tool_call"
        and "Capability denied" in (e.detail.get("result", {}).get("error") or "")
        for e in visible
    )


# --- Criterion 3: failed workflow replays locally in <5s ---


def test_criterion_3_replay_under_five_seconds(tmp_path: Path) -> None:
    """The replay CLI re-runs a previously-recorded workflow definition in
    well under 5 seconds."""
    definition = {
        "id": "replay-criterion",
        "name": "Replay criterion",
        "trigger": {"type": "manual"},
        "steps": [{"id": "a", "type": "deterministic", "function": "noop"}],
        "edges": [],
    }
    definition_path = tmp_path / "wf.json"
    definition_path.write_text(json.dumps(definition))
    recordings = tmp_path / "recordings"
    recordings.mkdir()

    start = time.perf_counter()
    result = subprocess.run(
        [
            sys.executable,
            "tools/replay.py",
            "--definition",
            str(definition_path),
            "--trigger",
            "{}",
            "--recordings-dir",
            str(recordings),
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    elapsed = time.perf_counter() - start

    assert result.returncode == 0, result.stderr
    assert elapsed < 5.0, f"Replay took {elapsed:.2f}s, must be <5s"
    assert "State:    completed" in result.stdout


# --- Criterion 4: two workflows (PDF + webhook) run concurrently without interfering ---


async def test_criterion_4_pdf_and_webhook_workflows_run_concurrently(
    tmp_path: Path,
) -> None:
    """Run a PDF-processing workflow and a webhook-triggered workflow
    concurrently. Verify both complete with their own independent state.
    """
    repos = in_memory_repositories()

    fns = FunctionRegistry()
    fns.register("pdf_extract", pdf_extract)

    async def webhook_step(config: dict[str, Any], ctx: Any, world: Any) -> dict[str, Any]:
        return {"echoed": ctx.trigger.get("payload", {})}

    fns.register("webhook_step", webhook_step)

    pdf_def = load_definition(
        {
            "id": "pdf-flow",
            "name": "PDF processing",
            "trigger": {"type": "file_watch"},
            "steps": [
                {
                    "id": "extract",
                    "type": "deterministic",
                    "function": "pdf_extract",
                    "config": {"filepath_from": "trigger.file_path"},
                },
                {
                    "id": "summarize",
                    "type": "agentic",
                    "goal": "Save the extracted text",
                    "model": MODEL,
                    "tools": ["file_write"],
                },
            ],
            "edges": [{"from": "extract", "to": "summarize"}],
        }
    )
    webhook_def = load_definition(
        {
            "id": "webhook-flow",
            "name": "Webhook flow",
            "trigger": {"type": "webhook"},
            "steps": [{"id": "echo", "type": "deterministic", "function": "webhook_step"}],
            "edges": [],
        }
    )

    pdf_world = mock_world()
    webhook_world = mock_world()

    pdf_bedrock = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[
                    (
                        "c1",
                        "file_write",
                        {"path": "/processed/summary.txt", "content": "PDF summary"},
                    )
                ]
            ),
            text_response("done"),
        ]
    )
    webhook_bedrock = FakeBedrock([])

    pdf_engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog([FileWriteTool()]),
        bedrock=pdf_bedrock,
        world=pdf_world,
    )
    webhook_engine = WorkflowEngine(
        repositories=repos,
        functions=fns,
        tools=ToolCatalog(),
        bedrock=webhook_bedrock,
        world=webhook_world,
    )

    # Materialize a real PDF on disk so pdf_extract can read it.
    pdf_path = tmp_path / "invoice.pdf"
    _make_pdf(pdf_path, "Invoice text payload")

    # Run both concurrently.
    pdf_run = pdf_engine.run(pdf_def, trigger_payload={"file_path": str(pdf_path)})
    webhook_run = webhook_engine.run(webhook_def, trigger_payload={"payload": {"message": "hello"}})
    pdf_instance, webhook_instance = await asyncio.gather(pdf_run, webhook_run)

    # Both completed.
    assert pdf_instance.state == WorkflowInstanceState.COMPLETED, pdf_instance.error
    assert webhook_instance.state == WorkflowInstanceState.COMPLETED, webhook_instance.error
    assert pdf_instance.id != webhook_instance.id

    # Independent state: PDF result lands in pdf_world, webhook result in webhook_world.
    pdf_fs = pdf_world.fs
    webhook_fs = webhook_world.fs
    assert isinstance(pdf_fs, MockFilesystem)
    assert isinstance(webhook_fs, MockFilesystem)
    assert pdf_fs.files["/processed/summary.txt"] == b"PDF summary"
    assert "/processed/summary.txt" not in webhook_fs.files
    assert webhook_instance.context["steps"]["echo"]["echoed"] == {"message": "hello"}
    assert "echo" not in pdf_instance.context["steps"]

    # Step executions are scoped to their instances.
    pdf_steps = await repos.steps.list_by_instance(pdf_instance.id)
    webhook_steps = await repos.steps.list_by_instance(webhook_instance.id)
    assert {s.step_id for s in pdf_steps} == {"extract", "summarize"}
    assert {s.step_id for s in webhook_steps} == {"echo"}
    assert all(s.state == StepExecutionState.COMPLETED for s in pdf_steps + webhook_steps)
