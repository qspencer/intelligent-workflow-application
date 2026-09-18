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
from enum import StrEnum
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


class Owner(StrEnum):
    """WHO produced a value — the distinction the projector could not make.

    Six review rounds each closed named leaks and each found more, because the
    projector knows the asset KIND and the field PATH but not the PRODUCER.
    Shape cannot supply it: `model: "attacker-supplied"` is a fine token, and a
    registered `noop` function returns its config unchanged into the same
    schema the engine's own output uses.

    So ownership is DECLARED, per field, and resolved at the boundary where the
    value enters rather than guessed at projection time.
    """

    #: The engine computed it (Bedrock usage, our cost arithmetic, a hash WE
    #: took, a stamp WE wrote). Trustworthy only because the ENGINE built the
    #: dict — which is why a deterministic function's output may not carry
    #: these keys at all (enforced at the boundary, in the executor).
    ENGINE = "engine"
    #: Operator-approved definition content (step ids, capability globs, the
    #: function name). Approved by authorship, not by spelling.
    CONFIG = "config"
    #: Parsed from model output, or derived from it. A score is a MODEL'S
    #: claim; being a float does not make it a platform measurement. Withheld
    #: until a field has a stated release rule.
    BUSINESS = "business"
    #: Metadata the projection itself generates (its markers and stamps).
    PROJECTION = "projection"


#: Ownership of every field declared at the ROOT of each asset schema. There is
#: no default: an unclassified field is a build error, exactly as a missing
#: asset kind is — a default would be the same guess this taxonomy exists to
#: remove. `test_every_declared_field_has_a_declared_owner` pins totality.
_OWNERSHIP: dict[str, dict[str, Owner]] = {
    "step_output": {
        "model": Owner.ENGINE,
        "usage": Owner.ENGINE,
        "cost_usd": Owner.ENGINE,
        "iterations": Owner.ENGINE,
        "stop_reason": Owner.ENGINE,
        "memory_written": Owner.ENGINE,
        "memory_hash": Owner.ENGINE,
        "recall_hash": Owner.ENGINE,
        "num_steps": Owner.ENGINE,
        "tool_calls": Owner.ENGINE,
        # Parsed out of model output — the model's claims, not measurements.
        "parse_ok": Owner.BUSINESS,
        "faithfulness_score": Owner.BUSINESS,
        "category_score": Owner.BUSINESS,
        "relevance_score": Owner.BUSINESS,
        "concern_count": Owner.BUSINESS,
        "needs_tests": Owner.BUSINESS,
        "_redacted": Owner.PROJECTION,
    },
    "step_row": {
        "id": Owner.ENGINE,
        "instance_id": Owner.ENGINE,
        "attempt": Owner.ENGINE,
        "state": Owner.ENGINE,
        "started_at": Owner.ENGINE,
        "completed_at": Owner.ENGINE,
        "error": Owner.ENGINE,
        "output": Owner.ENGINE,
        "step_id": Owner.CONFIG,
        "_redacted": Owner.PROJECTION,
    },
    "instance": {
        "id": Owner.ENGINE,
        "state": Owner.ENGINE,
        "created_at": Owner.ENGINE,
        "started_at": Owner.ENGINE,
        "completed_at": Owner.ENGINE,
        "error": Owner.ENGINE,
        "total_tokens": Owner.ENGINE,
        "total_cost_usd": Owner.ENGINE,
        "context": Owner.ENGINE,
        "trigger_payload": Owner.ENGINE,
        "org_id": Owner.CONFIG,
        "owner_user_id": Owner.CONFIG,
        "workflow_id": Owner.CONFIG,
        "_redacted": Owner.PROJECTION,
    },
    "context": {
        "instance_id": Owner.ENGINE,
        "total_tokens": Owner.ENGINE,
        "total_cost_usd": Owner.ENGINE,
        "dry_run": Owner.ENGINE,
        "steps": Owner.ENGINE,
        "trigger": Owner.ENGINE,
        "org_id": Owner.CONFIG,
        "workflow_id": Owner.CONFIG,
        "capabilities": Owner.CONFIG,
        "_redacted": Owner.PROJECTION,
    },
}


