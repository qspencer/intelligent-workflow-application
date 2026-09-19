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
from workflow_platform.trace_cipher import (
    TraceCipherError,
    build_trace_cipher,
    is_sealed_payload,
)
from workflow_platform.trace_projection import (
    PROJECTOR_VERSION,
    is_withheld_marker,
    project_audit_detail_at_rest,
    redact_tool_data,
)
from workflow_platform.trace_vault import (
    audit_idempotency_key,
    idempotency_key,
)

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
        # R6 F2: share ONE withholding predicate with the completeness path.
        # This detector had its own idea of what "projected" looks like, so an
        # object carrying only the withheld flag was skipped for restoration
        # while `has_redaction_marker` called the same object incomplete.
        if is_withheld_marker(obj):
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
    if not _version_reproducible(recorded_projector_version):
        return "unsupported"
    return (
        "ok"
        if redact_tool_data(raw, admin=False, kind="step_output") == stored_safe
        else "mismatch"
    )


def _version_reproducible(recorded_projector_version: str | None) -> bool:
    """Whether THIS build can reproduce a projection recorded under that
    version. One gate: audit details ask the same question, and asking it
    separately is how they came to disagree (R13 finding 5)."""
    return recorded_projector_version == PROJECTOR_VERSION


def verify_audit_projection_agreement(
    action: str, raw: Any, stored_detail: Any, recorded_projector_version: str | None
) -> str:
    """`verify_projection_agreement` for an AUDIT detail, which is projected
    action-aware rather than by asset kind.

    R13 finding 5: `rehydrate_audit_detail` compared with a bare `!=` and no
    version gate, so an entry stamped `UNSUPPORTED-999` returned its full
    detail and recorded SUCCESS. Same verdicts, and the same rule behind
    `unsupported`: a projector bump must not make pre-change rows read as
    corrupt (criterion 17)."""
    if not _version_reproducible(recorded_projector_version):
        return "unsupported"
    return "ok" if project_audit_detail_at_rest(action, raw) == stored_detail else "mismatch"


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
        audit_entry_id: str | None = None,
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
        if (
            row.org_id,
            row.instance_id,
            row.step_attempt_id,
            row.kind.value,
            row.audit_entry_id,
        ) != (org_id, instance_id, step_attempt_id, kind, audit_entry_id):
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
            try:
                return self._cipher.open(
                    row.payload,
                    org_id=org_id,
                    instance_id=instance_id,
                    step_attempt_id=step_attempt_id,
                    kind=kind,
                    schema_version=RAW_SCHEMA_VERSION,
                    audit_entry_id=audit_entry_id,
                )
            except TraceCipherError as exc:
                # R13 finding 4: a payload that will not open — tampered,
                # sealed under a rotated key, or bound to another identity —
                # is a RETRIEVAL outcome, not a server fault. It used to
                # escape as `TraceCipherError`, which the response boundary
                # does not catch, so the request 500'd and the release
                # decision was never completed: no raw for the reader AND no
                # record of the attempt's outcome.
                raise RawTraceUnavailable(
                    f"vault payload for {kind} could not be decrypted: {exc}"
                ) from exc
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

    async def _fetch_or_completed(
        self, key: str, *, request_id: str, instance_id: str, what: str
    ) -> RawTrace | None:
        """Vault lookup whose FAILURE completes the access record.

        R14 finding 2: the decryption repair wrapped `_payload_of`, but the
        LOOKUP sits outside it. A repository timeout therefore escaped as
        itself — HTTP 500, both access attempts recorded and neither
        completion nor release decision. A repository failure is an
        unavailable retrieval like any other, not a server fault.

        `None` (no such row) is NOT a failure here and is returned to the
        caller, which distinguishes "never vaulted" from "cannot be read".
        """
        try:
            return await self._repos.raw_trace_vault.get_by_idempotency_key(key)
        except Exception as exc:
            logger.warning("vault lookup failed for %s", what, exc_info=True)
            await self._complete(
                request_id=request_id, instance_id=instance_id, outcome="retrieval_failed"
            )
            raise RawTraceUnavailable(f"vault lookup failed for {what}: {exc}") from exc

    async def _opened_or_completed(
        self,
        row: RawTrace,
        *,
        request_id: str,
        org_id: str,
        instance_id: str,
        step_attempt_id: str | None,
        kind: str,
        audit_entry_id: str | None = None,
    ) -> Any:
        """`_payload_of`, but a failure COMPLETES the access record first.

        R13 finding 4 asked for decryption failures to become explicit
        recovery outcomes AND for the release audit to complete. Normalising
        `TraceCipherError` fixed the first half; this fixes the second. A
        `_payload_of` raise between `_begin` and `_complete` used to leave
        the system-access record open forever — the worst of both, since the
        reader gets nothing and the log does not say why.
        """
        try:
            return self._payload_of(
                row,
                org_id=org_id,
                instance_id=instance_id,
                step_attempt_id=step_attempt_id,
                kind=kind,
                audit_entry_id=audit_entry_id,
            )
        except RawTraceUnavailable:
            await self._complete(
                request_id=request_id, instance_id=instance_id, outcome="retrieval_failed"
            )
            raise

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
        row = await self._fetch_or_completed(
            key,
            request_id=request_id,
            instance_id=instance_id,
            what=f"step attempt {step_attempt_id}",
        )
        if row is None:
            await self._complete(
                request_id=request_id, instance_id=instance_id, outcome="retrieval_failed"
            )
            raise RawTraceUnavailable(f"missing vault output for {step_attempt_id}")
        full = await self._opened_or_completed(
            row,
            request_id=request_id,
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

    async def rehydrate_audit_detail(
        self,
        *,
        purpose: str,
        org_id: str,
        instance_id: str,
        audit_entry_id: str,
        action: str,
        stored_detail: Any,
        projector_version: str | None,
    ) -> Any:
        """Return an audit entry's FULL detail from the vault.

        R12 finding 1: the audit endpoints returned the STORED detail to a
        grant holder. That was correct while at rest held the raw; once the
        at-rest tightening landed, stored IS the projection, so a grant holder
        received `{"_withheld_keys": true}` while the release log recorded
        `released`. The vault row existed the whole time and nothing read it.

        Raises `RawTraceUnavailable` when the raw cannot be produced, so the
        caller can record `partial`/`retrieval_failed` rather than reporting a
        release it did not make.
        """
        if projector_version is None:
            # Never written as a projection, or projection took nothing — so
            # nothing was vaulted and the stored detail IS the full detail.
            #
            # Decided from the PERSISTED STAMP, never by re-running the
            # predicate on the stored detail: the stored detail is already
            # projected, so "would projection remove anything?" always answers
            # no. That mistake made an earlier version of this method a no-op
            # that returned the projection and called it a recovery.
            return stored_detail
        request_id = await self._begin(
            purpose=purpose,
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=None,
            kinds=[RawTraceKind.AUDIT_DETAIL.value],
        )
        key = audit_idempotency_key(org_id, instance_id, audit_entry_id)
        row = await self._fetch_or_completed(
            key,
            request_id=request_id,
            instance_id=instance_id,
            what=f"audit entry {audit_entry_id}",
        )
        if row is None:
            await self._complete(
                request_id=request_id, instance_id=instance_id, outcome="retrieval_failed"
            )
            raise RawTraceUnavailable(f"missing vault audit detail for entry {audit_entry_id}")
        full = await self._opened_or_completed(
            row,
            request_id=request_id,
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=None,
            kind=RawTraceKind.AUDIT_DETAIL.value,
            audit_entry_id=audit_entry_id,
        )
        # The entry's stamp and the vault row's describe the SAME projection
        # event, so a disagreement means the row does not belong to it.
        if row.projector_version != projector_version:
            await self._complete(
                request_id=request_id, instance_id=instance_id, outcome="integrity_failed"
            )
            raise RawTraceUnavailable(
                f"version disagreement for audit entry {audit_entry_id}: entry stamped "
                f"{projector_version!r}, vault row {row.projector_version!r}"
            )
        verdict = verify_audit_projection_agreement(action, full, stored_detail, projector_version)
        if verdict == "mismatch":
            await self._complete(
                request_id=request_id, instance_id=instance_id, outcome="integrity_failed"
            )
            raise RawTraceUnavailable(
                f"projection disagreement for audit entry {audit_entry_id}: the stored detail "
                "does not match a re-projection of the vaulted raw"
            )
        # `unsupported` still returns the raw — the grant holder asked for it
        # and it is intact — but the OUTCOME says so rather than "succeeded".
        await self._complete(
            request_id=request_id,
            instance_id=instance_id,
            outcome="succeeded" if verdict == "ok" else PROJECTION_UNSUPPORTED,
        )
        return full

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
        row = await self._fetch_or_completed(
            key,
            request_id=request_id,
            instance_id=instance_id,
            what=f"trigger of {instance_id}",
        )
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
        # No access record here (the caller owns begin/commit), so there is
        # nothing to complete — but a repository failure must still surface
        # as RawTraceUnavailable, which the response boundary catches,
        # rather than as itself, which it does not (R14 finding 2).
        try:
            row = await self._repos.raw_trace_vault.get_by_idempotency_key(key)
        except Exception as exc:
            raise RawTraceUnavailable(f"vault lookup failed: {exc}") from exc
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
