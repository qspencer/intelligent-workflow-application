"""Raw-trace vault write path + projector (docs/TRACE_GOVERNANCE_PLAN.md
§1.2/§1.4/§4.1, TG3a).

The projector classifies which parts of a step output / trigger payload are
RAW (default-deny for free-form content, per the safe-output contract §1.4):
tool-call input/result, free-form model output (taint, §1.1), recalled
correspondent history, and error text are raw; structured/enum fields
(category, scores, status) are not. `RawTraceVault` writes the raw parts to
the vault keyed on the immutable step-attempt.

TG3a is a DARK DUAL-WRITE: raw is copied into the vault while the operational
store keeps its inline copy authoritative (the flip to safe-only is TG3b). A
vault write failure is therefore logged, never raised — the inline copy is
still the source of truth in this phase.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from workflow_platform.persistence import RawTrace, RawTraceKind, Repositories
from workflow_platform.persistence.models import RAW_SCHEMA_VERSION
from workflow_platform.persistence.repository import VaultConflict
from workflow_platform.trace_cipher import build_trace_cipher
from workflow_platform.trace_projection import (
    project_audit_detail_final,
    redact_tool_data,
)

logger = logging.getLogger(__name__)


def output_has_raw(output: dict[str, Any]) -> bool:
    """Whether a step output carries anything the below-grant projection would
    redact — i.e. whether it needs a vault object at all (external code review
    2026-08-02 F1). Uses the SAME default-deny projector as the read surface,
    so nothing the operational store would strip is left unvaulted."""
    return bool(redact_tool_data(output, admin=False, kind="step_output") != output)


def audit_detail_has_raw(action: str | None, detail: Any) -> bool:
    """Whether an audit detail LOSES anything to at-rest projection — i.e.
    whether it needs a vault object at all.

    THE predicate behind audit-detail vaulting, and deliberately a CALL into
    the projection rather than a description of it: the rule is "vault exactly
    what projection would take away", so it cannot drift from what projection
    actually does. Round 11 named the trap this closes — if the scope were a
    snapshot of today's lenient policy, every detail that currently passes
    through unchanged would go unvaulted and start losing information the
    moment at-rest filtering is tightened.

    NOT the same question the zero-raw verifier asks. The verifier asks "does
    this STORED row still contain raw under the policy in force?" — today's
    lenient at-rest denylist. This asks "will the FINAL policy take anything
    away?" During the transition those differ, and collapsing them into one
    function would either stop the verifier certifying anything or scope
    vaulting to the wrong policy. They converge when the tightening lands.
    """
    return bool(project_audit_detail_final(action, detail) != detail)


def audit_idempotency_key(org_id: str, instance_id: str, audit_entry_id: str) -> str:
    """Deterministic key for an AUDIT_DETAIL object, keyed on the audit entry.

    A separate space from `idempotency_key`: that one anchors on the step
    attempt, and one step attempt emits MANY audit entries, so re-using it
    would make every tool call on an attempt collide onto one row. Re-driving
    the SAME entry (same id) re-addresses the same object; two different
    entries never collide, because `AuditEntry.id` is unique.
    """
    raw = "\x00".join((org_id, instance_id, "audit", audit_entry_id))
    return hashlib.sha256(raw.encode()).hexdigest()


def idempotency_key(
    org_id: str, instance_id: str, step_attempt_id: str | None, kind: RawTraceKind
) -> str:
    """Deterministic, collision-free (docs/TRACE_GOVERNANCE_PLAN.md §4.2/F2):
    keyed on the IMMUTABLE step-attempt id, so two different steps on the same
    attempt number get distinct objects; instance-level (trigger) rows use the
    literal "instance" anchor, a separate space. A retry re-addresses the same
    object."""
    anchor = step_attempt_id or "instance"
    raw = "\x00".join((org_id, instance_id, anchor, kind.value))
    return hashlib.sha256(raw.encode()).hexdigest()


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


class RawTraceVault:
    def __init__(self, repositories: Repositories) -> None:
        self._repos = repositories
        # Contract B1 (TG3d-1): when a master key is configured, vault payloads
        # are sealed (per-org AES-GCM, AEAD-bound). None = Contract-A plaintext.
        self._cipher = build_trace_cipher()

    async def _record(
        self,
        *,
        org_id: str,
        instance_id: str,
        step_attempt_id: str | None,
        kind: RawTraceKind,
        payload: Any,
        durable: bool,
        audit_entry_id: str | None = None,
    ) -> RawTrace | None:
        if _empty(payload):
            return None
        # P4: commit to the PLAINTEXT before sealing, so an idempotent put can
        # distinguish "same immutable write" from "different content under the
        # same key" without the repository holding a key (ciphertext nonces
        # differ per seal, so ciphertext itself can't be compared).
        commitment = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        stored = payload
        if self._cipher is not None:
            stored = self._cipher.seal(
                payload,
                org_id=org_id,
                instance_id=instance_id,
                step_attempt_id=step_attempt_id,
                kind=kind.value,
                schema_version=RAW_SCHEMA_VERSION,
            )
        trace = RawTrace(
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=step_attempt_id,
            audit_entry_id=audit_entry_id,
            kind=kind,
            idempotency_key=(
                audit_idempotency_key(org_id, instance_id, audit_entry_id)
                if audit_entry_id is not None
                else idempotency_key(org_id, instance_id, step_attempt_id, kind)
            ),
            payload=stored,
            content_commitment=commitment,
        )
        try:
            return await self._repos.raw_trace_vault.put(trace)
        except VaultConflict:
            # ALWAYS fatal, dark dual-write or not (P4): the same key already
            # holds DIFFERENT content, so continuing would leave the engine
            # believing it stored raw the vault does not hold.
            logger.error("raw-trace vault content conflict", exc_info=True)
            raise
        except Exception:
            if durable:
                # DURABLE (safe-only flip, TG3b): the operational store keeps
                # only the projection, so a lost vault write loses the raw —
                # the step/run must FAIL rather than silently drop it (§4.2).
                logger.warning("durable raw-trace vault write failed", exc_info=True)
                raise
            # Dark dual-write (TG3a): inline is authoritative, so a vault
            # failure must never fail the run. Log and move on.
            logger.warning("raw-trace vault write failed (dark dual-write)", exc_info=True)
            return None

    async def record_trigger(
        self, *, org_id: str, instance_id: str, payload: Any, durable: bool = False
    ) -> None:
        await self._record(
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=None,
            kind=RawTraceKind.TRIGGER_PAYLOAD,
            payload=payload,
            durable=durable,
        )

    async def record_step_output(
        self,
        *,
        org_id: str,
        instance_id: str,
        step_attempt_id: str,
        output: dict[str, Any],
        durable: bool = False,
    ) -> None:
        # Vault the FULL output (not per-field), so the default-deny projection
        # is lossless — ANY redacted field is recoverable on rehydration (F1).
        # A structured-only output with nothing to redact needs no vault row.
        if not output_has_raw(output):
            return
        await self._record(
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=step_attempt_id,
            kind=RawTraceKind.OUTPUT,
            payload=output,
            durable=durable,
        )

    async def record_error(
        self,
        *,
        org_id: str,
        instance_id: str,
        step_attempt_id: str | None,
        error: str | None,
        durable: bool = False,
    ) -> None:
        await self._record(
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=step_attempt_id,
            kind=RawTraceKind.ERROR,
            payload=error,
            durable=durable,
        )

    async def record_audit_detail(
        self,
        *,
        org_id: str,
        instance_id: str,
        audit_entry_id: str,
        action: str | None,
        detail: Any,
        durable: bool = True,
    ) -> None:
        """Vault one audit entry's raw detail, BEFORE the entry is appended.

        Only when at-rest projection would take something away — that is the
        whole scope rule, expressed as a call so it cannot drift.

        `durable=True` by DEFAULT here, unlike the other record_* methods: at
        the point this is called the operational store is about to keep only
        the projection, so a lost vault write loses the raw for good. It must
        fail the step rather than silently drop it.
        """
        if not audit_detail_has_raw(action, detail):
            return
        await self._record(
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=None,
            audit_entry_id=audit_entry_id,
            kind=RawTraceKind.AUDIT_DETAIL,
            payload=detail,
            durable=durable,
        )
