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
#: Set when an object dropped one or more entries because their KEYS were
#: undeclared (GR4-R4/R5). A BOOLEAN, deliberately:
#:
#:  - a COUNT was the round-5 defect — `{"_withheld_key_count": 123456789}`
#:    supplied on RAW input was retained verbatim and `output_has_raw()` called
#:    it clean, so the reserved field became a raw channel. A boolean cannot
#:    carry a value, so a forged one is worth exactly what a generated one is.
#:  - it discloses less: "something was withheld here" rather than how much
#:    structure there was.
_WITHHELD = "_withheld_keys"

#: The round-5 representation of the same signal, kept RECOGNISED (never
#: written). A stored round-5 projection carries `_withheld_key_count: n`, and
#: a build that does not know it reports an incomplete object COMPLETE and
#: skips restoring it (R6 F2). Reading historical shapes is not the same as
#: emitting them.
_WITHHELD_LEGACY = "_withheld_key_count"


def is_withheld_marker(obj: Any) -> bool:
    """Whether this object carries the withheld-entries signal, in ANY
    representation this build supports (current boolean, round-5 count).

    ONE predicate, because R6 found completeness (`has_redaction_marker`) and
    compatibility (`trace_rehydrate._output_projected`) detecting withholding
    differently — so the same object was 'complete' to one and 'projected' to
    the other."""
    if not isinstance(obj, dict):
        return False
    if obj.get(_WITHHELD) is True:
        return True
    legacy = obj.get(_WITHHELD_LEGACY)
    return isinstance(legacy, int) and not isinstance(legacy, bool) and legacy > 0


def _resolved_tool_name(name: Any, known_tools: frozenset[str] | None) -> Any:
    """A tool name may be shown only when it RESOLVES against a catalog the
    caller supplies; otherwise it is the model's own string and is withheld.

    R5 F2 — there is deliberately no process-wide catalog any more. Round 5
    shipped a module-global set kept in step by `ToolCatalog.register`, and the
    reviewer showed why that cannot work for a projection contract: the same
    stored record reconstructed differently before and after the catalog
    changed (success -> integrity_failed) with neither record touched, and
    `register` REPLACED the global rather than widening it, so a second catalog
    in one process silently erased the first. Projection must be a pure
    function of the record, so the catalog is now a per-call argument only —
    and with no argument the name is withheld. Restoring resolved names needs
    an immutable, VERSIONED catalog recorded alongside the projection, which is
    a design item, not a patch.

    R5 F6 — total over hostile input: a list/dict `name` is unhashable and
    membership raised TypeError.
    """
    if not isinstance(name, str) or known_tools is None:
        return _REDACTED_FIELD
    return name if name in known_tools else _REDACTED_FIELD


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

PROJECTOR_VERSION = "3"  # R5 F1: bumped — the R4/R5 containment CHANGED what
# projection emits (an undeclared key is dropped + flagged, not emitted with a
# redacted value; routing ids withheld; tool names resolved). Round 5 shipped
# those changes still stamped "2", so a row written by the OLD projector was
# re-projected under the NEW rules, disagreed, and read as INTEGRITY FAILURE
# instead of degrading. §4.3 already handles a bump correctly ("unsupported",
# criterion 17: a bump must not make pre-change rows read corrupt) — it was
# never given the chance. Any change to what projection EMITS bumps this.
# The SHAPE contract of a safe projection (§4.1). Bumped when the projected
# structure changes, independently of which fields the registry accepts.
PROJECTION_SCHEMA_VERSION = 2  # R6 F3: bumped — the projected STRUCTURE
# changed (an undeclared key is dropped and flagged rather than emitted with a
# redacted value; `pinned`/`pin_overrides` name lists became booleans). Its own
# rule says it moves when the shape moves; it had not. THIS is the one
# authoritative definition — `persistence.models` re-exports it rather than
# declaring its own, which is how the two drifted apart.

