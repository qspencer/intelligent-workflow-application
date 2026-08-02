"""The single trace projection (docs/TRACE_GOVERNANCE_PLAN.md §1). Produces
the SAFE form of a raw payload — used at READ time as a role-aware backstop
(re-exported by `api/redaction.py`) AND at WRITE time by the engine's
safe-only flip (TG3b), so it lives in the domain layer, not under `api`.

DEFAULT-DENY (external code review 2026-08-02 F1): a value survives a
below-grant read only if it is a safe-by-type scalar or its key is in
`_SAFE_KEYS` (engine/governance metadata or a validated enum). Free-form model
output, recalled history, error text, and any unknown field are redacted —
their raw lives in the vault. Tool-call lists and raw trigger payloads keep
their structural projections (`safe_tool_call` / `safe_trigger_payload`).
"""

from __future__ import annotations

import json
from typing import Any

_REDACTED_FIELD = "[redacted — raw-trace grant required]"
# Public alias for the write path (the flip stores this in place of raw error).
REDACTED_ERROR = _REDACTED_FIELD
_REDACTED_TRIGGER = "raw trigger payload withheld (raw-trace privilege only)"
# Routing fields kept in a redacted trigger payload: IDs, never content
# (subject/body/headers/arbitrary webhook fields — AND the sender address,
# external code review 2026-08-02 — are stripped).
_TRIGGER_ROUTING_KEYS = ("message_id", "thread_id", "id")

# DEFAULT-DENY allowlist (external code review 2026-08-02 F1). A value survives
# a below-grant read ONLY because its key is here (engine-computed metadata,
# structural/row identity, engine-authored governance fields, or a VALIDATED
# closed-enum/numeric business field) — never because no redaction branch
# recognized it. Everything else (free-form model strings, unknown keys,
# arbitrary lists) is redacted; its raw lives in the vault. Numbers / bools /
# null are safe by TYPE regardless of key. Erring toward OMITTING a key
# over-redacts (safe); including a raw-bearing key would leak, so keep this to
# fields that cannot carry model- or third-party-authored content.
_SAFE_KEYS = frozenset(
    {
        # structural / row identity
        "id",
        "instance_id",
        "step_id",
        "step_attempt_id",
        "workflow_id",
        "workflow_instance_id",
        "org_id",
        "owner_user_id",
        "state",
        "attempt",
        "created_at",
        "started_at",
        "completed_at",
        "updated_at",
        "last_seen_at",
        "timestamp",
        "expires_at",
        "revoked_at",
        # audit envelope / actor identity
        "actor_type",
        "actor_id",
        "action",
        "iss",
        "sub",
        # engine-computed run metadata (never model-authored)
        "model",
        "usage",
        "memory_hash",
        "recall_hash",
        "parse_ok",
        "memory_written",
        "stop_reason",
        "num_steps",
        # engine-authored governance metadata (§2/§3/§5)
        "surface",
        "outcome",
        "purpose",
        "reason_code",
        "scope",
        "request_id",
        "workload_identity",
        "kinds",
        "intended_kinds",
        "released_kinds",
        "withheld_kinds",
        "principal_id",
        "grant_id",
        "approved_by",
        "requested_by",
        "revoked_by",
        "approval_mode",
        "raw_included",
        "redaction_reason",
        "org_bypass",
        "era",
        "schema_version",
        "budget_action",
        "content_hash",
        # projector's own metadata
        "_redacted",
        "content_bytes",
        "input_keys",
        "result_ok",
        "error_present",
        "name",
        "pinned",
        "pin_overrides",
        # VALIDATED closed-enum / numeric business classifications
        "category",
        "attention",
        "complexity",
        "needs_tests",
        "document_type",
        "relevance_bucket",
    }
)


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
    input/result/error text. IDEMPOTENT: an already-projected record (carries
    `_redacted`) is returned unchanged, so `redact_tool_data` is a fixed point
    on safe data (the zero-raw verifier and backfill rely on this)."""
    if "_redacted" in tc:
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
        "_redacted": "raw tool input/result withheld (admin-tier only)",
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
    if key in _SAFE_KEYS:
        # Allowlisted: values under these keys are safe by construction
        # (engine/governance metadata or a validated enum).
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value  # safe by type
    if isinstance(value, dict):
        return _redact_dict(value)  # recurse structure; leaves are default-denied
    # DEFAULT-DENY: free-form string, list, or any non-allowlisted value.
    return _REDACTED_FIELD
