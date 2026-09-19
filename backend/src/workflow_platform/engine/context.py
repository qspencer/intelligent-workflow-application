"""WorkflowContext — shared mutable state for a single workflow instance.

A workflow instance has:
- an `instance_id` (uuid)
- the `trigger` payload that started it
- per-step outputs accumulated as the DAG executes
- the `ResolvedCapabilities` for the currently-executing step (set by the
  engine before each step), so deterministic step functions and the tools
  they call can enforce ACLs the same way agentic steps do.

Steps read from prior step outputs and from the trigger payload.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from workflow_platform.connectors.base import Connector
from workflow_platform.security import ResolvedCapabilities
from workflow_platform.workflow.definition import LearnedMemorySpec


class WorkflowContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    instance_id: str
    workflow_id: str
    org_id: str = "default"
    trigger: dict[str, Any] = Field(default_factory=dict)
    steps: dict[str, dict[str, Any]] = Field(default_factory=dict)
    capabilities: ResolvedCapabilities | None = None
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    #: This run executed in the dry-run sandbox (MockWorld + stubbed external
    #: tools). Set from the engine at run start, so it is persisted by the
    #: FIRST `_mark_instance` and survives whatever happens next.
    #:
    #: It used to be tagged onto `instance.context` by the API *after* the
    #: run. That made it invisible to the engine, and the resume path — which
    #: rebuilds the context from scratch — erased it. So a dry run could be
    #: retried, would execute against the REAL world, and would lose the only
    #: evidence it had ever been a dry run (found 2026-09-19 by retrying one).
    dry_run: bool = False
    # Per-run connectors that need lifecycle tied to the workflow run (e.g.
    # `PlaywrightConnector`). Engine lazy-builds these in `_drive`'s
    # try/finally; tools look up via `ToolContext.connectors`. Excluded
    # from `model_dump()` so connector instances don't leak into the
    # JSON-serialized `instance.context`.
    connectors: dict[str, Connector] = Field(default_factory=dict, exclude=True)
    # The definition's learned_memory block, stashed at run start so agentic
    # steps can recall per-entity history (G10) without threading the whole
    # definition through the dispatch chain. Runtime-only, like `connectors`.
    learned_memory_spec: LearnedMemorySpec | None = Field(default=None, exclude=True)

    def step_output(self, step_id: str) -> dict[str, Any] | None:
        return self.steps.get(step_id)

    def record_step_output(self, step_id: str, output: dict[str, Any]) -> None:
        self.steps[step_id] = output
