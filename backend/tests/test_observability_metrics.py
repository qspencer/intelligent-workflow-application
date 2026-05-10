"""Tests for Prometheus metrics: direct unit tests + an end-to-end check
that the engine increments counters when running a workflow."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
)
from workflow_platform.engine.functions import noop
from workflow_platform.main import create_app
from workflow_platform.observability import CONTENT_TYPE, NoopMetrics, PrometheusMetrics
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _samples(metrics: PrometheusMetrics, name: str) -> dict[tuple[tuple[str, str], ...], float]:
    """Return {label-tuple: value} for all samples of the named family."""
    out: dict[tuple[tuple[str, str], ...], float] = {}
    for family in metrics.registry.collect():
        for sample in family.samples:
            if sample.name == name:
                out[tuple(sorted(sample.labels.items()))] = sample.value
    return out


# --- noop ---


def test_noop_metrics_swallows_calls() -> None:
    m = NoopMetrics()
    m.workflow_started("a")
    m.workflow_finished("a", "completed", 1.5)
    m.step_finished("agentic", "completed", 0.3)
    m.agent_tokens(MODEL, 100, 50)
    m.bedrock_cost(MODEL, 0.001)


# --- prometheus direct ---


def test_prometheus_workflow_finished_increments_counter_and_histogram() -> None:
    m = PrometheusMetrics()
    m.workflow_finished("wf-a", "completed", 1.25)
    m.workflow_finished("wf-a", "completed", 0.75)
    m.workflow_finished("wf-a", "failed", 0.1)

    runs = _samples(m, "workflow_runs_total")
    assert runs[(("state", "completed"), ("workflow_id", "wf-a"))] == 2.0
    assert runs[(("state", "failed"), ("workflow_id", "wf-a"))] == 1.0

    counts = _samples(m, "workflow_run_duration_seconds_count")
    assert counts[(("workflow_id", "wf-a"),)] == 3.0


def test_prometheus_agent_tokens_split_by_kind() -> None:
    m = PrometheusMetrics()
    m.agent_tokens(MODEL, 1000, 500)
    m.agent_tokens(MODEL, 200, 0)  # output zero — should not record an output sample

    samples = _samples(m, "workflow_agent_tokens_total")
    assert samples[(("kind", "input"), ("model", MODEL))] == 1200.0
    assert samples[(("kind", "output"), ("model", MODEL))] == 500.0


def test_prometheus_bedrock_cost_skips_zero() -> None:
    m = PrometheusMetrics()
    m.bedrock_cost(MODEL, 0.0)
    m.bedrock_cost(MODEL, 0.000123)

    samples = _samples(m, "workflow_bedrock_cost_usd_total")
    assert samples[(("model", MODEL),)] == pytest.approx(0.000123)


def test_prometheus_render_returns_text_exposition() -> None:
    m = PrometheusMetrics()
    m.workflow_finished("wf-a", "completed", 1.0)
    body = m.render().decode()
    assert "workflow_runs_total" in body
    assert 'state="completed"' in body
    assert 'workflow_id="wf-a"' in body


# --- engine integration ---


@pytest.mark.asyncio
async def test_engine_records_metrics_for_agentic_workflow() -> None:
    metrics = PrometheusMetrics()
    repos = in_memory_repositories()
    fake_bedrock = FakeBedrock([text_response("done.", input_tokens=120, output_tokens=30)])
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry({"noop": noop}),
        tools=ToolCatalog(),
        bedrock=fake_bedrock,
        world=mock_world(),
        metrics=metrics,
    )
    definition = load_definition(
        {
            "id": "metric-wf",
            "name": "Metric WF",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "model": MODEL,
                    "tools": [],
                    "goal": "done",
                    "policy": {"max_iterations": 1, "max_total_tokens": 1000},
                }
            ],
            "edges": [],
        }
    )

    instance = await engine.run(definition)
    assert instance.state.value == "completed"

    runs = _samples(metrics, "workflow_runs_total")
    assert runs[(("state", "completed"), ("workflow_id", "metric-wf"))] == 1.0
    steps = _samples(metrics, "workflow_step_runs_total")
    assert steps[(("state", "completed"), ("step_type", "agentic"))] == 1.0
    tokens = _samples(metrics, "workflow_agent_tokens_total")
    assert tokens[(("kind", "input"), ("model", MODEL))] == 120.0
    assert tokens[(("kind", "output"), ("model", MODEL))] == 30.0


# --- /metrics endpoint ---


def test_metrics_endpoint_unauthenticated_and_text_exposition() -> None:
    app = create_app(repositories=in_memory_repositories())
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-type"] == CONTENT_TYPE
    # The registry was just built — at least the metric family names should appear.
    assert "workflow_runs_total" in response.text
