"""Workflow definition schema.

Workflows are DAGs of steps. Each step is either:
- `deterministic` — runs a registered function from the FunctionRegistry, OR
- `agentic` — runs an Agent (system prompt + tools + model + policy).

For Phase 0 / Week 3 the executor walks edges sequentially in topological order.
Conditional edges and parallel execution arrive in Phase 1 (Week 5).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from workflow_platform.security import CapabilityPolicy


class TriggerSpec(BaseModel):
    """Declarative trigger config. The trigger registry resolves `type` at runtime."""

    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    # Optional example payload shown verbatim in the dashboard's "Run"
    # dialog as the pre-filled trigger payload. Lets each workflow YAML
    # carry its own shape-hint so operators don't need to guess what the
    # agent expects. Never read by the engine — purely operator UX.
    example_payload: dict[str, Any] | None = None


class StepRuntimePolicy(BaseModel):
    """Runtime constraints common to all step types."""

    retries: int = 0
    timeout_seconds: float | None = None


class DeterministicStep(BaseModel):
    id: str
    type: Literal["deterministic"] = "deterministic"
    function: str
    config: dict[str, Any] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)
    capabilities: CapabilityPolicy | None = None
    runtime: StepRuntimePolicy = Field(default_factory=StepRuntimePolicy)


class AgenticStepPolicy(BaseModel):
    max_iterations: int = 10
    max_total_tokens: int = 200_000
    inference_config: dict[str, Any] | None = None


class AgenticStep(BaseModel):
    id: str
    type: Literal["agentic"] = "agentic"
    goal: str
    tools: list[str] = Field(default_factory=list)
    model: str
    system_prompt: str | None = None
    policy: AgenticStepPolicy = Field(default_factory=AgenticStepPolicy)
    outputs: list[str] = Field(default_factory=list)
    capabilities: CapabilityPolicy | None = None
    runtime: StepRuntimePolicy = Field(default_factory=StepRuntimePolicy)


Step = Annotated[DeterministicStep | AgenticStep, Field(discriminator="type")]


class Edge(BaseModel):
    """Directed edge in the DAG. Uses the schema's `from` / `to` keys.

    `condition` (optional) is a sandboxed Python expression evaluated against the
    workflow context after the source step completes. If it returns falsy the
    edge is "inactive" — the target won't be entered through this edge. A target
    whose every incoming edge is inactive is marked SKIPPED (and the skip
    propagates downstream).
    """

    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="from")
    target: str = Field(alias="to")
    condition: str | None = None


class WorkflowPolicy(BaseModel):
    max_total_tokens: int | None = None
    timeout_seconds: float | None = None
    budget_action: Literal["notify", "pause", "escalate"] = "pause"


class WorkflowDefinition(BaseModel):
    id: str
    name: str
    description: str = ""
    trigger: TriggerSpec
    steps: list[Step]
    edges: list[Edge] = Field(default_factory=list)
    policies: WorkflowPolicy = Field(default_factory=WorkflowPolicy)
    capabilities: CapabilityPolicy | None = None
