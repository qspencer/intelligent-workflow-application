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
    # A step cancelled because a sibling branch failed, or the instance was
    # paused/killed mid-flight (external review 2026-08-01). Distinct from
    # FAILED (its own failure) and PENDING (never scheduled) so recovery and
    # audit can tell a started-and-cancelled step from an unscheduled one.
    # Not in `already_done`, so a paused instance re-runs it on resume.
    CANCELLED = "cancelled"


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


LOCAL_ISSUER = "local"


class User(BaseModel):
    """A persisted platform user. `(iss, sub)` is the stable join key — sub
    alone is not globally unique across issuers. Two origins share the row
    shape (docs/AUTH_PLAN.md):

    - JIT-provisioned from the IdP identity on first authenticated request
      (`oidc`/`dev` modes). Authn and roles stay with the IdP per
      ARCHITECTURE D4; `password_hash` stays None and `roles` stays [].
    - Admin-created local users (`AUTH_MODE=local`): `iss="local"`,
      `sub=<row id>`, an Argon2id `password_hash`, and DB-assigned `roles`.

    The audit log's `actor_id` remains the raw sub string by design — audit
    entries must not dangle or mutate when users are reorganized."""

    id: str = Field(default_factory=_new_id)
    iss: str
    sub: str
    email: str | None = None
    display_name: str | None = None
    org_id: str = DEFAULT_ORG_ID
    password_hash: str | None = None
    roles: list[str] = Field(default_factory=list)
    is_active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    last_seen_at: datetime = Field(default_factory=_utcnow)


class AuthSession(BaseModel):
    """A server-side login session (`AUTH_MODE=local`). The opaque token
    lives only in the user's cookie; this row stores its sha256 — a DB read
    never yields a usable credential. Deleting the row is revocation."""

    id: str = Field(default_factory=_new_id)
    user_id: str
    token_hash: str
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    last_seen_at: datetime = Field(default_factory=_utcnow)


class RawTraceGrantState(StrEnum):
    """Grant lifecycle (docs/TRACE_GOVERNANCE_PLAN.md §2, TG1). Legal
    transitions: pending → {active, rejected, cancelled};
    active → {revoked, expired}."""

    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    REVOKED = "revoked"
    EXPIRED = "expired"


class RawTraceApprovalMode(StrEnum):
    """How a platform-wide grant activates (TRACE_GOVERNANCE_PLAN §2.1). An
    org-scoped grant always uses `dual_administrator` (a different Admin as
    grantor); `tenant_authorized` requires an external approval artifact and
    is only meaningful once a real external tenant exists."""

    DUAL_ADMINISTRATOR = "dual_administrator"
    TENANT_AUTHORIZED = "tenant_authorized"


class RawTraceReasonCode(StrEnum):
    """CLOSED reason enum — NOT free-form prose (TRACE_GOVERNANCE_PLAN §2/F2:
    a bounded note would still hold a pasted email body). Governance detail
    that carries customer content lives in a separately-governed record."""

    INCIDENT_INVESTIGATION = "incident_investigation"
    CUSTOMER_SUPPORT = "customer_support"
    COMPLIANCE_AUDIT = "compliance_audit"
    DEBUGGING = "debugging"


class RawTraceGrant(BaseModel):
    """The raw-trace read privilege, distinct from ordinary administration
    (TRACE_GOVERNANCE_PLAN §2). A grant is a state machine, not a boolean:
    default-off for everyone including Administrators. `org_id is None` means
    platform-wide (covers every org); otherwise it covers only that org."""

    id: str = Field(default_factory=_new_id)
    principal_id: str  # the User.id the grant is for
    org_id: str | None = None  # None = platform-wide (exactly one of org/platform)
    state: RawTraceGrantState = RawTraceGrantState.PENDING
    approval_mode: RawTraceApprovalMode = RawTraceApprovalMode.DUAL_ADMINISTRATOR
    requested_by: str
    requested_at: datetime = Field(default_factory=_utcnow)
    approved_by: str | None = None
    approved_at: datetime | None = None
    external_approval_ref: str | None = None
    expires_at: datetime | None = None  # mandatory (non-null) for platform-wide
    reason_code: RawTraceReasonCode = RawTraceReasonCode.DEBUGGING
    ticket_ref: str | None = None  # OPAQUE internal id, never free-form prose
    revoked_by: str | None = None
    revoked_at: datetime | None = None

    @property
    def is_platform_wide(self) -> bool:
        return self.org_id is None

    def is_active(self, now: datetime) -> bool:
        """ACTIVE and not past expiry. Expiry is authoritative at check time
        even before the row is transitioned to EXPIRED (TRACE_GOVERNANCE_PLAN
        §2/F6)."""
        if self.state is not RawTraceGrantState.ACTIVE:
            return False
        return self.expires_at is None or self.expires_at > now

    def covers(self, target_org: str | None, now: datetime) -> bool:
        """Active AND scope-covers the target org. A platform-wide grant
        covers any org; an org-scoped grant covers only its own — an org-A
        grant does NOT travel to org B (TRACE_GOVERNANCE_PLAN §2)."""
        if not self.is_active(now):
            return False
        return self.is_platform_wide or self.org_id == target_org