def owner_of(kind: str, field: str) -> Owner | None:
    """Declared owner of a ROOT field, or None if the kind declares none."""
    return _OWNERSHIP.get(kind, {}).get(field)


#: BUSINESS fields cleared for ordinary-reader display. The default is
#: WITHHELD. R7: a release entry names its ASSET/PATH, its AUTHORIZED PRODUCER
#: and its reason — a field NAME cannot establish who computed a value, which
#: is the whole point of the taxonomy.
#:
#: | field      | asset/path              | authorized producer            |
#: |------------|-------------------------|--------------------------------|
#: | `parse_ok` | `step_output.parse_ok`  | the `record_*` parser functions |
#:
#: Reason, stated as a bounded status disclosure rather than "a boolean tells
#: you nothing": `parse_ok` discloses EXACTLY ONE BIT — whether the authorized
#: parser could read the model's output as the declared shape. It is the
#: operational signal that separates "the step ran and the model returned
#: junk" from "the step failed", which nothing else visible below grant
#: distinguishes. It carries no part of the model's text, and its producer is
#: verified at the boundary (`_PARSE_OK_PRODUCERS`), not inferred from the key.
_RELEASED_BUSINESS: frozenset[str] = frozenset({"parse_ok"})

#: The only step functions permitted to emit `parse_ok`. Any other function
#: returning that key is claiming a parse it did not perform, so the key is
#: dropped at the boundary (R7 P1).
#: Kept EXPLICIT rather than derived at runtime — a security control should be
#: readable, not computed. It is pinned against reality instead:
#: `test_parse_ok_producers_match_the_functions_that_compute_it` fails the
#: build if a registered function computes `parse_ok` and is missing here, or
#: if a name here no longer computes it. (The first version of this list was
#: written from memory and had `record_invoice` for what is really
#: `record_invoice_extraction`, which silently stripped a legitimate field from
#: the invoice pipeline — caught by its own test, but only by luck of coverage.)
_PARSE_OK_PRODUCERS: frozenset[str] = frozenset(
    {
        "record_email_triage",
        "record_pr_triage",
        "record_paper_triage",
        "record_evaluation",
        "record_invoice_extraction",
    }
)


#: Field names the PROJECTOR owns, wherever they appear. Held separately from
#: the schemas because ownership of a name is a property of the projector, not
#: of one asset: the version stamps were removed from the projected output
#: (self-found, ledger M2 — a supplied `projector_version` survived as
#: projection metadata, and nothing ever read it from the JSON), but a FUNCTION
#: still must not be able to emit them. Defence in depth: the projector drops
#: them, and the boundary refuses them.
_RESERVED_PROJECTION_FIELDS: frozenset[str] = frozenset(
    {
        "_withheld_keys",
        "_withheld_key_count",
        "_redacted",
        "projector_version",
        "projection_schema_version",
    }
)


def function_may_emit(field: str, function_name: str) -> bool:
    """Whether `function_name` is an authorized producer of `field`.

    PROJECTION-owned fields have NO function producer — the projector writes
    them — and a released BUSINESS field has a named one."""
    if field in _RESERVED_PROJECTION_FIELDS:
        return False
    if _OWNERSHIP.get("step_output", {}).get(field) is Owner.PROJECTION:
        return False
    if field == "parse_ok":
        return function_name in _PARSE_OK_PRODUCERS
    return True


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


#: A tool call's parameter arity above this is not operationally meaningful,
#: and the number's only remaining job would be to carry a value.
_MAX_ARITY = 32


def _bounded_arity(value: Any) -> int:
    """Clamp a parameter count so it cannot carry data (R7 §4.4).

    `input_key_count` is PROJECTION metadata — we compute it — but on the
    already-projected branch the raw is gone, so it can only be read back from
    the record, and the reviewer showed an arbitrary supplied number surviving
    there as if we had produced it. It cannot be recomputed and it cannot be
    dropped (projection must stay a fixed point), so it is BOUNDED: an int in
    0..32, which is ~5 bits instead of 64. Clamping is idempotent.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return min(value, _MAX_ARITY)


def _bounded_size(value: Any) -> int:
    """Reduce a byte size to its ORDER OF MAGNITUDE, for the same reason.

    A size must stay meaningful over a huge range, so clamping is wrong; it is
    rounded down to a power of ten instead ("about 10 KB"). That is ~10
    distinct values, and it is a fixed point: the bucket of a bucket is itself.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return 0
    power = 10 ** (len(str(value)) - 1)
    return int(power)


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

