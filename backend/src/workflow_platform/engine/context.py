"""WorkflowContext — shared mutable state for a single workflow instance.

A workflow instance has:
- an `instance_id` (uuid)
- the `trigger` payload that started it
- per-step outputs accumulated as the DAG executes

Steps read from prior step outputs and from the trigger payload.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WorkflowContext(BaseModel):
    instance_id: str
    workflow_id: str
    trigger: dict[str, Any] = Field(default_factory=dict)
    steps: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def step_output(self, step_id: str) -> dict[str, Any] | None:
        return self.steps.get(step_id)

    def record_step_output(self, step_id: str, output: dict[str, Any]) -> None:
        self.steps[step_id] = output