# An approved opaque identifier: bounded, no whitespace, no prose. This is what
# makes id/hash/model/action fields safe — a token of this shape cannot carry a
# mail body or a paragraph of PII.
# An OPAQUE IDENTIFIER for OPERATOR-IDENTITY paths only (`actor_id`, `sub`),
# which legitimately hold an operator email — so this DOES include `@` (the
# earlier comment claimed otherwise; the regex is authoritative, GR4-r2 F2). It
# is NOT for third-party/externally-supplied values: routing ids and model/state
# fields use `_short_token`, which excludes `@` so it cannot admit an email.
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


def _safe_key(key: Any) -> bool:
    """A dict KEY safe to emit: a field-NAME-shaped token (no `@`, no spaces).
    A raw key — email, prose, any attacker-controlled string — fails, so it is
    dropped rather than emitted as a leak channel (GR4-r2 F1)."""
    return _short_token(key)


def _token_list(value: Any) -> list[str]:
    """A list of tokens — only the token elements survive. NOT total over a bare
    string (GR4-r2 F1: `for x in "SECRET"` iterates CHARACTERS): a non-list is
    an empty list, never decomposed."""
    if not isinstance(value, list):
        return []
    return [x for x in value if _short_token(x)]


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


def is_generated_marker(value: Any) -> bool:
    """Exactly one of the markers THIS module emits. The SINGLE source for every
    marker check (projector `_marker`, `has_redaction_marker`, the migration's
    `_error_has_raw`) so no caller re-implements it wrongly.

    - TOTAL: a non-string is never a marker (guards the set membership, so a
      hostile `{"_redacted": []}` cannot raise `unhashable type` — GR4-r2 F6);
    - EXACT: a forged `"[redacted <raw>]"` is NOT a marker (never a prefix —
      the marker is an output representation, never an input capability, F2/F4)."""
    return isinstance(value, str) and value in _GENERATED_MARKERS


