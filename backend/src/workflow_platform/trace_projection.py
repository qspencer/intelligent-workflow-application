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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_REDACTED_FIELD = "[redacted — raw-trace grant required]"
# Public alias for the write path (the flip stores this in place of raw error).
REDACTED_ERROR = _REDACTED_FIELD
_REDACTED_TRIGGER = "raw trigger payload withheld (raw-trace privilege only)"
_REDACTED_TOOL = "raw tool input/result withheld (admin-tier only)"
# The CLOSED set of markers this module emits. Membership is exact; a value that
# merely looks like one is attacker-supplied and is itself redacted.
_GENERATED_MARKERS = frozenset({_REDACTED_FIELD, _REDACTED_TRIGGER, _REDACTED_TOOL})
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
# The SHAPE contract of a safe projection (§4.1). Bumped when the projected
# structure changes, independently of which fields the registry accepts.
PROJECTION_SCHEMA_VERSION = 1

# An approved opaque identifier: bounded, no whitespace, no prose. This is what
# makes id/hash/model/action fields safe — a token of this shape cannot carry a
# mail body or a paragraph of PII.
# An OPAQUE IDENTIFIER: uuid/hash/slug shaped. Deliberately excludes `@` and
# `+` so it cannot admit an email address — the old single regex allowed
# `alice@example.com` under `model` (F1b). Identity paths that legitimately
# hold an email (`actor_id`, `sub`) declare `_opaque_id` too; that value is the
# OPERATOR's own identity, not third-party content, and is declared per path
# rather than by a blanket rule.
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9._:@|/=-]{1,200}$")
# A SHORT TOKEN: model ids, enum-ish states, hashes. No `@`, no spaces.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:/=-]{1,120}$")
_TS_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.+Z-]{0,20}$")

_Validator = Callable[[Any], bool]


def _opaque_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_OPAQUE_ID_RE.match(value))


def _short_token(value: Any) -> bool:
    """No `@`, so a path declaring a token cannot receive an email."""
    return isinstance(value, str) and bool(_TOKEN_RE.match(value))


def _opaque(value: Any) -> bool:  # back-compat alias for existing callers
    return _opaque_id(value)


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
    # EXACT match only (F2). Prefix-matching `"[redacted"` made the marker an
    # input capability: `{"_redacted": "[redacted SSN123456789]"}` survived.
    return value in _GENERATED_MARKERS


# Platform-global registry: engine-computed, governance, and row-identity fields
# only. NOTE (§1.4 gap named by design review 2026-08-03): per-workflow BUSINESS
# outputs (`category`, `attention`, `relevance_bucket`, `document_type`,
# `complexity`) are deliberately ABSENT — their vocabularies are per-workflow, so
# they cannot be validated here and are vaulted/redacted by default. The
# per-workflow safe-schema DECLARATION that lets a workflow opt them back in
# (with its own enum) is the next slice; until then this over-redacts, which is
# the safe direction.
# --- PATH-SCOPED projection schema (third code review, 2026-08-08 F1) --------
#
# The previous primitive was a process-global, NAME-keyed registry consulted at
# every depth while walking the DATA. That made safety a property of a key
# *appearing somewhere*, so an undeclared container laundered raw whenever a
# descendant happened to spell `model`, `id` or `state`:
#
#     {"unregistered_map": {"model": "SSN123456789"}}   → survived unchanged
#
# Recursion is now driven by the SCHEMA, not the data. A key that is not
# declared at *this position* is redacted WHOLE — never explored. Declared
# containers recurse only into their own declared children. Safety is therefore
# a property of the PATH, which is what §1.4 §1.3 actually require ("exact
# source"), and adding a field elsewhere cannot make it safe here.


@dataclass(frozen=True)
class Leaf:
    """A declared value position. Survives only if `validate` accepts it."""

    validate: _Validator


@dataclass(frozen=True)
class Obj:
    """A declared container. Children not listed here are REDACTED WHOLE.
    `wildcard` covers genuinely dynamic keys (step ids, usage counters) — it is
    a declaration too, not an escape hatch: the wildcard's own node still
    validates."""

    children: dict[str, Node] = field(default_factory=dict)
    wildcard: Node | None = None


@dataclass(frozen=True)
class Seq:
    """A declared list; every element is projected under `item`."""

    item: Node


@dataclass(frozen=True)
class ToolCalls:
    """The tool-call list — structurally projected by `safe_tool_call`."""


@dataclass(frozen=True)
class TriggerPayload:
    """A raw inbound payload — routing ids only, via `safe_trigger_payload`."""


