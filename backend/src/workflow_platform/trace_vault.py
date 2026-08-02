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
import logging
from typing import Any

from workflow_platform.persistence import RawTrace, RawTraceKind, Repositories
from workflow_platform.persistence.models import RAW_SCHEMA_VERSION
from workflow_platform.trace_cipher import build_trace_cipher
from workflow_platform.trace_projection import redact_tool_data

logger = logging.getLogger(__name__)


def output_has_raw(output: dict[str, Any]) -> bool:
    """Whether a step output carries anything the below-grant projection would
    redact — i.e. whether it needs a vault object at all (external code review
    2026-08-02 F1). Uses the SAME default-deny projector as the read surface,
    so nothing the operational store would strip is left unvaulted."""
    return bool(redact_tool_data(output, admin=False) != output)


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
    ) -> RawTrace | None:
        if _empty(payload):
            return None
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
            kind=kind,
            idempotency_key=idempotency_key(org_id, instance_id, step_attempt_id, kind),
            payload=stored,
        )
        try:
            return await self._repos.raw_trace_vault.put(trace)
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
