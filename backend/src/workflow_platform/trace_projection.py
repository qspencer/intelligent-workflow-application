"""The single trace projection (docs/TRACE_GOVERNANCE_PLAN.md §1). Produces
the SAFE form of a raw payload — used at READ time as a role-aware backstop
(re-exported by `api/redaction.py`) AND at WRITE time by the engine's
safe-only flip (TG3b), so it lives in the domain layer, not under `api`.

DEFAULT-DENY via a REGISTERED, VALIDATED field projection (§1.4 CONTRACT 1): a
value survives a below-grant read only if its field is in `_SAFE_FIELDS` AND the
value passes that field's validator (approved opaque id / closed enum / bounded
number / bool / structurally-checked container). Free-form model output, recalled
history, error text, unknown fields, and registered fields carrying the wrong
shape are all redacted — their raw lives in the vault. Tool-call lists and raw
trigger payloads keep their structural projections (`safe_tool_call` /
`safe_trigger_payload`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

_REDACTED_FIELD = "[redacted — raw-trace grant required]"
# Public alias for the write path (the flip stores this in place of raw error).
REDACTED_ERROR = _REDACTED_FIELD
_REDACTED_TRIGGER = "raw trigger payload withheld (raw-trace privilege only)"
_REDACTED_TOOL = "raw tool input/result withheld (admin-tier only)"
# Routing fields kept in a redacted trigger payload: IDs, never content
# (subject/body/headers/arbitrary webhook fields — AND the sender address,
# external code review 2026-08-02 — are stripped).
_TRIGGER_ROUTING_KEYS = ("message_id", "thread_id", "id")

# --- Registered field projections (TRACE_GOVERNANCE_PLAN §1.4 CONTRACT 1) ---
#
# "A field is safe-to-persist only via a REGISTERED, VERSIONED projection — not
# merely a validated type." The prior key-allowlist violated this: it passed any
# value whose KEY was listed, and any number/bool by TYPE — so `{"category":
# SECRET}`, `{"usage": [SECRET]}` and `{"ssn": 123456789}` all survived
# (external code RE-review 2026-08-03). Now every surviving value must pass its
# field's VALIDATOR: an approved opaque identifier, a closed enum, a bounded
# number, a bool, or a structurally-checked container. Anything unregistered,
# mistyped, or out of bounds is redacted and its raw lives in the vault.
#
# Deliberately NOT registered: `output_text`, `summary`, `reasoning`, `recall`,
# `error` and every other free-form field (raw by taint, §1.1).

PROJECTOR_VERSION = "2"  # bumped: key-allowlist → validated field registry

# An approved opaque identifier: bounded, no whitespace, no prose. This is what
# makes id/hash/model/action fields safe — a token of this shape cannot carry a
# mail body or a paragraph of PII.
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9._:@+/=-]{1,200}$")
_TS_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.+Z-]{0,20}$")

_Validator = Callable[[Any], bool]


def _opaque(value: Any) -> bool:
    return isinstance(value, str) and bool(_OPAQUE_RE.match(value))


def _boolean(value: Any) -> bool:
    return isinstance(value, bool)


def _count(value: Any) -> bool:
    """A bounded non-negative integer (bools excluded — they are their own rule)."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**12


def _amount(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= float(value) <= 10**9
    )


def _score(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and -1 <= value <= 100


def _ts(value: Any) -> bool:
    if isinstance(value, datetime):
        return True
    return isinstance(value, str) and len(value) <= 40 and bool(_TS_RE.match(value))


def _enum(*allowed: str) -> _Validator:
    values = frozenset(allowed)
    return lambda v: isinstance(v, str) and v in values


def _opaque_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) <= 256 and all(_opaque(v) for v in value)


def _usage(value: Any) -> bool:
    """The engine's token-usage struct: short opaque keys → bounded counts."""
    return (
        isinstance(value, dict)
        and len(value) <= 16
        and all(_opaque(k) and _count(v) for k, v in value.items())
    )


def _marker(value: Any) -> bool:
    """OUR redaction marker — never an input capability. A forged `_redacted`
    carrying attacker text fails this and is itself redacted."""
    return isinstance(value, str) and (
        value.startswith("[redacted") or value in (_REDACTED_TRIGGER, _REDACTED_TOOL)
    )


