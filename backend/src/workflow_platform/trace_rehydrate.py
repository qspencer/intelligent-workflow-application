"""Raw-trace rehydration read-back + system-access audit
(docs/TRACE_GOVERNANCE_PLAN.md §4.3/§3.2, TG3b).

Reconstructs a full step output / trigger payload from the vault (the flip's
precondition: raw can be read back faithfully before the operational store
stops holding it inline). Every engine-side vault read — resume, fork,
retry, and the read-surface merge for a grant-holder — is audit-BEFORE-fetch
and FAIL-CLOSED (§3.2): a `raw_trace_system_access_attempted` entry commits
before any fetch, and if that append fails no fetch occurs.

Integrity (§4.3): a kind the operational row shows as projected but the
vault lacks is a `retrieval_failed` — rehydration raises rather than silently
returning projected content.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from workflow_platform.persistence import AuditEntry, RawTrace, RawTraceKind, Repositories
from workflow_platform.trace_cipher import build_trace_cipher
from workflow_platform.trace_vault import idempotency_key

logger = logging.getLogger(__name__)

# The engine's narrow runtime identity for vault reads (not a human).
WORKLOAD_IDENTITY = "engine"

# Which output field each raw kind restores.
_OUTPUT_FIELD: dict[RawTraceKind, str] = {
    RawTraceKind.TOOL_CALLS: "tool_calls",
    RawTraceKind.MODEL_OUTPUT: "output_text",
    RawTraceKind.RECALL: "recall",
}


class RawTraceUnavailable(Exception):
    """A required raw trace could not be read back (missing / audit-closed) —
    rehydration fails closed rather than returning projected content."""


class RawTraceRehydrator:
    def __init__(
        self, repositories: Repositories, workload_identity: str = WORKLOAD_IDENTITY
    ) -> None:
        self._repos = repositories
        self._workload = workload_identity
        # Contract B1: decrypt sealed vault payloads (matches the vault's cipher
        # via the shared env master key). None = plaintext vault.
        self._cipher = build_trace_cipher()

    def _payload_of(self, row: RawTrace) -> Any:
        """The row's raw content — decrypted + integrity-verified when sealed
        (the AEAD binds it to org/instance/attempt/kind, so a substituted or
        edited row fails to open)."""
        if self._cipher is not None and self._cipher.is_sealed(row.payload):
            return self._cipher.open(
                row.payload,
                org_id=row.org_id,
                instance_id=row.instance_id,
                step_attempt_id=row.step_attempt_id,
                kind=row.kind.value,
                schema_version=row.raw_schema_version,
            )
        return row.payload

    async def _begin(
        self,
        *,
        purpose: str,
        org_id: str,
        instance_id: str,
        step_attempt_id: str | None,
        kinds: list[str],
    ) -> str:
        """Commit the system-access attempt BEFORE any fetch/decrypt (§3.2).
        Raises `RawTraceUnavailable` fail-closed if the audit can't be
        recorded — access we can't record is access we don't take."""
        request_id = uuid.uuid4().hex
        try:
            await self._repos.audit.append(
                AuditEntry(
                    actor_type="system",
                    actor_id=self._workload,
                    action="raw_trace_system_access_attempted",
                    workflow_instance_id=instance_id,
                    step_id=step_attempt_id,
                    detail={
                        "request_id": request_id,
                        "workload_identity": self._workload,
                        "purpose": purpose,
                        "org_id": org_id,
                        "kinds": sorted(kinds),
                    },
                )
            )
        except Exception as exc:
            logger.warning("system-access audit append failed; fail-closed", exc_info=True)
            raise RawTraceUnavailable("system-access audit unavailable") from exc
        return request_id

    async def _complete(self, *, request_id: str, instance_id: str, outcome: str) -> None:
        try:
            await self._repos.audit.append(
                AuditEntry(
                    actor_type="system",
                    actor_id=self._workload,
                    action="raw_trace_system_access_completed",
                    workflow_instance_id=instance_id,
                    detail={"request_id": request_id, "outcome": outcome},
                )
            )
        except Exception:
            logger.warning("system-access completion audit append failed", exc_info=True)

    async def rehydrate_output(
        self,
        *,
        purpose: str,
        org_id: str,
        instance_id: str,
        step_attempt_id: str,
        safe_output: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the full step output: the safe operational fields with the
        raw kinds (tool_calls / output_text / recall) overlaid from the vault.
        Audited + fail-closed; a projected-but-missing kind raises."""
        kinds = [k for k in _OUTPUT_FIELD if _is_projected(safe_output, k)]
        if not kinds:
            return safe_output
        request_id = await self._begin(
            purpose=purpose,
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=step_attempt_id,
            kinds=[k.value for k in kinds],
        )
        merged = dict(safe_output)
        for kind in kinds:
            key = idempotency_key(org_id, instance_id, step_attempt_id, kind)
            row = await self._repos.raw_trace_vault.get_by_idempotency_key(key)
            if row is None:
                await self._complete(
                    request_id=request_id, instance_id=instance_id, outcome="retrieval_failed"
                )
                raise RawTraceUnavailable(
                    f"missing vault raw for {kind.value} of {step_attempt_id}"
                )
            merged[_OUTPUT_FIELD[kind]] = self._payload_of(row)
        await self._complete(request_id=request_id, instance_id=instance_id, outcome="succeeded")
        return merged

    async def rehydrate_trigger(
        self, *, purpose: str, org_id: str, instance_id: str, safe_trigger: dict[str, Any]
    ) -> dict[str, Any]:
        """Return the full trigger payload from the vault (instance-level).
        If nothing is vaulted, the safe trigger is returned as-is (a trigger
        with no sensitive content was never projected)."""
        key = idempotency_key(org_id, instance_id, None, RawTraceKind.TRIGGER_PAYLOAD)
        request_id = await self._begin(
            purpose=purpose,
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=None,
            kinds=[RawTraceKind.TRIGGER_PAYLOAD.value],
        )
        row = await self._repos.raw_trace_vault.get_by_idempotency_key(key)
        # A trigger with no vaulted row simply had no sensitive content — not
        # a failure; the safe trigger is complete.
        await self._complete(request_id=request_id, instance_id=instance_id, outcome="succeeded")
        if row is None:
            return safe_trigger
        opened = self._payload_of(row)
        return opened if isinstance(opened, dict) else safe_trigger

    async def merge_output(
        self, *, org_id: str, instance_id: str, step_attempt_id: str, safe_output: dict[str, Any]
    ) -> dict[str, Any]:
        """Read-surface overlay for a grant-holder (TG3b.3). The HUMAN access
        is already audited via the release-boundary path (§3.1), so this emits
        NO system-access audit. Best-effort: a missing vault row leaves the
        projected field (the read degrades, it does not 500). A no-op when
        nothing is projected — so it's harmless under the default dark
        dual-write, where the operational row already holds raw."""
        merged = dict(safe_output)
        for kind in (k for k in _OUTPUT_FIELD if _is_projected(safe_output, k)):
            key = idempotency_key(org_id, instance_id, step_attempt_id, kind)
            row = await self._repos.raw_trace_vault.get_by_idempotency_key(key)
            if row is not None:
                merged[_OUTPUT_FIELD[kind]] = self._payload_of(row)
        return merged

    async def merge_trigger(
        self, *, org_id: str, instance_id: str, safe_trigger: dict[str, Any]
    ) -> dict[str, Any]:
        """Read-surface trigger overlay (no system-access audit — see
        `merge_output`)."""
        key = idempotency_key(org_id, instance_id, None, RawTraceKind.TRIGGER_PAYLOAD)
        row = await self._repos.raw_trace_vault.get_by_idempotency_key(key)
        if row is not None:
            opened = self._payload_of(row)
            if isinstance(opened, dict):
                return opened
        return safe_trigger


def _is_projected(safe_output: dict[str, Any], kind: RawTraceKind) -> bool:
    """Whether the safe operational output has this raw kind projected out
    (so it must be rehydrated). A tool-call list projected to metadata, a
    withheld `output_text`, or a redacted `recall` marker all qualify."""
    field = _OUTPUT_FIELD[kind]
    value = safe_output.get(field)
    if value is None:
        return False
    if kind is RawTraceKind.TOOL_CALLS and isinstance(value, list):
        return any(isinstance(c, dict) and "_redacted" in c for c in value)
    if kind is RawTraceKind.MODEL_OUTPUT and isinstance(value, str):
        return value.startswith("[redacted")
    if kind is RawTraceKind.RECALL and isinstance(value, str):
        return value.startswith("[redacted")
    return False