Node = Leaf | Obj | Seq | ToolCalls | TriggerPayload

# Leaf kinds, named so a path declares WHAT it may hold, not merely that it is
# allowed. `_opaque` is deliberately NOT reused for everything: an id-shaped
# validator that accepts `alice@example.com` is not a validator (F1b).
_ID = Leaf(_opaque_id)
_TOKEN = Leaf(_short_token)
_TS_ = Leaf(_ts)
_BOOL = Leaf(_boolean)
_COUNT = Leaf(_count)
_AMOUNT = Leaf(_amount)
_SCORE = Leaf(_score)
_MARKER = Leaf(_marker)
_USAGE = Obj(wildcard=_COUNT)

# A step's projected output. `output_text`, `summary`, `recall`, `error` and any
# undeclared field are absent by construction — they are raw by taint (§1.1).
_STEP_OUTPUT = Obj(
    children={
        "model": _TOKEN,
        "usage": _USAGE,
        "cost_usd": _AMOUNT,
        "iterations": _COUNT,
        "stop_reason": _TOKEN,
        "parse_ok": _BOOL,
        "memory_written": _BOOL,
        "memory_hash": _TOKEN,
        "recall_hash": _TOKEN,
        "num_steps": _COUNT,
        "faithfulness_score": _SCORE,
        "category_score": _SCORE,
        "relevance_score": _SCORE,
        "concern_count": _COUNT,
        "needs_tests": _BOOL,
        "tool_calls": ToolCalls(),
        "projector_version": _TOKEN,
        "projection_schema_version": _COUNT,
        "_redacted": _MARKER,
    }
)

# Engine/governance metadata on an audit `detail`. Content-free by design.
_AUDIT_DETAIL = Obj(
    children={
        "request_id": _ID,
        "surface": Leaf(
            _enum("detail", "explain", "audit", "ws", "memory", "escalation", "dry_run")
        ),
        "outcome": _TOKEN,
        "purpose": _TOKEN,
        "reason_code": _TOKEN,
        "scope": _TOKEN,
        "workload_identity": _TOKEN,
        "kinds": Seq(_TOKEN),
        "intended_kinds": Seq(_TOKEN),
        "released_kinds": Seq(_TOKEN),
        "withheld_kinds": Seq(_TOKEN),
        "principal_id": _ID,
        "grant_id": _ID,
        "approved_by": _ID,
        "requested_by": _ID,
        "revoked_by": _ID,
        "approval_mode": _TOKEN,
        "raw_included": _BOOL,
        "redaction_reason": _TOKEN,
        "org_bypass": _BOOL,
        "org_scoped": _BOOL,
        "state": _TOKEN,
        "era": _COUNT,
        "attempt": _COUNT,
        "type": _TOKEN,
        "content_hash": _TOKEN,
        "namespace": _ID,
        "mode": Leaf(_enum("summary", "categories")),
        "budget_action": _TOKEN,
        "unrecognized_ids": _COUNT,
        "org_id": _ID,
        "trigger": TriggerPayload(),
        "trigger_payload": TriggerPayload(),
        "tool_calls": ToolCalls(),
        "_redacted": _MARKER,
    }
)

# --- SCHEMAS BY ASSET KIND -------------------------------------------------
#
# Projection is keyed by (asset kind, path). The path half alone was not
# enough: one shared root served step outputs, instance dumps AND audit details,
# so a step output was projected against a schema that never declared `model` or
# `cost_usd` and lost them — after which the §4.3 equality check disagreed with
# itself. The CALLER knows what it holds; it must say so.

_CONTEXT = Obj(
    children={
        "trigger": TriggerPayload(),
        "steps": Obj(wildcard=_STEP_OUTPUT),
        "total_tokens": _COUNT,
        "total_cost_usd": _AMOUNT,
        "dry_run": _BOOL,
        "_redacted": _MARKER,
    }
)

_INSTANCE = Obj(
    children={
        "id": _ID,
        "workflow_id": _ID,
        "org_id": _ID,
        "owner_user_id": _ID,
        "state": _TOKEN,
        "error": _MARKER,
        "created_at": _TS_,
        "started_at": _TS_,
        "completed_at": _TS_,
        "trigger_payload": TriggerPayload(),
        "context": _CONTEXT,
        "total_tokens": _COUNT,
        "total_cost_usd": _AMOUNT,
        "projector_version": _TOKEN,
        "projection_schema_version": _COUNT,
        "_redacted": _MARKER,
    }
)