# Platform-global registry: engine-computed, governance, and row-identity fields
# only. NOTE (§1.4 gap named by design review 2026-08-03): per-workflow BUSINESS
# outputs (`category`, `attention`, `relevance_bucket`, `document_type`,
# `complexity`) are deliberately ABSENT — their vocabularies are per-workflow, so
# they cannot be validated here and are vaulted/redacted by default. The
# per-workflow safe-schema DECLARATION that lets a workflow opt them back in
# (with its own enum) is the next slice; until then this over-redacts, which is
# the safe direction.
_SAFE_FIELDS: dict[str, _Validator] = {
    # structural / row identity
    "id": _opaque,
    "instance_id": _opaque,
    "step_id": _opaque,
    "step_attempt_id": _opaque,
    "workflow_id": _opaque,
    "workflow_instance_id": _opaque,
    "org_id": _opaque,
    "owner_user_id": _opaque,
    "principal_id": _opaque,
    "grant_id": _opaque,
    "namespace": _opaque,
    "state": _opaque,
    "attempt": _count,
    "created_at": _ts,
    "started_at": _ts,
    "completed_at": _ts,
    "updated_at": _ts,
    "last_seen_at": _ts,
    "timestamp": _ts,
    "expires_at": _ts,
    "revoked_at": _ts,
    "approved_at": _ts,
    "requested_at": _ts,
    # audit envelope / actor identity
    "actor_type": _enum("human", "engine", "system", "connector"),
    "actor_id": _opaque,
    "action": _opaque,
    "iss": _opaque,
    "sub": _opaque,
    # engine-computed run metadata (never model-authored)
    "model": _opaque,
    "usage": _usage,
    "memory_hash": _opaque,
    "recall_hash": _opaque,
    "parse_ok": _boolean,
    "memory_written": _boolean,
    "stop_reason": _opaque,
    "num_steps": _count,
    "iterations": _count,
    "input_tokens": _count,
    "output_tokens": _count,
    "total_tokens": _count,
    "cost_usd": _amount,
    "total_cost_usd": _amount,
    "duration_seconds": _amount,
    "dry_run": _boolean,
    "truncated": _boolean,
    "type": _opaque,
    # engine-authored governance metadata (§2/§3/§5)
    "surface": _enum("detail", "explain", "audit", "ws", "memory"),
    "outcome": _opaque,
    "purpose": _opaque,
    "reason_code": _opaque,
    "scope": _opaque,
    "mode": _enum("summary", "categories"),
    "request_id": _opaque,
    "workload_identity": _opaque,
    "kinds": _opaque_list,
    "intended_kinds": _opaque_list,
    "released_kinds": _opaque_list,
    "withheld_kinds": _opaque_list,
    "approved_by": _opaque,
    "requested_by": _opaque,
    "revoked_by": _opaque,
    "approval_mode": _opaque,
    "raw_included": _boolean,
    "redaction_reason": _opaque,
    "org_bypass": _boolean,
    "org_scoped": _boolean,
    "era": _count,
    "schema_version": _opaque,
    "projector_version": _opaque,
    "raw_schema_version": _opaque,
    "budget_action": _opaque,
    "content_hash": _opaque,
    "unrecognized_ids": _count,
    # projector's own metadata
    "_redacted": _marker,
    "content_bytes": _count,
    "input_keys": _opaque_list,
    "result_ok": _boolean,
    "error_present": _boolean,
    "name": _opaque,
    "pinned": _opaque_list,
    "pin_overrides": _opaque_list,
    # bounded numeric evaluation/business scores (numbers, not prose)
    "faithfulness_score": _score,
    "category_score": _score,
    "relevance_score": _score,
    "concern_count": _count,
    "needs_tests": _boolean,
}


def safe_trigger_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """A trigger payload (raw inbound mail / webhook body / file event) →
    routing fields only (TRACE_GOVERNANCE_PLAN §1, F4). `redact_tool_data`
    keeps message_id/thread_id/id so IDs stay visible; strips subject / body /
    headers / webhook content AND the sender address (external code review
    2026-08-02: `from.address` is grant-gated, not safe operational metadata)."""
    safe: dict[str, Any] = {"_redacted": _REDACTED_TRIGGER}
    for key in _TRIGGER_ROUTING_KEYS:
        if key in payload:
            safe[key] = payload[key]
    return safe


