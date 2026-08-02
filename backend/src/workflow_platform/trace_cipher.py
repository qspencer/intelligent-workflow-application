"""Per-org envelope encryption for the raw-trace vault
(docs/TRACE_GOVERNANCE_PLAN.md §0.1 Contract B1 / TG3d-1).

Contract B1 = **database-operator resistance**: a DB dump / backup of
`raw_traces` contains ciphertext only; the keys live OUTSIDE the DB role
(here, derived from a master key held in the environment / a KMS). Each org
gets its own data key (HKDF from the master), so cross-tenant re-encryption
is never needed and per-tenant key rotation is possible. Payloads are sealed
with AES-256-GCM, whose associated data BINDS the ciphertext to
`(org, instance, step_attempt, kind, schema_version)` — so a vault-row edit
or a cross-row substitution fails to open (§4.3 integrity).

Activation is opt-in: `build_trace_cipher()` returns a cipher only when
`WORKFLOW_PLATFORM_TRACE_MASTER_KEY` is set, else None (the Contract-A
plaintext vault). B2 (host-operator resistance) is a separate infrastructure
design (attested runtime / external key release) — NOT this module.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_ALG = "AESGCM-HKDF-1"
_HKDF_SALT = b"workflow-platform/trace-vault/v1"
ENV_MASTER_KEY = "WORKFLOW_PLATFORM_TRACE_MASTER_KEY"


class TraceCipherError(Exception):
    """Sealing/opening failed (bad key, tampered ciphertext, or AAD mismatch)."""


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _aad(
    *, org_id: str, instance_id: str, step_attempt_id: str | None, kind: str, schema_version: int
) -> bytes:
    """Canonical, immutable associated data binding the ciphertext to its
    identity — a substitution across rows changes the AAD and fails to open."""
    return json.dumps(
        {
            "org": org_id,
            "instance": instance_id,
            "attempt": step_attempt_id,
            "kind": kind,
            "schema": schema_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class TraceCipher:
    def __init__(self, master_key: bytes) -> None:
        if len(master_key) < 32:
            raise TraceCipherError("trace master key must be at least 32 bytes")
        self._master = master_key

    def _data_key(self, org_id: str) -> bytes:
        """Per-org 256-bit data key, derived from the master (HKDF). The org
        key is never persisted — derived on demand from the env/KMS master."""
        return HKDF(
            algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=org_id.encode("utf-8")
        ).derive(self._master)

    def seal(
        self,
        payload: Any,
        *,
        org_id: str,
        instance_id: str,
        step_attempt_id: str | None,
        kind: str,
        schema_version: int,
    ) -> dict[str, Any]:
        nonce = os.urandom(12)
        aad = _aad(
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=step_attempt_id,
            kind=kind,
            schema_version=schema_version,
        )
        ct = AESGCM(self._data_key(org_id)).encrypt(
            nonce, json.dumps(payload, default=str).encode("utf-8"), aad
        )
        return {"alg": _ALG, "key_id": org_id, "nonce": _b64e(nonce), "ct": _b64e(ct)}

    def open(
        self,
        sealed: Any,
        *,
        org_id: str,
        instance_id: str,
        step_attempt_id: str | None,
        kind: str,
        schema_version: int,
    ) -> Any:
        if not isinstance(sealed, dict) or sealed.get("alg") != _ALG:
            raise TraceCipherError("not a sealed trace payload")
        aad = _aad(
            org_id=org_id,
            instance_id=instance_id,
            step_attempt_id=step_attempt_id,
            kind=kind,
            schema_version=schema_version,
        )
        try:
            pt = AESGCM(self._data_key(org_id)).decrypt(
                _b64d(sealed["nonce"]), _b64d(sealed["ct"]), aad
            )
        except Exception as exc:  # InvalidTag on tamper / AAD mismatch / wrong key
            raise TraceCipherError("failed to open sealed trace payload") from exc
        return json.loads(pt)

    @staticmethod
    def is_sealed(payload: Any) -> bool:
        return isinstance(payload, dict) and payload.get("alg") == _ALG


def build_trace_cipher() -> TraceCipher | None:
    """A cipher iff `WORKFLOW_PLATFORM_TRACE_MASTER_KEY` (base64) is set, else
    None (the Contract-A plaintext vault). Reference key custody = the
    environment; a production deployment swaps in a KMS-backed master."""
    raw = os.environ.get(ENV_MASTER_KEY)
    if not raw:
        return None
    try:
        return TraceCipher(_b64d(raw.strip()))
    except Exception as exc:
        raise TraceCipherError(f"invalid {ENV_MASTER_KEY}") from exc
