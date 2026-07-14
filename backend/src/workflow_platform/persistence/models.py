"""Runtime persistence models.

These describe the *state* a workflow accumulates as it runs, distinct from the
declarative `WorkflowDefinition`. Every long-lived value the system needs to
look up later goes through one of these.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


class WorkflowInstanceState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


class StepExecutionState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowInstance(BaseModel):
    id: str = Field(default_factory=_new_id)
    workflow_id: str
    state: WorkflowInstanceState = WorkflowInstanceState.PENDING
    trigger_payload: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class StepExecution(BaseModel):
    id: str = Field(default_factory=_new_id)
    instance_id: str
    step_id: str
    state: StepExecutionState = StepExecutionState.PENDING
    output: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AuditEntry(BaseModel):
    """Append-only record of an action performed by a human or an agent.

    `action` is a stable, machine-friendly identifier (`workflow_started`,
    `step_completed`, `tool_call`, ...). `detail` carries action-specific data;
    its shape is documented per-action by the emitter.
    """

    id: str = Field(default_factory=_new_id)
    timestamp: datetime = Field(default_factory=_utcnow)
    actor_type: str
    actor_id: str
    action: str
    workflow_instance_id: str | None = None
    step_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class TriggerCursorState(BaseModel):
    """Persisted poll position for a polling trigger (G9).

    `cursor` is the last-seen event timestamp; `seen_ids` is the recently
    fired event-id ring. Both are needed for a loss-free AND duplicate-free
    restart: Gmail's `after:` is second-granular and inclusive, so the
    boundary message always re-matches — the persisted ids absorb it.
    """

    cursor: datetime
    seen_ids: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_utcnow)