def safe_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    """One tool-call record → non-sensitive metadata: parameter KEYS (not
    values), result status, and a content hash+size — never raw
    input/result/error text. IDEMPOTENT: an already-projected record (in SAFE
    SHAPE — `input_keys` present, no raw `input`/`result`) is returned
    unchanged, so `redact_tool_data` is a fixed point on safe data. A `_redacted`
    marker is NEVER trusted as an input capability (re-review 2026-08-03 F1): a
    record still carrying raw `input`/`result` is projected regardless of any
    marker a caller forged onto it."""
    if "input_keys" in tc and "input" not in tc and "result" not in tc:
        return tc
    result = tc.get("result") or {}
    content = result.get("content")
    safe: dict[str, Any] = {
        "name": tc.get("name"),
        "input_keys": sorted((tc.get("input") or {}).keys()),
        "result_ok": not result.get("error"),
        "error_present": bool(result.get("error")),
        "pinned": tc.get("pinned", []),
        "pin_overrides": tc.get("pin_overrides", []),
        "_redacted": _REDACTED_TOOL,
    }
    if content is not None:
        # Byte length only — the truncated hash was dropped (external review
        # 2026-08-01 nonblocking note: a hash is an equality/dictionary
        # oracle for low-entropy results; there's no operational use for it
        # in ordinary-reader responses).
        safe["content_bytes"] = len(json.dumps(content, sort_keys=True, default=str).encode())
    return safe


def has_redaction_marker(obj: Any) -> bool:
    """Whether a (possibly already grant-merged) structure still carries ANY
    default-deny redaction marker — a `[redacted …` string or a dict with
    `_redacted` (external code review 2026-08-02 F8: lets a read surface tell
    whether a raw retrieval was COMPLETE before it commits the release audit)."""
    if isinstance(obj, str):
        return obj.startswith("[redacted") or obj == _REDACTED_TRIGGER
    if isinstance(obj, dict):
        if "_redacted" in obj:
            return True
        return any(has_redaction_marker(v) for v in obj.values())
    if isinstance(obj, list):
        return any(has_redaction_marker(v) for v in obj)
    return False


def redact_error(error: str | None, admin: bool) -> str | None:
    """Error text is RAW (external code review 2026-08-02 F2 — it can echo tool
    output or mail content). A grant-holder (admin=True) reads it unchanged;
    below the grant it becomes the redaction marker (None stays None). Single
    source of the marker for the read surfaces that carry a bare error string
    (instance list, step explain)."""
    if admin or error is None:
        return error
    return _REDACTED_FIELD


def redact_tool_data(obj: Any, admin: bool) -> Any:
    """The below-grant projection (admin=True → unchanged). DEFAULT-DENY
    (external code review 2026-08-02 F1): a value survives ONLY because it is
    a safe-by-TYPE scalar (number/bool/null) or its key is in `_SAFE_KEYS` —
    NOT because no redaction branch recognized it. So free-form model output
    (`output_text`, `summary`, `reasoning`, …), recalled correspondent history
    (`recall`), error text, and any unknown field are redacted, whether or not
    the step used a tool. Recurses into nested dicts (context / step outputs /
    audit details); tool-call lists and trigger payloads keep their existing
    structural projections. IDEMPOTENT on already-safe data (a fixed point —
    the verifier and backfill rely on it)."""
    if admin or not isinstance(obj, dict):
        return obj
    return _redact_dict(obj)


def _redact_dict(obj: dict[str, Any]) -> dict[str, Any]:
    # A single tool-call-shaped detail (a `tool_call` audit entry).
    if {"input", "result", "name"} <= obj.keys() and "tool_calls" not in obj:
        return safe_tool_call(obj)
    return {k: _redact_value(k, v) for k, v in obj.items()}


def _redact_value(key: str, value: Any) -> Any:
    if key == "tool_calls" and isinstance(value, list):
        return [safe_tool_call(c) if isinstance(c, dict) else _REDACTED_FIELD for c in value]
    if key in ("trigger_payload", "trigger") and isinstance(value, dict):
        # Raw inbound message (instance `trigger_payload`, `context.trigger`,
        # the `workflow_started` audit detail) → routing IDs only.
        return safe_trigger_payload(value)
    rule = _SAFE_FIELDS.get(key)
    if rule is not None and rule(value):
        # Registered AND the value passed its validator (§1.4 CONTRACT 1).
        return value
    if value is None:
        return None  # absence carries no content
    if isinstance(value, dict):
        return _redact_dict(value)  # recurse structure; leaves are default-denied
    # DEFAULT-DENY: unregistered field, or a registered field whose value failed
    # validation (wrong type, out of bounds, prose where an id was expected).
    return _REDACTED_FIELD