def _marker(value: Any) -> bool:
    """OUR redaction marker — never an input capability. A forged `_redacted`
    carrying attacker text (or a non-string) fails this and is itself redacted."""
    return is_generated_marker(value)


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
    `wildcard` covers genuinely dynamic keys — it is a declaration too, not an
    escape hatch: the wildcard's own node still validates.

    `wildcard_keys` declares where the dynamic KEYS come from, because a key is
    content and shape cannot vet it (GR4-R4). A token-SHAPED secret
    (`AKIAIOSFODNN7EXAMPLE`, an unformatted SSN) passes every field-name test
    there is, so the only sound question is the key's SOURCE:

      - `"platform"` — the keys are generated by us (step ids, from the
        workflow definition). They survive.
      - `None` (the default, fail-closed) — the keys are DATA. The entry is
        dropped whole: a container whose keys carry outside content may not
        emit them, whatever they look like.

    A container that needs specific data-keyed entries declares them as
    `children` (see `_USAGE`), which is a closed set rather than a shape test."""

    children: dict[str, Node] = field(default_factory=dict)
    wildcard: Node | None = None
    wildcard_keys: str | None = None


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
# GR4-R4: was `Obj(wildcard=_COUNT)`, which admitted ANY token-shaped key —
# `{"usage": {"AKIAIOSFODNN7EXAMPLE": 1}}` rode through as a KEY. The counter
# names are a closed, platform-produced set, so declare them; anything else is
# dropped rather than shape-tested.
_USAGE = Obj(
    children={
        "input_tokens": _COUNT,
        "output_tokens": _COUNT,
        "total_tokens": _COUNT,
        "cache_read_input_tokens": _COUNT,
        "cache_write_input_tokens": _COUNT,
        # R5 F5: AgentUsage really produces these two; omitting them dropped
        # live engine counters (explain's `usage.get("iterations")` went None).
        "iterations": _COUNT,
        "tool_calls": _COUNT,
    }
)

# Capability entries are workflow-DECLARED config (glob paths, host patterns,
# tool names from the definition YAML) — provably platform/user-authored, never
# third-party content — so a no-whitespace bound is the right validator: it
# admits `*.example.com` and `/inbox/*` that a token path would reject, while
# still refusing prose. Scoped to capability paths only.
_CONFIG = Leaf(
    lambda v: isinstance(v, str) and bool(v) and len(v) <= 200 and not any(c.isspace() for c in v)
)
_CAP_POLICY = Obj(
    children={
        "name": _TOKEN,
        "tools": Seq(_CONFIG),
        "file_read": Seq(_CONFIG),
        "file_write": Seq(_CONFIG),
        "allowed_hosts": Seq(_CONFIG),
        "max_tokens_per_call": _COUNT,
    }
)
_CAPABILITIES = Obj(children={"layers": Seq(_CAP_POLICY)})

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
        # A `step_completed` entry carries the step output verbatim
        # (`detail={"output": execution.output, ...}`), and some entries carry a
        # context snapshot. Both are declared with THEIR OWN schema rather than
        # inherited — that nesting is exactly what the path-scoped model exists
        # to express, and the flat name registry could never have named it.
        "output": _STEP_OUTPUT,
        "steps": Obj(wildcard=_STEP_OUTPUT, wildcard_keys="platform"),
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
        # engine-computed identifiers of the run — safe, and needed so they
        # survive at-rest projection instead of becoming unrecoverable markers
        "instance_id": _ID,
        "workflow_id": _ID,
        "org_id": _ID,
        "trigger": TriggerPayload(),
        "steps": Obj(wildcard=_STEP_OUTPUT, wildcard_keys="platform"),
        "capabilities": _CAPABILITIES,
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
    if value is None:
        # `null` carries no content, at any path, declared or not. Treating it
        # as raw produced spurious markers (a `null` capability list became
        # `[redacted …]`), which then failed the grant-holder completeness check.
        return None
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
        withheld = False
        for k, v in value.items():
            # A dict KEY is content too (GR4-r2 F1): the old code emitted every
            # key verbatim, so a hostile key (`{"victim@example.com": 1}`, or a
            # raw key under a wildcard like `usage`) leaked whole. Keys are now
            # validated exactly like values:
            #   - a DECLARED child key is a schema literal → safe, recurse;
            #   - any other key must itself be a safe token (a field NAME, not
            #     raw) to survive at all; an email/prose key is DROPPED entirely
            #     (never emitted), so the key cannot be a leak channel;
            #   - a non-declared key survives ONLY under a wildcard that
            #     declares its keys platform-generated; otherwise the entry is
            #     dropped WHOLE (GR4-R4). Emitting an undeclared key with a
            #     redacted value still published the KEY, and a token-shaped
            #     secret is a perfectly good key — so an unknown key is
            #     omitted, not merely emptied.
            if k == _WITHHELD_LEGACY:
                # Historical signal on an already-stored row: preserve the FACT
                # by re-emitting it in the current representation, never the
                # old count (which was itself a raw channel).
                if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                    withheld = True
                continue
            if k == _WITHHELD:
                # Our own withheld flag. It must survive re-projection or
                # projection stops being a fixed point (the at-rest backfill
                # and its verifier both rely on idempotence). Safe to accept
                # from input BECAUSE it is a boolean: `True` is the only value
                # it can hold, so a forged one smuggles nothing. Anything else
                # is dropped rather than echoed.
                if v is True:
                    out[k] = True
                else:
                    # R6 F2: a reserved field holding anything but `True` is
                    # raw input wearing our field name. Dropping it silently
                    # left the object looking COMPLETE; dropping an entry is
                    # exactly what the flag exists to report.
                    withheld = True
                continue
            if k in node.children:
                out[k] = _project(node.children[k], v)
            elif not _safe_key(k):
                # Hostile key (prose/email/whitespace) → drop the entry. R5:
                # this branch dropped SILENTLY, leaving no withholding signal
                # at all; every dropped entry must raise the flag.
                withheld = True
                continue
            elif node.wildcard is not None and node.wildcard_keys == "platform":
                # Platform-generated key (a step id) — safe to emit.
                out[k] = _project(node.wildcard, v)
            else:
                # DATA-keyed wildcard, or no wildcard at all: the key may carry
                # content that no shape test can rule out. Drop it whole and
                # COUNT it — the count preserves the completeness signal the
                # old `key: [redacted]` form carried (an operator can still see
                # that something was withheld here, and a grant holder can
                # recover exactly what) without publishing the key itself.
                withheld = True
                continue
        if withheld:
            out[_WITHHELD] = True
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
            # GR4-R4: these ids are EXTERNALLY supplied — a webhook `id` is
            # whatever the caller sends, a `message_id` whatever the provider
            # sends. Round 3 tightened the SHAPE test here (token, so no `@`),
            # which closed `id: victim@example.com` but not
            # `id: AKIAIOSFODNN7EXAMPLE`: a token-shaped secret is
            # indistinguishable from a routing token BY SHAPE. Source is the
            # only sound test, and the source is outside — so the id is
            # withheld below grant. It stays fully recoverable to a grant
            # holder from the vaulted raw payload.
            safe[key] = _REDACTED_FIELD
    return safe


def safe_tool_call(
    tc: dict[str, Any], *, known_tools: frozenset[str] | None = None
) -> dict[str, Any]:
    """One tool-call record → non-sensitive metadata: parameter ARITY (a
    count, not the names — F1d), result status, and a content byte size — never
    raw input/result/error text or parameter names.

    EVERY field is reconstructed and VALIDATED, including the projection's OWN
    structural fields (G-Trace-Review-4 F1): `name` must be a token (else
    redacted), `pinned`/`pin_overrides` collapse to booleans. There is NO
    trusted shortcut — the previous `input_key_count`-present fast path returned
    an attacker-shaped record unchanged, so `{"input_key_count":0,"name":<raw>}`
    survived AND `output_has_raw` reported no vault need. Idempotence now comes
    from validation being a fixed point: an already-projected record has no raw
    `input`/`result`, so its derived signals are read back from its own
    (validated) safe fields.

    A `_redacted` marker is NEVER trusted as an input capability (re-review
    2026-08-03 F1): raw `input`/`result` is projected regardless of any marker a
    caller forged on.

    `known_tools` is the RESOLVED catalog, supplied per call (GR4-R4). There is
    no process-wide default: omitting it withholds the name (R5 F2). A tool name is chosen by the
    MODEL and is recorded even when dispatch rejects it, so a token test on it
    validates nothing — `exfiltrate_sk_live_51H8xQ2` is a perfectly good token.
    The name is therefore emitted only when it resolves to a catalog entry;
    anything else is the model's own string and is withheld. Omitting
    `known_tools` FAILS CLOSED (no catalog, no name), because a caller that
    cannot say what resolved cannot vouch for the string either."""
    projected = "input" not in tc and "result" not in tc and "input_key_count" in tc
    if projected:
        # Already-projected: rebuild from its OWN safe fields (validated), so a
        # forged extra key or a hostile `name` cannot ride through. Derived
        # signals come from the record because the raw result is gone.
        result_ok = bool(tc.get("result_ok"))
        error_present = bool(tc.get("error_present"))
        input_key_count = tc["input_key_count"] if _count(tc.get("input_key_count")) else 0
        content_bytes = tc.get("content_bytes")
        content_bytes = content_bytes if _count(content_bytes) else None
    else:
        result = tc.get("result") or {}
        content = result.get("content")
        result_ok = not result.get("error")
        error_present = bool(result.get("error"))
        # F1d: only the arity survives — never the model-chosen parameter names.
        input_key_count = len(tc.get("input") or {})
        content_bytes = (
            len(json.dumps(content, sort_keys=True, default=str).encode())
            if content is not None
            else None
        )
    safe: dict[str, Any] = {
        # F1 (G-Trace-Review-4): validate the projection's OWN fields. A tool
        # GR4-R4: resolved against the catalog, not shape-tested. A
        # model-chosen name that does not resolve is withheld.
        # R5 F6: a `name` that is a list/dict is UNHASHABLE, and membership
        # raised TypeError — the projector must be total over hostile input.
        "name": _resolved_tool_name(tc.get("name"), known_tools),
        "input_key_count": input_key_count,
        "result_ok": result_ok,
        "error_present": error_present,
        # `pinned`/`pin_overrides` are parameter KEY names (agent.py) — the same
        # channel as input_keys — so keep only token-shaped elements; prose,
        # emails and whitespace are dropped.
        # R5 §3.4: these are parameter NAMES taken from definition config, so
        # they are exactly the tool-name problem one level down — shape cannot
        # establish their origin, and with no canonical tool-parameter schema
        # to resolve them against (the process catalog was removed, R5 F2) the
        # names are withheld. The SIGNAL each carries is preserved as a
        # boolean: whether the call had pinned params, and whether the model
        # tried to override one (the action-surface probe worth auditing). A
        # boolean cannot carry a value, so re-reading it from an already
        # projected record is safe where a count would not be.
        "pinned_present": bool(_token_list(tc.get("pinned"))) or tc.get("pinned_present") is True,
        "pin_overrides_present": bool(_token_list(tc.get("pin_overrides")))
        or tc.get("pin_overrides_present") is True,
        "_redacted": _REDACTED_TOOL,
    }
    if content_bytes is not None:
        # Byte length only — the truncated hash was dropped (external review
        # 2026-08-01 nonblocking note: a hash is an equality/dictionary
        # oracle for low-entropy results; there's no operational use for it
        # in ordinary-reader responses).
        safe["content_bytes"] = content_bytes
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
        # R5 F3b: the withheld-keys flag is a redaction marker too. An object
        # reduced to `{_WITHHELD: True}` carries no `[redacted …` string and no
        # `_redacted` key, so this returned False and the completeness
        # predicate reported a merged raw retrieval COMPLETE when entries had
        # in fact been dropped.
        if is_withheld_marker(obj):
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


# Audit-detail fields that carry RAW content wherever they appear. Engine audit
# details are otherwise engine-AUTHORED operational metadata (ids, counts, flags,
# connector/step names) that is safe at rest; only these few carry exception
# text, model content, or a correspondent key. `escalation_requested` adds its
# model-authored `reason`/`context` by action. `trigger`/`output` are already
# projected at their own write sites, so they are not repeated here.
_RAW_AUDIT_FIELDS = (
    "error",
    "exception",
    "entity",
    "params",
    "observation_text",
    # `query` is the correspondent-derived recall query written by a SUCCESSFUL
    # memory_recalled audit (executor `recalled.query`) — raw (GR4-r2 F3). Its
    # absence here was the denylist gap the guide flagged, already live.
    "query",
    "recall",
)


def project_audit_detail_at_rest(action: str | None, detail: Any) -> Any:
    """The AT-REST (and verifier) projection of one audit detail under the flip
    (B1): remove raw, KEEP safe operational metadata. Action-aware, so it agrees
    with the read dispatcher and the verifier (G-Trace-Review-4 F4):

    - `tool_call`: the whole detail IS a tool-call record → `safe_tool_call`;
    - `escalation_requested`: `reason` + `context` are model-authored → redacted;
    - otherwise: redact only the known raw-bearing fields in place, so operational
      ids/counts/flags survive (a default-deny schema would wrongly drop them and
      the verifier would false-flag the row).

    Idempotent: a field already holding a generated marker is left as-is. NEW raw
    audit fields must be added to `_RAW_AUDIT_FIELDS` — that is the one thing a
    reviewer of a new audit write should check."""
    if not isinstance(detail, dict):
        return detail
    if action == "tool_call":
        return safe_tool_call(detail)
    out = dict(detail)
    raw_keys: tuple[str, ...] = _RAW_AUDIT_FIELDS
    if action == "escalation_requested":
        raw_keys = (*raw_keys, "reason", "context")
    for k in raw_keys:
        v = out.get(k)
        # Skip None and an already-generated marker (idempotence). Only a string
        # can be a marker — `context` is a dict, so guard the membership test.
        if k in out and v is not None and not (isinstance(v, str) and v in _GENERATED_MARKERS):
            out[k] = _REDACTED_FIELD
    return out


def redact_tool_data(obj: Any, admin: bool, *, kind: str) -> Any:
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
