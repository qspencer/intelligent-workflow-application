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
    # UI-only (canvas). Engine ignores both. `label` overrides the derived
    # node title; `output_renderer` selects the inspector's result card.
    label: str | None = None
    output_renderer: str | None = None


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
    # Input minimization (EMAIL_TRIAGE_ACT_PLAN §3): when set, the user
    # message carries ONLY these resolved context paths (e.g.
    # "steps.record.category", "trigger.message_id") instead of the full
    # trigger + all prior outputs. Load-bearing for tool-holding steps that
    # must never see attacker-influenced text. Absent = legacy behavior.
    inputs: list[str] | None = None
    policy: AgenticStepPolicy = Field(default_factory=AgenticStepPolicy)
    outputs: list[str] = Field(default_factory=list)
    capabilities: CapabilityPolicy | None = None
    runtime: StepRuntimePolicy = Field(default_factory=StepRuntimePolicy)
    # UI-only (canvas). Engine ignores both. `label` overrides the derived
    # node title; `output_renderer` selects the inspector's result card.
    label: str | None = None
    output_renderer: str | None = None


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
    # UI-only (canvas): plain-language render of `condition` on the edge.
    # Engine ignores it. Falls back to the raw expression when unset.
    condition_label: str | None = None


class ObservationSpec(BaseModel):
    """One memory write the engine performs after a successful run.

    `text` is a template: `{trigger.x.y}` / `{steps.id.field}` placeholders
    resolve against the workflow context (missing paths render empty).
    `author` is the trust-critical field — `third_party` for content that
    arrived from outside (received mail, external documents: its claims are
    quarantined, never stored as user facts), `system` for platform-derived
    content (e.g. a triage verdict), `user` for the user's own words.
    """

    text: str
    author: Literal["user", "third_party", "system"] = "third_party"
    # Mixed provenance (veracium >=0.1.7): when a system/user-authored
    # observation's TEXT embeds lower-trust content (a verdict quoting an
    # email subject), declare the content's source here — trust caps at the
    # minimum of author and derived_from, closing the laundering channel.
    derived_from: Literal["user", "third_party", "system"] | None = None
    event_type: str = "chat"
    # Context paths (same dotted form as placeholders, no braces):
    date_from: str | None = None  # ISO date/datetime the event occurred
    ref_from: str | None = None  # evidence reference (e.g. a message id)


class RecallSpec(BaseModel):
    """Read side of learned memory (G10): before each agentic step, the
    engine recalls what prior runs learned about the entity at `query_from`
    (a `trigger.x.y` context path — e.g. the sender address) and injects the
    provenance-fenced context into the agent's system prompt. The recalled
    block is injected VERBATIM — never flattened or re-summarized — and the
    entity value is normalized before querying (case, plus-addressing).
    """

    query_from: str
    token_budget: int = 600


class LearnedMemorySpec(BaseModel):
    """Opt-in learned per-entity memory (veracium) for a workflow.

    The engine ingests `observations` after a run COMPLETEs (write side) and,
    when `recall` is set, injects per-entity history into agentic steps
    (read side). Agents never write this memory directly. `user_id` names
    whose memory this is (e.g. the mailbox owner); per-entity separation
    within it is veracium's job.
    """

    user_id: str
    observations: list[ObservationSpec] = Field(default_factory=list)
    recall: RecallSpec | None = None


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
    learned_memory: LearnedMemorySpec | None = None
