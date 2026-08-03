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
from workflow_platform.persistence.models import RAW_SCHEMA_VERSION, RawTraceState
from workflow_platform.trace_cipher import build_trace_cipher, is_sealed_payload
from workflow_platform.trace_projection import PROJECTOR_VERSION, redact_tool_data
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


PROJECTION_UNSUPPORTED = "projector_version_unsupported"


def verify_projection_agreement(
    raw: Any, stored_safe: Any, recorded_projector_version: str | None
) -> str:
    """The FROZEN §4.3 predicate: `project(raw, recorded_version) == stored`.

    Replaces the marker scan (re-review finding 3). Returns:
      "ok"          — re-projecting the fetched raw reproduces the stored safe
                      row exactly, so the pair is consistent;
      "mismatch"    — it does NOT, i.e. the operational row was tampered with or
                      the vault object does not belong to it → integrity failure;
      "unsupported" — the row was written by a projector version this build
                      cannot reproduce. NOT corrupt (criterion 17: a projector
                      bump must not make pre-change rows read corrupt) — the
                      caller degrades with an explicit audited outcome.
    """
    if recorded_projector_version != PROJECTOR_VERSION:
        return "unsupported"
    return "ok" if redact_tool_data(raw, admin=False) == stored_safe else "mismatch"


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
        # P3a (§4.3): the object must be USABLE — a RESERVED/ABORTED row is not
        # a committed write and must never be handed back as raw (re-review
        # finding 3 reproduced acceptance of an ABORTED row).
        if row.state is not RawTraceState.COMMITTED:
            raise RawTraceUnavailable(f"vault row {row.id} is {row.state.value}, not committed")
        if row.raw_schema_version != RAW_SCHEMA_VERSION:
            raise RawTraceUnavailable(
                f"vault row {row.id} raw_schema_version {row.raw_schema_version} unsupported"
            )
        sealed = is_sealed_payload(row.payload)
        # F4 (re-review): sealed-ness is decided from the ROW, independent of
        # whether a cipher is configured. A sealed row with no key MUST fail
        # closed — never return the AEAD envelope as if it were plaintext.
        if sealed and self._cipher is None:
            raise RawTraceUnavailable("sealed vault payload but no decryption key available")
        if self._cipher is not None:
            if not sealed:
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
        projector_version: str | None = None,
    ) -> dict[str, Any]:
        """Return the FULL step output from the vault.

        P3a (§4.3): whether raw is REQUIRED is decided by the row's PERSISTED
        projection stamp (`projector_version`), never by scanning the payload
        for markers — an operator who deletes a marker can no longer make this
        skip the vault. After fetching, the raw is re-projected and compared
        with the stored safe row; a mismatch is an integrity failure."""
        if projector_version is None:
            return safe_output  # never written as a projection → nothing vaulted
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
        # §4.3 agreement: re-project the fetched raw under the RECORDED version
        # and require it to reproduce the stored safe row.
        verdict = verify_projection_agreement(full, safe_output, projector_version)
        if verdict == "mismatch":
            await self._complete(
                request_id=request_id, instance_id=instance_id, outcome="integrity_failed"
            )
            raise RawTraceUnavailable(
                f"projection disagreement for {step_attempt_id}: the operational row does not "
                "match a re-projection of the vaulted raw"
            )
        await self._complete(
            request_id=request_id,
            instance_id=instance_id,
            outcome="succeeded" if verdict == "ok" else PROJECTION_UNSUPPORTED,
        )
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
        self,
        *,
        org_id: str,
        instance_id: str,
        step_attempt_id: str,
        safe_output: dict[str, Any],
        projector_version: str | None = None,
    ) -> dict[str, Any]:
        """Read-surface overlay for a grant-holder (TG3b.3). The HUMAN access
        is already audited via the release-boundary path (§3.1), so this emits
        NO system-access audit. Best-effort: a missing vault row leaves the
        projected field (the read degrades, it does not 500).

        P3a: prefers the PERSISTED stamp, but falls back to the marker scan for
        rows written before the stamp existed (the backfilled corpus carries no
        `projector_version`). The fallback is acceptable HERE and not on the
        execution path: a miss only means a grant-holder's read does not merge,
        with no effect on what the workflow runs on."""
        if projector_version is None and not _output_projected(safe_output):
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

    async def merge_error(
        self, *, org_id: str, instance_id: str, step_attempt_id: str | None, safe_error: Any
    ) -> Any:
        """Read-surface overlay for a grant-holder: restore a step's raw error
        text from the vault when the operational store holds only the marker
        (F2, no system-access audit — the human release is audited on the
        detail path). A no-op when the stored error isn't the marker."""
        if not (isinstance(safe_error, str) and safe_error.startswith("[redacted")):
            return safe_error
        key = idempotency_key(org_id, instance_id, step_attempt_id, RawTraceKind.ERROR)
        row = await self._repos.raw_trace_vault.get_by_idempotency_key(key)
        if row is None:
            return safe_error  # best-effort; caller reports partial (F8)
        return self._payload_of(
            row,
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=step_attempt_id,
            kind=RawTraceKind.ERROR.value,
        )

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
