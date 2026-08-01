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

logger = logging.getLogger(__name__)

# Keys on an agentic step output that are RAW (default-deny for free-form).
# `tool_calls` carries raw input/result; `output_text` is free-form model
# text (taint: any raw-influenced output is raw); `recall` is third-party
# correspondent history.
_OUTPUT_RAW_KINDS: tuple[tuple[str, RawTraceKind], ...] = (
    ("tool_calls", RawTraceKind.TOOL_CALLS),
    ("output_text", RawTraceKind.MODEL_OUTPUT),
    ("recall", RawTraceKind.RECALL),
)


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


def raw_kinds_of_output(output: dict[str, Any]) -> dict[RawTraceKind, Any]:
    """The raw assets present in a step output (the parts a below-grant reader
    must not see). Structured fields are left out — they are safe."""
    found: dict[RawTraceKind, Any] = {}
    for field, kind in _OUTPUT_RAW_KINDS:
        value = output.get(field)
        if not _empty(value):
            found[kind] = value
    return found


class RawTraceVault:
    def __init__(self, repositories: Repositories) -> None:
        self._repos = repositories

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
        trace = RawTrace(
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=step_attempt_id,
            kind=kind,
            idempotency_key=idempotency_key(org_id, instance_id, step_attempt_id, kind),
            payload=payload,
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
        for kind, payload in raw_kinds_of_output(output).items():
            await self._record(
                org_id=org_id,
                instance_id=instance_id,
                step_attempt_id=step_attempt_id,
                kind=kind,
                payload=payload,
                durable=durable,
            )

    async def record_error(
        self,
        *,
        org_id: str,
        instance_id: str,
        step_attempt_id: str,
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
