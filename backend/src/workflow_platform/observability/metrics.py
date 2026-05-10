"""Prometheus metrics for the workflow engine.

`Metrics` is the protocol the engine calls into. `NoopMetrics` is the default
(zero side-effects, used in tests that don't care). `PrometheusMetrics` wraps
a `prometheus_client` registry; expose its output at `/metrics`.
"""

from __future__ import annotations

from typing import Protocol

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

CONTENT_TYPE: str = "text/plain; version=0.0.4; charset=utf-8"


class Metrics(Protocol):
    """Hooks the engine calls at meaningful workflow-execution moments.

    Designed to be cheap and side-effect-only — never raise, never block."""

    def workflow_started(self, workflow_id: str) -> None: ...

    def workflow_finished(self, workflow_id: str, state: str, duration_seconds: float) -> None: ...

    def step_finished(self, step_type: str, state: str, duration_seconds: float) -> None: ...

    def agent_tokens(self, model: str, input_tokens: int, output_tokens: int) -> None: ...

    def bedrock_cost(self, model: str, cost_usd: float) -> None: ...


class NoopMetrics:
    """Default `Metrics` — does nothing. Used everywhere a recorder is optional."""

    def workflow_started(self, workflow_id: str) -> None:
        pass

    def workflow_finished(self, workflow_id: str, state: str, duration_seconds: float) -> None:
        pass

    def step_finished(self, step_type: str, state: str, duration_seconds: float) -> None:
        pass

    def agent_tokens(self, model: str, input_tokens: int, output_tokens: int) -> None:
        pass

    def bedrock_cost(self, model: str, cost_usd: float) -> None:
        pass


class PrometheusMetrics:
    """`Metrics` impl backed by a `prometheus_client` `CollectorRegistry`.

    One registry per app process. Exposed via `render()` for the `/metrics`
    HTTP endpoint."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self._workflow_runs = Counter(
            "workflow_runs_total",
            "Workflow runs that reached a terminal state, labeled by state.",
            ["workflow_id", "state"],
            registry=self.registry,
        )
        self._workflow_duration = Histogram(
            "workflow_run_duration_seconds",
            "Wall-clock duration of workflow runs from start to terminal state.",
            ["workflow_id"],
            registry=self.registry,
        )
        self._step_runs = Counter(
            "workflow_step_runs_total",
            "Step executions that reached a terminal state, labeled by type and state.",
            ["step_type", "state"],
            registry=self.registry,
        )
        self._step_duration = Histogram(
            "workflow_step_duration_seconds",
            "Wall-clock duration of step executions.",
            ["step_type"],
            registry=self.registry,
        )
        self._agent_tokens = Counter(
            "workflow_agent_tokens_total",
            "Cumulative input + output tokens consumed by agentic steps, labeled by model and kind.",
            ["model", "kind"],
            registry=self.registry,
        )
        self._bedrock_cost = Counter(
            "workflow_bedrock_cost_usd_total",
            "Cumulative Bedrock spend in USD, labeled by model.",
            ["model"],
            registry=self.registry,
        )

    def workflow_started(self, workflow_id: str) -> None:
        # Counter on completion is enough; starts are observable from /api/workflow-instances.
        pass

    def workflow_finished(self, workflow_id: str, state: str, duration_seconds: float) -> None:
        self._workflow_runs.labels(workflow_id=workflow_id, state=state).inc()
        self._workflow_duration.labels(workflow_id=workflow_id).observe(duration_seconds)

    def step_finished(self, step_type: str, state: str, duration_seconds: float) -> None:
        self._step_runs.labels(step_type=step_type, state=state).inc()
        self._step_duration.labels(step_type=step_type).observe(duration_seconds)

    def agent_tokens(self, model: str, input_tokens: int, output_tokens: int) -> None:
        if input_tokens:
            self._agent_tokens.labels(model=model, kind="input").inc(input_tokens)
        if output_tokens:
            self._agent_tokens.labels(model=model, kind="output").inc(output_tokens)

    def bedrock_cost(self, model: str, cost_usd: float) -> None:
        if cost_usd > 0:
            self._bedrock_cost.labels(model=model).inc(cost_usd)

    def render(self) -> bytes:
        """Serialize the registry in Prometheus text exposition format."""
        return generate_latest(self.registry)
