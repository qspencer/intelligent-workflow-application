"""Runtime persistence models.

These describe the *state* a workflow accumulates as it runs, distinct from the
declarative `WorkflowDefinition`. Every long-lived value the system needs to
look up later goes through one of these.
"""

from __future__ import annotations

import json
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
    # 1-based attempt number. Each attempt is its own immutable row (the
    # step-attempt identity the raw-traces vault keys on) — a retry appends a
    # new row, never mutates a prior one. See docs/EXECUTION_SEMANTICS.md §3a.
    attempt: int = 1
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


class RawTraceKind(StrEnum):
    """The raw asset a vault row holds (docs/TRACE_GOVERNANCE_PLAN.md §1/§4.1).
    Unknown kinds are default-deny → vault-only (§1.2), never operational."""

    OUTPUT = "output"  # the FULL step output (default-deny lossless vault, F1)
    TOOL_CALLS = "tool_calls"  # legacy per-kind (pre-F1 vault rows)
    MODEL_OUTPUT = "model_output"  # legacy per-kind — free-form model text
    TRIGGER_PAYLOAD = "trigger_payload"
    RECALL = "recall"  # legacy per-kind
    ERROR = "error"


def vault_fingerprint(trace: RawTrace) -> tuple[Any, ...]:
    """Identity + content of a vault object, for idempotent-put conflict
    detection (P4). Uses the plaintext `content_commitment` when present so
    re-sealing the same content is NOT a conflict; falls back to the raw
    payload for legacy plaintext rows."""
    return (
        trace.org_id,
        trace.instance_id,
        trace.step_attempt_id,
        trace.kind.value,
        trace.raw_schema_version,
        trace.projector_version,
        trace.content_commitment
        if trace.content_commitment is not None
        else json.dumps(trace.payload, sort_keys=True, default=str),
    )


class RawTraceState(StrEnum):
    """Vault object lifecycle (docs/TRACE_GOVERNANCE_PLAN.md §4.2). Under
    Contract A (same-DB) the states collapse into one transaction and a row is
    written directly COMMITTED; RESERVED/STORED/REFERENCED/ABORTED are the
    separate-vault (B) fencing protocol, carried on the row so the B form
    slots in without a migration."""

    RESERVED = "reserved"
    STORED = "stored"
    REFERENCED = "referenced"
    COMMITTED = "committed"
    ABORTED = "aborted"


# Version stamps recorded on every vault row + the operational row that
# references it, so a projector/schema change never makes an old row read as
# corrupt (docs/TRACE_GOVERNANCE_PLAN.md §4.3, criterion 17/23).
RAW_SCHEMA_VERSION = 1
PROJECTION_SCHEMA_VERSION = 1
PROJECTOR_VERSION = "trace-projector@1"


class RawTrace(BaseModel):
    """One raw asset in the vault, keyed on the immutable step-attempt
    (docs/TRACE_GOVERNANCE_PLAN.md §4.1). `step_attempt_id` is the
    `StepExecution.id` of the producing attempt; None for an instance-level
    trigger payload. The `idempotency_key` (a deterministic hash) is the
    natural key — a retry re-addresses the SAME object, and two different
    steps on the same attempt number get DISTINCT objects (F2)."""

    id: str = Field(default_factory=_new_id)
    org_id: str = DEFAULT_ORG_ID
    instance_id: str
    step_attempt_id: str | None = None  # None = instance-level (trigger)
    kind: RawTraceKind
    state: RawTraceState = RawTraceState.COMMITTED
    idempotency_key: str
    raw_schema_version: int = RAW_SCHEMA_VERSION
    projection_schema_version: int = PROJECTION_SCHEMA_VERSION
    projector_version: str = PROJECTOR_VERSION
    payload: Any = None  # plaintext JSON under Contract A; ciphertext under B (TG3d)
    # P4 (re-review finding 7): a hash over the PLAINTEXT content, set by the
    # vault before sealing. Ciphertext gets a fresh nonce per seal, so it cannot
    # be compared directly; this commitment lets an idempotent `put` tell "same
    # immutable write" from "different content under the same key" without the
    # repository holding any key. None on pre-P4 rows (fall back to payload).
    content_commitment: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
