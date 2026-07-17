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
    # Tenant attribution from birth (single-org today). On the instance
    # directly — the fastest-growing table; cost/audit queries want org
    # attribution without joins.
    org_id: str = "default"
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


DEFAULT_ORG_ID = "default"


class Organization(BaseModel):
    """A tenant boundary. Single-org today (the migration seeds `default`);
    the column plumbing exists so features scope by org from birth instead
    of being retrofitted."""

    id: str = Field(default_factory=_new_id)
    name: str
    created_at: datetime = Field(default_factory=_utcnow)


class User(BaseModel):
    """A persisted platform user, JIT-provisioned from the IdP identity on
    first authenticated request. `(iss, sub)` is the stable join key — sub
    alone is not globally unique across issuers. Authn and roles stay with
    the IdP (ARCHITECTURE D4): this row exists so features (ownership,
    per-user memory) have a stable id to reference, never for passwords.
    The audit log's `actor_id` remains the raw sub string by design — audit
    entries must not dangle or mutate when users are reorganized."""

    id: str = Field(default_factory=_new_id)
    iss: str
    sub: str
    email: str | None = None
    display_name: str | None = None
    org_id: str = DEFAULT_ORG_ID
    created_at: datetime = Field(default_factory=_utcnow)
    last_seen_at: datetime = Field(default_factory=_utcnow)
