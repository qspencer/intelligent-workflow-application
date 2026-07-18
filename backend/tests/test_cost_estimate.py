"""Tests for run-stats + the /api/workflows/{id}/cost-estimate endpoint (C6.2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.cost import CostReportService, price_for_model
from workflow_platform.main import create_app
from workflow_platform.persistence import (
    StepExecution,
    StepExecutionState,
    WorkflowInstance,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition_from_yaml

HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

_WF_YAML = f"""
id: est-wf
name: Estimate WF
trigger:
  type: manual
steps:
  - id: classify
    type: agentic
    model: {HAIKU}
    goal: classify the thing
  - id: route
    type: deterministic
    function: noop
edges:
  - {{from: classify, to: route}}
policies:
  max_total_tokens: 5000
  budget_action: pause
"""


async def _seed_runs(repos: Any, *, n_runs: int) -> None:
    """n_runs distinct instances of est-wf, each with one COMPLETED agentic step
    costing $0.01 / 2000 tokens."""
    for _ in range(n_runs):
        inst = WorkflowInstance(workflow_id="est-wf", state=WorkflowInstanceState.COMPLETED)
        await repos.instances.create(inst)
        await repos.steps.create(
            StepExecution(
                instance_id=inst.id,
                step_id="classify",
                state=StepExecutionState.COMPLETED,
                output={"model": HAIKU, "cost_usd": 0.01, "usage": {"total_tokens": 2000}},
                started_at=datetime.now(UTC),
            )
        )


# --- run_stats_for_workflow ---


async def test_run_stats_averages_over_distinct_runs() -> None:
    repos = in_memory_repositories()
    await _seed_runs(repos, n_runs=3)
    stats = await CostReportService(repos).run_stats_for_workflow("est-wf")
    assert stats.run_count == 3
    assert stats.total_cost_usd == pytest.approx(0.03)
    assert stats.avg_cost_usd == pytest.approx(0.01)
    assert stats.avg_tokens == 2000


async def test_run_stats_empty_when_no_history() -> None:
    stats = await CostReportService(in_memory_repositories()).run_stats_for_workflow("est-wf")
    assert stats.run_count == 0
    assert stats.avg_cost_usd is None
    assert stats.avg_tokens is None


# --- endpoint ---


def test_cost_estimate_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()

    async def _setup() -> None:
        await repos.definitions.save(load_definition_from_yaml(_WF_YAML))
        await _seed_runs(repos, n_runs=2)

    asyncio.run(_setup())

    client = TestClient(create_app(repositories=repos))
    body = client.get(
        "/api/workflows/est-wf/cost-estimate",
        headers={"X-Dev-User": "a", "X-Dev-Groups": "org-viewers"},
    ).json()

    assert body["max_total_tokens"] == 5000
    assert body["budget_action"] == "pause"
    assert body["run_count"] == 2
    assert body["avg_cost_usd"] == pytest.approx(0.01)
    assert body["avg_tokens"] == 2000

    # One agentic step (the deterministic step is excluded), with real rates.
    assert len(body["models"]) == 1
    model = body["models"][0]
    assert model["step_id"] == "classify"
    assert model["model"] == HAIKU
    price = price_for_model(HAIKU)
    assert price is not None
    assert model["input_per_million"] == price.input_per_million
    assert model["output_per_million"] == price.output_per_million


def test_cost_estimate_no_history_has_null_avg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    asyncio.run(repos.definitions.save(load_definition_from_yaml(_WF_YAML)))

    client = TestClient(create_app(repositories=repos))
    body = client.get(
        "/api/workflows/est-wf/cost-estimate",
        headers={"X-Dev-User": "a", "X-Dev-Groups": "org-viewers"},
    ).json()
    assert body["run_count"] == 0
    assert body["avg_cost_usd"] is None
    assert body["avg_tokens"] is None
    assert len(body["models"]) == 1  # rates still shown without history


def test_cost_estimate_unknown_workflow_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(repositories=in_memory_repositories()))
    r = client.get(
        "/api/workflows/ghost/cost-estimate",
        headers={"X-Dev-User": "a", "X-Dev-Groups": "org-viewers"},
    )
    assert r.status_code == 404