_STEP_ROW = Obj(
    children={
        "id": _ID,
        "instance_id": _ID,
        "step_id": _ID,
        "attempt": _COUNT,
        "state": _TOKEN,
        "error": _MARKER,
        "output": _STEP_OUTPUT,
        "started_at": _TS_,
        "completed_at": _TS_,
        "projector_version": _TOKEN,
        "projection_schema_version": _COUNT,
        "_redacted": _MARKER,
    }
)

#: The asset kinds a caller may declare. There is deliberately no default that
#: silently guesses — see `redact_tool_data`.
SCHEMAS: dict[str, Node] = {
    "instance": _INSTANCE,
    "step_row": _STEP_ROW,
    "step_output": _STEP_OUTPUT,
    "context": _CONTEXT,
    "audit_detail": _AUDIT_DETAIL,
}


def _project(node: Node | None, value: Any) -> Any:
    """Project `value` at a declared position. `node is None` = UNDECLARED at
    this path ⇒ the whole value is redacted, never recursed."""
    if node is None:
        return _REDACTED_FIELD
    if isinstance(node, Leaf):
        return value if node.validate(value) else _REDACTED_FIELD
    if isinstance(node, TriggerPayload):
        return safe_trigger_payload(value) if isinstance(value, dict) else _REDACTED_FIELD
    if isinstance(node, ToolCalls):
        if not isinstance(value, list):
            return _REDACTED_FIELD
        return [safe_tool_call(c) if isinstance(c, dict) else _REDACTED_FIELD for c in value]
    if isinstance(node, Seq):
        if not isinstance(value, list):
            return _REDACTED_FIELD
        return [_project(node.item, v) for v in value]
    if isinstance(node, Obj):
        if not isinstance(value, dict):
            return _REDACTED_FIELD
        out: dict[str, Any] = {}
        for k, v in value.items():
            child = node.children.get(k, node.wildcard)
            out[k] = _project(child, v)
        return out
    return _REDACTED_FIELD


def safe_trigger_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """A trigger payload (raw inbound mail / webhook body / file event) →
    routing fields only (TRACE_GOVERNANCE_PLAN §1, F4). `redact_tool_data`
    keeps message_id/thread_id/id so IDs stay visible; strips subject / body /
    headers / webhook content AND the sender address (external code review
    2026-08-02: `from.address` is grant-gated, not safe operational metadata)."""
    safe: dict[str, Any] = {"_redacted": _REDACTED_TRIGGER}
    for key in _TRIGGER_ROUTING_KEYS:
        if key in payload:
            # F1c: these were copied verbatim, making `id` a free-text channel.
            value = payload[key]
            safe[key] = value if _opaque_id(value) else _REDACTED_FIELD
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
    if "input_key_count" in tc and "input" not in tc and "result" not in tc:
        return tc
    result = tc.get("result") or {}
    content = result.get("content")
    safe: dict[str, Any] = {
        "name": tc.get("name"),
        # F1d: `input_keys` exported raw parameter NAMES, which a model chooses
        # while reading hostile content — so a key like "customer SSN 123-45-6789"
        # became persisted content. Only the arity survives.
        "input_key_count": len(tc.get("input") or {}),
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
        return obj in _GENERATED_MARKERS
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


def redact_tool_data(obj: Any, admin: bool, *, kind: str = "audit_detail") -> Any:
    """The below-grant projection (admin=True → unchanged). DEFAULT-DENY
    (external code review 2026-08-02 F1, tightened by the 08-03 re-review): a
    value survives ONLY because its field is registered in `_SAFE_FIELDS` AND
    the value passes that field's VALIDATOR — never because it is a scalar and
    never because no redaction branch recognized it. (The earlier
    safe-by-TYPE / key-allowlist wording described behaviour that leaked
    `{"category": SECRET}` and `{"ssn": 123456789}`; it is gone.) So free-form
    model output
    (`output_text`, `summary`, `reasoning`, …), recalled correspondent history
    (`recall`), error text, and any unknown field are redacted, whether or not
    the step used a tool. Recurses into nested dicts (context / step outputs /
    audit details); tool-call lists and trigger payloads keep their existing
    structural projections. IDEMPOTENT on already-safe data (a fixed point —
    the verifier and backfill rely on it)."""
    if admin or not isinstance(obj, dict):
        return obj
    schema = SCHEMAS.get(kind)
    if schema is None:
        raise ValueError(
            f"unknown projection asset kind {kind!r}; declare one of {sorted(SCHEMAS)}"
        )
    return _project(schema, obj)
