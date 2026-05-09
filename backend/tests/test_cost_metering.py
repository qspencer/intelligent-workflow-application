"""Tests for cost calculation, engine attribution, budget enforcement, and
cost reporting."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.cost import CostReportService, cost_for_usage
from workflow_platform.cost.pricing import MODEL_PRICING, ModelPrice
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
)
from workflow_platform.main import create_app
from workflow_platform.persistence import (
    StepExecution,
    StepExecutionState,
    WorkflowInstance,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
SONNET = "anthropic.claude-3-sonnet-20240229-v1:0"


# --- Pricing math ---


def test_cost_for_usage_haiku() -> None:
    cost = cost_for_usage({"input_tokens": 1_000_000, "output_tokens": 0}, HAIKU)
    # Haiku input: $0.25 per 1M tokens.
    assert cost == pytest.approx(0.25)


def test_cost_for_usage_sonnet() -> None:
    cost = cost_for_usage({"input_tokens": 100_000, "output_tokens": 200_000}, SONNET)
    # Sonnet: $3 in + $15 out per 1M.
    assert cost == pytest.approx(0.1 * 3 + 0.2 * 15)


def test_cost_for_usage_unknown_model_returns_zero() -> None:
    cost = cost_for_usage({"input_tokens": 1000, "output_tokens": 1000}, "made-up-model")
    assert cost == 0.0


def test_cost_for_usage_handles_empty_usage() -> None:
    assert cost_for_usage({}, HAIKU) == 0.0
    assert cost_for_usage(None, HAIKU) == 0.0


def test_pricing_table_includes_main_anthropic_models() -> None:
    assert HAIKU in MODEL_PRICING
    assert SONNET in MODEL_PRICING
    assert isinstance(MODEL_PRICING[HAIKU], ModelPrice)


# --- Engine cost attribution ---


async def test_agentic_step_records_model_and_cost_in_output() -> None:
    repos = in_memory_repositories()
    bedrock = FakeBedrock([text_response("ok", input_tokens=1_000_000, output_tokens=500_000)])
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
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
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )
    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.COMPLETED

    steps = await repos.steps.list_by_instance(instance.id)
    assert len(steps) == 1
    output = steps[0].output or {}
    assert output["model"] == HAIKU
    # 1M input + 500K output haiku = 0.25 + 0.625 = 0.875.
    assert output["cost_usd"] == pytest.approx(0.875, rel=1e-3)
    # Instance context exposes the rolling totals.
    assert instance.context["total_tokens"] == 1_500_000
    assert instance.context["total_cost_usd"] == pytest.approx(0.875, rel=1e-3)


# --- Budget enforcement ---


def _budgeted_definition(action: str, max_total_tokens: int = 100) -> dict[str, Any]:
    return {
        "id": "wf",
        "name": "wf",
        "trigger": {"type": "manual"},
        "policies": {
            "max_total_tokens": max_total_tokens,
            "budget_action": action,
        },
        "steps": [
            {
                "id": "a",
                "type": "agentic",
                "goal": "burn",
                "model": HAIKU,
                "tools": [],
            },
            {
                "id": "b",
                "type": "agentic",
                "goal": "more",
                "model": HAIKU,
                "tools": [],
            },
        ],
        "edges": [{"from": "a", "to": "b"}],
    }


async def test_budget_pause_action_pauses_after_breach() -> None:
    repos = in_memory_repositories()
    # First step burns above the cap (1000 tokens, cap is 100).
    bedrock = FakeBedrock(
        [
            text_response("done", input_tokens=600, output_tokens=400),
            text_response("never reached"),
        ]
    )
    definition = load_definition(_budgeted_definition("pause", max_total_tokens=100))
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )
    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.PAUSED
    audit = await repos.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert "budget_exceeded" in actions
    assert "workflow_paused" in actions
    # Step b never started.
    steps = await repos.steps.list_by_instance(instance.id)
    assert {s.step_id for s in steps} == {"a"}


async def test_budget_notify_action_continues_with_audit() -> None:
    repos = in_memory_repositories()
    bedrock = FakeBedrock(
        [
            text_response("over budget", input_tokens=600, output_tokens=400),
            text_response("still running", input_tokens=10, output_tokens=10),
        ]
    )
    definition = load_definition(_budgeted_definition("notify", max_total_tokens=100))
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )
    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.COMPLETED
    audit = await repos.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert "budget_exceeded" in actions
    # Both steps ran despite the breach.
    steps = await repos.steps.list_by_instance(instance.id)
    assert {s.step_id for s in steps} == {"a", "b"}


async def test_budget_escalate_emits_special_action() -> None:
    repos = in_memory_repositories()
    bedrock = FakeBedrock(
        [
            text_response("over", input_tokens=600, output_tokens=400),
            text_response("never reached"),
        ]
    )
    definition = load_definition(_budgeted_definition("escalate", max_total_tokens=100))
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )
    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.PAUSED
    audit = await repos.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert "budget_escalated" in actions


# --- CostReportService ---


async def _seed_reports(repos: Any) -> None:
    """Seed two instances across two workflows + models for aggregation tests."""
    inst_a = WorkflowInstance(workflow_id="wf-a", state=WorkflowInstanceState.COMPLETED)
    inst_b = WorkflowInstance(workflow_id="wf-b", state=WorkflowInstanceState.COMPLETED)
    await repos.instances.create(inst_a)
    await repos.instances.create(inst_b)

    # wf-a uses haiku ($0.25 in + $1.25 out per 1M)
    await repos.steps.create(
        StepExecution(
            instance_id=inst_a.id,
            step_id="a",
            state=StepExecutionState.COMPLETED,
            output={
                "model": HAIKU,
                "cost_usd": 0.5,
                "usage": {"total_tokens": 1_000_000},
            },
            started_at=datetime.now(UTC),
        )
    )
    # wf-b uses sonnet
    await repos.steps.create(
        StepExecution(
            instance_id=inst_b.id,
            step_id="x",
            state=StepExecutionState.COMPLETED,
            output={
                "model": SONNET,
                "cost_usd": 2.0,
                "usage": {"total_tokens": 500_000},
            },
            started_at=datetime.now(UTC),
        )
    )
    # A failed step should NOT count.
    await repos.steps.create(
        StepExecution(
            instance_id=inst_a.id,
            step_id="failed",
            state=StepExecutionState.FAILED,
            output={"model": HAIKU, "cost_usd": 99.0, "usage": {"total_tokens": 999}},
            started_at=datetime.now(UTC),
        )
    )


async def test_cost_report_by_workflow() -> None:
    repos = in_memory_repositories()
    await _seed_reports(repos)
    service = CostReportService(repos)
    rows = await service.by_workflow()
    keyed = {r.key: r for r in rows}
    assert "wf-a" in keyed and "wf-b" in keyed
    assert keyed["wf-a"].total_cost_usd == pytest.approx(0.5)
    assert keyed["wf-b"].total_cost_usd == pytest.approx(2.0)
    # Failed step skipped.
    assert keyed["wf-a"].step_count == 1


async def test_cost_report_by_model() -> None:
    repos = in_memory_repositories()
    await _seed_reports(repos)
    service = CostReportService(repos)
    rows = await service.by_model()
    keyed = {r.key: r for r in rows}
    assert keyed[HAIKU].total_cost_usd == pytest.approx(0.5)
    assert keyed[SONNET].total_cost_usd == pytest.approx(2.0)


async def test_cost_report_by_day_groups_correctly() -> None:
    repos = in_memory_repositories()
    await _seed_reports(repos)
    service = CostReportService(repos)
    rows = await service.by_day()
    today = datetime.now(UTC).date().isoformat()
    keyed = {r.key: r for r in rows}
    assert today in keyed
    assert keyed[today].total_cost_usd == pytest.approx(2.5)


async def test_cost_report_since_filter() -> None:
    repos = in_memory_repositories()
    await _seed_reports(repos)
    service = CostReportService(repos)
    # Filter to "after now" — expect no rows.
    future = datetime.now(UTC) + timedelta(days=1)
    rows = await service.by_workflow(since=future)
    assert rows == []


# --- API endpoints ---


def test_cost_by_workflow_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    asyncio.run(_seed_reports(repos))
    app = create_app(repositories=repos)
    client = TestClient(app)
    r = client.get(
        "/api/cost/by-workflow",
        headers={"X-Dev-User": "alice", "X-Dev-Groups": "viewers"},
    )
    assert r.status_code == 200
    data = r.json()
    keys = {row["workflow_id"] for row in data}
    assert keys == {"wf-a", "wf-b"}


def test_cost_endpoints_require_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app(repositories=in_memory_repositories())
    client = TestClient(app)
    r = client.get("/api/cost/by-day")
    assert r.status_code == 401


def test_cost_endpoint_rejects_bad_since(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app(repositories=in_memory_repositories())
    client = TestClient(app)
    r = client.get(
        "/api/cost/by-workflow?since=not-a-date",
        headers={"X-Dev-User": "alice", "X-Dev-Groups": "viewers"},
    )
    assert r.status_code == 400