PROJECTOR_VERSION = "8"  # v8: the five fields v7 declared used `_ID`, which
# admits `@` (it exists for operator-identity paths). They are ROUTING ids, so
# the rule at `_OPAQUE_ID_RE` says `_short_token`. Caught by the round-12
# forgery pass, not by a reviewer: `from_step_id: "victim@example.com"`
# survived at rest and to grant-less readers. v7 is already stamped on
# production rows, so this is a new version rather than an edit.
#
# v7: the AT-REST AUDIT TIGHTENING (2026-09-18).
# `project_audit_detail_at_rest` was a denylist, so anything unlisted passed
# through and at rest held strictly MORE than a grant-less reader could see —
# the model-chosen tool name among it. It is now the read path itself. Five
# fields were declared in `_AUDIT_DETAIL` at the same time, and only five: the
# escalation link the API filters on, plus the fork-lineage/connector trail
# already test-pinned. Audit output changed, so the version moves.
#
# v6: SELF-FOUND (ledger M2 detector): the version
# STAMPS were declared inside the projected output, where a supplied
# `projector_version: "AKIAIOSFODNN7EXAMPLE"` survived as projection metadata.
# Nothing read them from the JSON — every consumer uses the ROW COLUMN — so an
# in-output copy was pure attack surface AND a second source of truth for the
# stamp, which is the exact duplication F5 was raised about. Removed; the row
# column is the authority. Output changed, so the version moves.
# (arity clamped, size to an order of magnitude), so a v4 record carrying
# `content_bytes: 1234` re-projects to 1000 — a changed output, which is a
# changed version. Caught by reasoning, NOT by the guard: its corpus had only
# small tool calls, so nothing exercised the range the bounding alters. The
# corpus now covers it.
# "3" — round 7 withholds model-derived BUSINESS fields that round 6 emitted,
# so a round-6 record re-projected under round 7 disagrees and reads as
# TAMPERING rather than degrading. Bumping PROJECTION_SCHEMA_VERSION did not
# cover it: `verify_projection_agreement` compares the PROJECTOR version only.
# Twice now this was missed by hand, which is the argument for the
# golden-fixture guard: any change to emitted output must move this.
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
    #: Owner of each declared child (R7 P1). This lives on the NODE, not on the
    #: asset kind, because the same node is reached at many paths — the step
    #: output schema is the root of `step_output` AND sits under
    #: `context.steps.<id>`, `step_row.output` and an audit detail's `output`.
    #: Keyed by kind at the entry point, the rule fired only on the OUTERMOST
    #: object, so scores were withheld from a stored step output and retained
    #: in the very same run's instance context.
    owners: dict[str, Owner] = field(default_factory=dict)


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
        "_redacted": _MARKER,
    },
    owners=_OWNERSHIP["step_output"],
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
        # --- Declared by the 2026-09-18 at-rest tightening, and ONLY these.
        # When at-rest became the read path, every undeclared field began to
        # be withheld. These five are what the PLATFORM itself needs or what
        # was already test-pinned as the operator trail; each is an
        # engine-owned identifier or a fixed token, never model- or
        # correspondent-derived. Everything else the tightening strips stays
        # stripped — widening what a grant-less reader sees is a separate,
        # deliberate decision, not a side effect of tightening at rest.
        #
        # `original_id` links `escalation_resolved` back to its request and
        # the escalations API filters on it, so withholding it would not hide
        # the link — it would BREAK resolution, permanently.
        # `_TOKEN`, NOT `_ID`: these are ROUTING ids. `_ID`/`_OPAQUE_ID_RE`
        # admits `@` because operator-identity paths (`actor_id`, `sub`)
        # legitimately hold an email; the rule stated at `_OPAQUE_ID_RE` is
        # that routing and model/state fields use `_short_token` instead.
        # They were `_ID` for half an hour and the round-12 forgery pass
        # caught it: a step id of `victim@example.com` passed through at rest
        # AND to grant-less readers. Step ids come from the definition, which
        # a scaffolded or imported workflow does not fully control.
        "original_id": _TOKEN,
        # Fork lineage + connector identity: the operator trail pinned by
        # `test_operational_detail_is_untouched_at_rest`.
        "source_instance_id": _TOKEN,
        "from_step_id": _TOKEN,
        "preserved_step_ids": Seq(_TOKEN),
        "connector": _TOKEN,
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
    },
    owners=_OWNERSHIP["context"],
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
        "_redacted": _MARKER,
    },
    owners=_OWNERSHIP["instance"],
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
        "_redacted": _MARKER,
    },
    owners=_OWNERSHIP["step_row"],
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
                # old count (which was itself a raw channel). R7 P2: an
                # INVALID value here is content being removed, exactly as under
                # the current key, so it raises the flag rather than vanishing.
                if (isinstance(v, int) and not isinstance(v, bool) and v > 0) or v is not None:
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
                if node.owners.get(k) is Owner.BUSINESS and k not in _RELEASED_BUSINESS:
                    # R7 P1: withheld wherever this node appears, not only when
                    # it is the outermost object. Keyed by asset kind at the
                    # entry point, the rule fired on the root alone — so a run
                    # withheld scores from its stored step output and retained
                    # them in its own instance context.
                    withheld = True
                    continue
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
        # R7 §4.4: read back from the record because the raw is gone — so
        # BOUNDED, since an unbounded number read from input is a channel, not
        # metadata we produced.
        input_key_count = _bounded_arity(tc.get("input_key_count"))
        cb = tc.get("content_bytes")
        content_bytes = _bounded_size(cb) if _count(cb) else None
    else:
        result = tc.get("result") or {}
        content = result.get("content")
        result_ok = not result.get("error")
        error_present = bool(result.get("error"))
        # F1d: only the arity survives — never the model-chosen parameter names.
        # Bounded on THIS branch too, or projection stops being a fixed point:
        # an exact count here and a clamped one on re-projection would disagree
        # for any call above the bound.
        input_key_count = _bounded_arity(len(tc.get("input") or {}))
        content_bytes = (
            _bounded_size(len(json.dumps(content, sort_keys=True, default=str).encode()))
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


def project_audit_detail_final(action: str | None, detail: Any) -> Any:
    """The at-rest policy, defined as **the read path**: at rest must never
    hold more than a reader without a raw-trace grant can already see.
    Anything beyond that belongs in the vault, recoverable by a grant holder.

    Introduced while at-rest was still the lenient denylist, so that vaulting
    could be scoped by the FINAL policy before the tightening landed and no
    window existed in which projection destroyed unvaulted raw (round 11).
    Since 2026-09-18 at-rest IS this, and the separation is kept only because
    the name states the intent.
    """
    if not isinstance(detail, dict):
        return detail
    if action == "tool_call":
        return safe_tool_call(detail)
    return redact_tool_data(detail, admin=False, kind="audit_detail")


def project_audit_detail_at_rest(action: str | None, detail: Any) -> Any:
    """The AT-REST projection of one audit detail under the flip (B1).

    **Tightened 2026-09-18 to BE the final policy.** It was a denylist
    (`_RAW_AUDIT_FIELDS` plus action-specific extras), which meant anything
    unlisted passed through: at rest we stored strictly MORE than a reader
    without a raw-trace grant could see, and the excess was exactly the
    attacker-influenced material — the model-chosen tool name in
    `tool_param_override_blocked` among it.

    Now at rest holds no more than the read path releases, and everything
    beyond that is vaulted first (`RawTraceVault.record_audit_detail`), so
    the tightening withholds rather than destroys.

    This is now one function with `project_audit_detail_final`. Keeping two
    names that must return the same thing is the M3 class, so this delegates
    rather than duplicating; the verifier and the vaulting predicate ask the
    same question again and share it.
    """
    return project_audit_detail_final(action, detail)


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
