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
from workflow_platform.persistence.models import RAW_SCHEMA_VERSION
from workflow_platform.trace_cipher import build_trace_cipher
from workflow_platform.trace_vault import idempotency_key

logger = logging.getLogger(__name__)

# The engine's narrow runtime identity for vault reads (not a human).
WORKLOAD_IDENTITY = "engine"


def _output_projected(obj: Any) -> bool:
    """Whether a safe operational output was projected (so its full raw must be
    rehydrated from the vault). True if ANY default-deny redaction marker
    appears anywhere: a `[redacted …` string or a dict carrying `_redacted`
    (a projected tool-call or trigger). F1: detects redaction of any field,
    not just the three legacy kinds."""
    if isinstance(obj, str):
        return obj.startswith("[redacted")
    if isinstance(obj, dict):
        if "_redacted" in obj:
            return True
        return any(_output_projected(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_output_projected(v) for v in obj)
    return False


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

    def _payload_of(
        self,
        row: RawTrace,
        *,
        org_id: str,
        instance_id: str,
        step_attempt_id: str | None,
        kind: str,
    ) -> Any:
        """The row's raw content, bound to the EXPECTED identity the caller
        asked for — never to metadata trusted from the row (external code
        review 2026-08-02 F6). Two DB-operator attacks this closes:

        - **Substitution:** the fetched row's `(org, instance, attempt, kind)`
          MUST equal the expected tuple, else raise — a row moved under another
          key is rejected.
        - **Downgrade:** under encryption an unsealed payload is REJECTED (a DB
          operator cannot slip plaintext past the cipher).

        Decryption uses the EXPECTED tuple as AEAD associated data (+ the
        constant schema version we sealed with), so a relabeled/wrong-org
        ciphertext fails to open even if its stored metadata was tampered."""
        if (row.org_id, row.instance_id, row.step_attempt_id, row.kind.value) != (
            org_id,
            instance_id,
            step_attempt_id,
            kind,
        ):
            raise RawTraceUnavailable(f"vault row identity mismatch for {kind}")
        if self._cipher is not None:
            if not self._cipher.is_sealed(row.payload):
                raise RawTraceUnavailable("unsealed vault payload under encryption (downgrade)")
            return self._cipher.open(
                row.payload,
                org_id=org_id,
                instance_id=instance_id,
                step_attempt_id=step_attempt_id,
                kind=kind,
                schema_version=RAW_SCHEMA_VERSION,
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
        """Return the FULL step output from the vault (F1: the vault holds the
        whole raw output, so any default-deny-redacted field is restored, not
        just tool_calls/output_text/recall). Audited + fail-closed; a projected
        output whose vault object is missing raises."""
        if not _output_projected(safe_output):
            return safe_output
        request_id = await self._begin(
            purpose=purpose,
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=step_attempt_id,
            kinds=[RawTraceKind.OUTPUT.value],
        )
        key = idempotency_key(org_id, instance_id, step_attempt_id, RawTraceKind.OUTPUT)
        row = await self._repos.raw_trace_vault.get_by_idempotency_key(key)
        if row is None:
            await self._complete(
                request_id=request_id, instance_id=instance_id, outcome="retrieval_failed"
            )
            raise RawTraceUnavailable(f"missing vault output for {step_attempt_id}")
        full = self._payload_of(
            row,
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=step_attempt_id,
            kind=RawTraceKind.OUTPUT.value,
        )
        await self._complete(request_id=request_id, instance_id=instance_id, outcome="succeeded")
        return full if isinstance(full, dict) else safe_output

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
        if row is None:
            # A trigger the projection MARKED as redacted but the vault lacks is
            # a retrieval failure — must NOT resume on projected input while the
            # audit says success (external code review 2026-08-02 F7). A trigger
            # with no marker simply had no sensitive content.
            if "_redacted" in safe_trigger:
                await self._complete(
                    request_id=request_id, instance_id=instance_id, outcome="retrieval_failed"
                )
                raise RawTraceUnavailable(
                    f"missing vault raw for projected trigger of {instance_id}"
                )
            await self._complete(
                request_id=request_id, instance_id=instance_id, outcome="succeeded"
            )
            return safe_trigger
        opened = self._payload_of(
            row,
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=None,
            kind=RawTraceKind.TRIGGER_PAYLOAD.value,
        )
        await self._complete(request_id=request_id, instance_id=instance_id, outcome="succeeded")
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
        if not _output_projected(safe_output):
            return safe_output
        key = idempotency_key(org_id, instance_id, step_attempt_id, RawTraceKind.OUTPUT)
        row = await self._repos.raw_trace_vault.get_by_idempotency_key(key)
        if row is None:
            return safe_output  # best-effort; the caller reports partial (F8)
        full = self._payload_of(
            row,
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=step_attempt_id,
            kind=RawTraceKind.OUTPUT.value,
        )
        return full if isinstance(full, dict) else safe_output

    async def merge_trigger(
        self, *, org_id: str, instance_id: str, safe_trigger: dict[str, Any]
    ) -> dict[str, Any]:
        """Read-surface trigger overlay (no system-access audit — see
        `merge_output`)."""
        key = idempotency_key(org_id, instance_id, None, RawTraceKind.TRIGGER_PAYLOAD)
        row = await self._repos.raw_trace_vault.get_by_idempotency_key(key)
        if row is not None:
            opened = self._payload_of(
                row,
                org_id=org_id,
                instance_id=instance_id,
                step_attempt_id=None,
                kind=RawTraceKind.TRIGGER_PAYLOAD.value,
            )
            if isinstance(opened, dict):
                return opened
        return safe_trigger
