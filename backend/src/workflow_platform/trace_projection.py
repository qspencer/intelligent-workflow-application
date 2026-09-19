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
    #:
    #: In the per-(action, field) registry it also covers a NESTED container
    #: that reaches a reader only as this projector's own rendering of it.
    #: `step_completed.output` and `workflow_started.trigger` are not
    #: released: `_project` walks their declared schema, drops every
    #: undeclared key, and withholds every BUSINESS child via that schema's
    #: own `owners` table. What a grant-less reader sees is therefore the
    #: projection's construction, not the stored value — which is a different
    #: claim from "the engine produced this", and the only one that is true
    #: of a deterministic step's output.
    #:
    #: It is not an escape hatch: a rule may claim this owner ONLY by
    #: pointing at a node that carries per-child ownership or projects
    #: structurally, pinned by
    #: `test_a_PROJECTION_owned_rule_delegates_to_a_schema`.
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

PROJECTOR_VERSION = "17"  # v17: the typed SUBJECT reference
# (G-Trace-Subject-Identity stage 2). Four actions that carry a subject —
# memory_observed, memory_recalled, user_created, user_updated — gain a
# `subject` object beside the field naming the subject directly.
#
# It is DISCLOSED where `memory_*`'s raw `user_id` stays withheld, and that
# is the point rather than an inconsistency: a reader without a raw-trace
# grant can now tell that two entries concern the same subject without
# learning who. `ref` is `users.id` for a platform user and an HMAC keyed
# over `(address, org)` for a mailbox, so it correlates within a tenant and
# not across one.
#
# v16: the GOVERNANCE surface joins the registry
# (G-Trace-Chokepoint-Rest). Eight actions written by the API and auth
# routers — auth_login(_failed), user_created/updated, org_created/renamed,
# workflow_deleted, instance_deleted — classified so those writers can move
# onto the audit chokepoint without the move destroying what they record.
#
# Every one is INSTANCE-LESS, and an instance-less entry has no vault, so
# "withheld" there means GONE rather than grant-recoverable. Six of the
# eight were losing fields to the read path already while storing them in
# the clear; registering makes at rest and the read path agree WITHOUT
# discarding the operator trail, which is the only outcome that closes the
# gap rather than moving it.
#
# One new leaf: `_LABEL`, an operator-authored display name (org names).
# It is the first free-text position the projector discloses, on narrow
# ground stated at its definition — Administrator authorship,
# Administrator-only audience, and destruction as the alternative.
#
# v15: `memory_recalled` gains the two timings
# that explain where a run's wall clock goes — `recall_seconds` and
# `outcomes_seconds`, both engine-measured floats. Added after measuring
# that `triage` averaged 155.8s of which the model call was 0.8s: the cost
# was 40 sequential learned-memory outcome writes, and no field recorded
# it. A number nobody records is a number nobody checks.
#
# v14: `alert_abandoned_pause` classified — the
# monitoring check added when fixing the stranded-RUNNING orphans created a
# second blindness (a correct PAUSED state that nothing watched). Four
# fields, all platform arithmetic or operator-configured thresholds; the
# instance's `error` string is deliberately NOT emitted, because on a budget
# pause or a failure-then-retry it can carry step error text.
#
# A new action has no stored rows, so nothing can disagree under it and the
# bump buys no compatibility. It happens anyway: the rule is that a change
# to what the projection emits moves this constant, and the reason the rule
# is absolute is that two people already judged an exception safe and were
# wrong (rounds 6 and 7, both read as tampering afterwards). An absolute
# rule costs one regenerated fixture; a judged one costs the guard.
#
# v13: two v12 validators corrected against the
# production data they were written for. Both were caught by running the
# projector over the live audit table rather than by review, which is the
# lesson: a rule is a claim about values, and only the values settle it.
#
#   - `alert_*.window_seconds` was `_COUNT` (int-only). The thresholds on
#     `MonitoringConfig` are FLOATS, so every live value was redacted. The
#     v12 corpus case used an int literal, so the golden guard saw nothing.
#   - `workflow_completed.steps` was given the context-snapshot node because
#     `_AUDIT_DETAIL` declares that shape under the same key. Production
#     stores a step-id LIST there — the same content `step_ids` now holds —
#     so 6,416 rows read back redacted. A shared NAME is not a shared type.
#   - `_TS_RE`'s tail bound of 20 rejects a full `isoformat()` with
#     microseconds and an offset (258 `last_run_at` values). Raised to 26;
#     the charset and the 40-character total were always the control.
#
# Corpus cases now carry the types production stores, not the types that
# were convenient to type.
#
# v12: the registry extended from one action to
# EIGHTEEN — the engine's own execution trail (step/workflow lifecycle, fork,
# budget), the five monitoring alerts, and learned-memory recall. Together
# with `memory_observed` that is 93% of the production audit log by volume,
# so the registry is now the projection most audit reads actually take.
#
# Fields were enumerated from the production table per action, not from the
# source, so shapes only historical rows carry (`workflow_completed.steps`)
# are classified too.
#
# THIS IS A WIDENING, and the largest one since v9. 28 fields across 11
# actions are released that the flat schema withheld — the whole of the
# monitoring alerts' arithmetic (`rate`, `failed`, `depth`, `tokens`,
# `window_seconds`, the thresholds beside them), the budget numbers, the
# postcondition's `min_success`/`actual_success`/`stop_reason`, recall's
# `edges`/`episodes`/`token_budget`/`uses_recorded`, and two `unexpected`
# booleans. The flat schema withheld them by OMISSION: it was written for
# grant-decision entries and never classified a monitoring alert, so an
# operator reading "error rate alert" got an entry with every number
# stripped. Each released field is a scalar the PLATFORM computed or a
# threshold the OPERATOR configured; none is content, and none is derived
# from a trigger, a message or model output.
# `test_the_registry_releases_exactly_these_fields_beyond_the_flat_schema`
# freezes that set, so the next action added cannot widen quietly.
#
# Two things narrow or stay narrow:
#
#   - `alert_stale_trigger.account` is WITHHELD by rule rather than by
#     omission — an operator-authored value that is nonetheless a mailbox
#     identity, the `memory_observed.user_id` class.
#   - `step_completed.output` and `workflow_started.trigger` keep their
#     nested schemas under `Owner.PROJECTION`, whose meaning is widened (and
#     test-constrained) to cover a value a reader sees only as this
#     projector's rendering of it.
#
# v11: the per-(action, field) REGISTRY, first
# action (`memory_observed`). Classification by SOURCE, stated per action
# rather than per field, because the same NAME has different producers under
# different actions — `evidence_ref` is engine-set in the fork and judge
# paths and context-resolved in the observe path, which a flat table got
# wrong (R14 F1). Each rule carries owner + validator + disclosure, because
# an ownership label is an assertion and the validator is what enforces it.
#
# Output changes for `memory_observed` only, and only by releasing
# `backfill` (an engine-set bool, the same class as `attempt`). Everything
# the v10 flat schema withheld here stays withheld.
#
# v10: the widening, CORRECTED by source (R14 F1).
# v9 declared 19 fields by SHAPE and we argued content could not survive the
# validators. The reviewer disproved it: `evidence_ref` is resolved from the
# workflow CONTEXT, so a trigger field became a token-shaped value released
# to a reader with no grant — while the same value was withheld in the
# stored trigger. Shape bounds DAMAGE, not PROVENANCE.
#
# v10 keeps only fields whose SOURCE is established — canonical ids from
# records, hashes and counts computed by the emitting component — turns the
# classifications into CLOSED ENUMS, and withdraws `evidence_ref` (input-
# derived) and `event_type` (free author-controlled string). A narrower
# projection is a changed projection, so the version moves rather than v9
# being edited: historical version meanings stay intact.
#
# v9: the AT-REST WIDENING (operator decision,
# 2026-09-18). v8 made at rest equal the read path and 39% of audit entries
# then withheld EVERY field, because `_AUDIT_DETAIL` was written for
# governance entries and never classified the engine-EXECUTION fields.
# 19 of them are now declared as `_TOKEN`/`_COUNT`/`_AMOUNT` — never `_ID`,
# per v8. What decided it: a grant-less reader ALREADY gets `workflow_id`
# on the instance surface, so withholding it on the audit surface protected
# nothing and only cost the trail. Measured 39% -> 1% fully withheld.
# `user_id` (an email in 416/416 production cases), `trigger` and `output`
# stay withheld. Audit output changed, so the version moves.
#
# v8: the five fields v7 declared used `_ID`, which
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
# The tail bound was 20, which rejects a full `datetime.isoformat()` with
# microseconds AND an offset — `2026-09-15T10:49:28.384385+00:00` has a
# 21-character tail. 258 production `last_run_at` values were redacted by it.
# The charset (digits and `:.+Z-`) plus the 40-character total is what bounds
# this; the tail count was never the control.
_TS_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.+Z-]{0,26}$")

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

#: A context snapshot's per-step outputs. Named because three schemas reach
#: it (`_AUDIT_DETAIL.steps`, `_CONTEXT.steps`, and the registry's
#: `workflow_completed.steps`); two equal literals are two things to keep
#: equal, which is the M3 class.
_STEPS_SNAPSHOT = Obj(wildcard=_STEP_OUTPUT, wildcard_keys="platform")

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
        # --- The AT-REST WIDENING (2026-09-18, operator decision).
        #
        # The tightening made at rest equal the read path, and 39% of audit
        # entries then withheld EVERY field. `_AUDIT_DETAIL` was written for
        # governance entries and had never classified the engine-EXECUTION
        # fields, so their absence was an oversight rather than a judgement.
        #
        # What decided it was not the lost trail but an INCONSISTENCY: a
        # reader with no grant already gets `workflow_id` on the INSTANCE
        # surface. Withholding the same value on the audit surface protects
        # nothing — the reader fetches it next door — and only costs the
        # trail. Measured: entries withholding everything fall 39% -> 1%.
        #
        # Every field here is `_TOKEN` / `_COUNT` / `_AMOUNT`, never `_ID`.
        # `_ID` admits `@` because it exists for operator-identity paths, so
        # a routing id declared as `_ID` can carry an email — the v8 defect.
        # Verified against 4,000 production entries: 100% token-shaped, max
        # 43 chars, none containing `@` or a space.
        #
        # DELIBERATELY NOT declared:
        #   `user_id`    — an email in 416/416 production cases, and unlike
        #                  `actor_id` it names the person ACTED UPON, who may
        #                  be a third party. Grant-only.
        #   `trigger`, `output` — they have their own nested schemas above;
        #                  declaring them flat would bypass those.
        #   `emitter`    — never appeared in the sample, so there is no
        #                  evidence it is safe. Undeclared = withheld, which
        #                  is the right default for a field we cannot vouch for.
        "workflow_id": _TOKEN,
        "instance_id": _TOKEN,
        # NOT `steps`: that key is ALREADY declared below as an Obj of step
        # OUTPUTS (a context snapshot), and re-declaring it here would have
        # silently replaced that node — same dict literal, later key wins.
        # `workflow_completed` emits the completed step IDS, a different
        # thing, so it gets its own key rather than a union node.
        "step_ids": Seq(_TOKEN),
        "model": _TOKEN,
        # CLOSED ENUMS, not tokens. `author`/`derived_from` are
        # `Literal["user","third_party","system"]` at the writer, so they can
        # be validated against the set rather than accepted for being short.
        # R14 finding 1: "ownership labels alone do not establish source" —
        # the validator has to encode the closed set.
        "author": Leaf(_enum("user", "third_party", "system")),
        "derived_from": Leaf(_enum("user", "third_party", "system")),
        # ENGINE-COMPUTED: both are `"sha256:" + sha256(...)` inside the
        # service that emits them. No caller supplies them.
        "text_hash": _TOKEN,
        "context_hash": _TOKEN,
        #
        # WITHDRAWN by R14 finding 1 — declared in v9, removed in v10:
        #
        #   `evidence_ref` — resolved from the workflow CONTEXT via
        #   `ObservationSpec.ref_from`, a YAML-configured dotted path, so
        #   whatever the TRIGGER carries there becomes its value. It passes
        #   `_TOKEN`, and the reviewer demonstrated it reaching a reader with
        #   no grant while the same value was withheld in the stored trigger.
        #   Input-derived: withheld until there is an explicit disclosure
        #   policy, and a public reference should be built from an
        #   authoritative record instead.
        #
        #   `event_type` — a free `str` on the spec (default "chat"), so it is
        #   author-controlled, and in a SCAFFOLDED workflow the author is the
        #   model. Not a closed set, so not releasable on shape.
        "cost_usd": _AMOUNT,
        "threshold_seconds": _AMOUNT,
        "running_for_seconds": _AMOUNT,
        "input_tokens": _COUNT,
        "output_tokens": _COUNT,
        "facts": _COUNT,
        "quarantined": _COUNT,
        "edges": _COUNT,
        "observation": _COUNT,
        "trigger": TriggerPayload(),
        "trigger_payload": TriggerPayload(),
        "tool_calls": ToolCalls(),
        # A `step_completed` entry carries the step output verbatim
        # (`detail={"output": execution.output, ...}`), and some entries carry a
        # context snapshot. Both are declared with THEIR OWN schema rather than
        # inherited — that nesting is exactly what the path-scoped model exists
        # to express, and the flat name registry could never have named it.
        "output": _STEP_OUTPUT,
        "steps": _STEPS_SNAPSHOT,
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
        "steps": _STEPS_SNAPSHOT,
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


#: Closed sets the registry validates against, rather than accepting a value
#: for being short. Each is closed AT THE WRITE SITE, and the comment says
#: where — an enum whose set is only a convention is a token in disguise.
#: `step.type` is a Pydantic `Literal` on both step models.
_STEP_TYPE = Leaf(_enum("deterministic", "agentic"))
#: `_check_stale_email_triggers` emits this alert only for these two trigger
#: types; anything else `continue`s before the detail is built.
_EMAIL_TRIGGER_TYPE = Leaf(_enum("email", "gmail_poll"))
#: `WorkflowPolicy.budget_action`, a Pydantic `Literal`.
_BUDGET_ACTION = Leaf(_enum("notify", "pause", "escalate"))
#: An operator-authored DISPLAY LABEL — an org name and the like. Distinct
#: from `_CONFIG` only in allowing spaces, which is the whole reason it
#: exists: `_CONFIG` was shaped for capability globs and rejects any
#: whitespace, and "Test Org Beta" is not a glob.
#:
#: Disclosing free text at all is a departure from §1.1 ("free-form model
#: output is raw by taint"), so the narrow ground it stands on:
#:  - AUTHORSHIP. Org create/rename is Administrator-gated, so the text is
#:    operator-authored, the same class as the capability strings `_CONFIG`
#:    already releases. It is not third-party content.
#:  - AUDIENCE. Instance-less audit is Administrator-only (THREAT_MODEL §5),
#:    so the reader of these entries is the author's peer.
#:  - THE ALTERNATIVE IS DESTRUCTION, not withholding. An instance-less
#:    entry has no vault to fall back on, so "withhold" means the previous
#:    org name is gone — and `org_renamed.from` is the only record of it.
#: Bounded hard: printable, single line, <=120 chars. Control characters
#: are refused, not stripped.
_LABEL = Leaf(
    lambda v: (
        isinstance(v, str)
        and 0 < len(v) <= 120
        and v.isprintable()
        and "\n" not in v
        and "\r" not in v
    )
)
#: `LocalAuth._audit_failed`'s three call sites, which are the whole set.
_LOGIN_FAILURE_CAUSE = Leaf(_enum("unknown", "bad_password", "inactive"))
#: `auth/bootstrap.py`'s two origins.
_USER_ORIGIN = Leaf(_enum("permanent_admin", "test_seed"))
#: An IP literal as the login path records it. `_TOKEN`'s charset already
#: covers IPv4 and IPv6 (digits, `.`, `:`); named here so the intent is
#: readable at the rule.
_IP = _TOKEN

#: `agent.StopReason`, spelled out rather than imported — the projector is a
#: domain leaf and importing the agent package to read an enum would invert
#: that. `test_the_stop_reason_enum_is_the_agents_enum` pins the two equal,
#: so the copy cannot drift silently.
_STOP_REASON = Leaf(
    _enum(
        "end_turn",
        "tool_use",
        "max_iterations",
        "budget_exhausted",
        "max_tokens",
        "stop_sequence",
        "content_filtered",
        "guardrail_intervened",
        "error",
    )
)
#: `LearnedMemoryService.record_outcomes` returns exactly these counters
#: (`stats["failed"]`, `stats["upgraded" if ... else "recorded"]`). Declared
#: as a closed child set, NOT a wildcard: the keys are ours, and saying so is
#: cheaper than shape-testing keys we already know.
_USES_RECORDED = Obj(children={"failed": _COUNT, "recorded": _COUNT, "upgraded": _COUNT})


@dataclass(frozen=True)
class FieldRule:
    """What one field of one ACTION's audit detail is.

    Three things, deliberately separate, because the reviewer's round-16
    answer was that they are not interchangeable: *"an ownership label
    documents an assertion; writer-side construction and tests must support
    it"*.

    - `owner`    — WHO produces the value. The assertion.
    - `node`     — how it is validated. Shape, which bounds damage.
    - `disclose` — whether a reader with no raw-trace grant may see it.
                   Stated per field rather than inferred from the owner,
                   because an ENGINE-owned value can still be one we choose
                   to withhold.

    Ownership alone never releases anything: a field is disclosed only when
    `disclose` is True AND its node validates the value.
    """

    owner: Owner
    node: Node
    disclose: bool


#: The typed subject reference (G-Trace-Subject-Identity stage 2). It is
#: DISCLOSED where the raw `user_id` beside it is withheld — that is the
#: whole point: a reader without a raw-trace grant can tell that two
#: entries are about the same subject, and still cannot tell who.
#:
#: `ref` is `users.id` for a platform user and an HMAC pseudonym keyed over
#: `(address, org)` for a mailbox, so it is a within-tenant correlator by
#: construction. `org_id` is carried on the reference and never inferred
#: from it.
_SUBJECT = Obj(
    children={
        "kind": Leaf(_enum("platform_user", "mailbox", "unknown")),
        "ref": _TOKEN,
        "org_id": _ID,
    }
)

#: BUSINESS-owned fields the registry deliberately DISCLOSES, by
#: `(action, field)`. The general rule is that input-derived values are
#: withheld — `test_no_BUSINESS_owned_field_is_disclosed` enforces it — and
#: an exception must be named here rather than obtained by relabelling the
#: owner, which is the one move that would make the taxonomy useless.
#:
#: The sibling of `_RELEASED_BUSINESS` for step outputs (`parse_ok`), and
#: held to the same standard: state what the field is, why release is
#: necessary rather than convenient, and what release actually costs.
_REGISTRY_RELEASED_BUSINESS: dict[tuple[str, str], str] = {
    ("auth_login_failed", "email"): (
        "Whatever was typed at the login form, so caller-supplied and BUSINESS "
        "by provenance. Released because it IS the security trail this entry "
        "exists to be — 'who is being targeted, from where' is the whole "
        "content of a failed-login record, and the entry is instance-less so "
        "withholding destroys it rather than deferring it to a grant. Cost is "
        "bounded: `_ID` shape, an Administrator-only surface (THREAT_MODEL "
        "§5), and the login path is enumeration-resistant, so the row tells "
        "an attacker nothing about the account that they do not already know."
    ),
}

#: Per-(action, field) classification — the registry the round-14/16 reviews
#: asked for, replacing a flat per-field table that could not express the
#: same NAME having different PRODUCERS under different actions.
#:
#: That is not hypothetical: `evidence_ref` is engine-set to an instance id
#: in the fork and judge paths, and resolved from the workflow CONTEXT in the
#: observe path. A flat table classified it once and was wrong for one of
#: them — round-14 finding 1.
#:
#: Built incrementally, per the reviewer: *"start with the actions whose
#: metadata you want to expose"*. An action absent here falls back to the
#: flat `_AUDIT_DETAIL` schema, which is default-deny, so adding an action
#: can only ever RELEASE more — never accidentally leak by omission.
AUDIT_FIELD_RULES: dict[str, dict[str, FieldRule]] = {
    "memory_observed": {
        # --- ENGINE: computed by the component that emits the record.
        "observation": FieldRule(Owner.ENGINE, _COUNT, True),
        "facts": FieldRule(Owner.ENGINE, _COUNT, True),
        "quarantined": FieldRule(Owner.ENGINE, _COUNT, True),
        "input_tokens": FieldRule(Owner.ENGINE, _COUNT, True),
        "output_tokens": FieldRule(Owner.ENGINE, _COUNT, True),
        "cost_usd": FieldRule(Owner.ENGINE, _AMOUNT, True),
        # `"sha256:" + sha256(text)`, computed inside the service.
        "text_hash": FieldRule(Owner.ENGINE, _TOKEN, True),
        "model": FieldRule(Owner.ENGINE, _TOKEN, True),
        # Set by the offline backfill tool, never by a caller.
        "backfill": FieldRule(Owner.ENGINE, _BOOL, True),
        # Canonical ids from records. Present in the flat schema too, and
        # kept here deliberately: a registry entry that omits a field the
        # flat schema released would silently WITHDRAW it for that action.
        # `test_a_registry_action_does_not_silently_withdraw_a_flat_field`
        # is the coverage check for exactly that.
        "workflow_id": FieldRule(Owner.ENGINE, _TOKEN, True),
        "instance_id": FieldRule(Owner.ENGINE, _TOKEN, True),
        # --- CONFIG: declared in the workflow YAML.
        # Closed enums, so the validator encodes the set rather than
        # accepting anything token-shaped.
        "author": FieldRule(Owner.CONFIG, Leaf(_enum("user", "third_party", "system")), True),
        "derived_from": FieldRule(Owner.CONFIG, Leaf(_enum("user", "third_party", "system")), True),
        # A free `str` on the spec, so in a SCAFFOLDED workflow the model
        # wrote it. Classified, and withheld.
        "event_type": FieldRule(Owner.CONFIG, _TOKEN, False),
        # --- BUSINESS: derived from the workflow's input.
        # Resolved from the CONTEXT via `ObservationSpec.ref_from`, so the
        # trigger controls it. This is the round-14 P1.
        "evidence_ref": FieldRule(Owner.BUSINESS, _TOKEN, False),
        # The memory namespace key — a mailbox address in production, not a
        # platform user id. Withheld; see G-Trace-Subject-Identity.
        "user_id": FieldRule(Owner.BUSINESS, _ID, False),
        "subject": FieldRule(Owner.ENGINE, _SUBJECT, True),
    },
    # --- The ENGINE's own execution trail. These five actions are 93% of the
    # audit log by volume, and every field below was enumerated from the
    # production table (`select distinct jsonb_object_keys ... group by
    # action`) rather than from recollection, so a field that historical rows
    # carry and current code no longer writes is still classified.
    "step_started": {
        "type": FieldRule(Owner.CONFIG, _STEP_TYPE, True),
        "attempt": FieldRule(Owner.ENGINE, _COUNT, True),
    },
    "step_completed": {
        "attempt": FieldRule(Owner.ENGINE, _COUNT, True),
        # `execution.output`. NOT owned by the engine: a deterministic step's
        # output is whatever its registered function returned, which for
        # `noop`-shaped functions is its config and for a parser is model
        # output. What a grant-less reader gets is `_project(_STEP_OUTPUT,
        # ...)` — declared children only, BUSINESS children (the scores)
        # withheld by `_STEP_OUTPUT.owners`. See `Owner.PROJECTION`.
        "output": FieldRule(Owner.PROJECTION, _STEP_OUTPUT, True),
    },
    "step_failed": {
        "attempt": FieldRule(Owner.ENGINE, _COUNT, True),
        # Exception text. The ENGINE raised it, but the MESSAGE routinely
        # embeds the input that caused it (a path, a parse fragment, an API
        # body), so provenance follows the content, not the raiser. This is
        # the one field the whole vault exists for: withheld here, recoverable
        # with a grant.
        "error": FieldRule(Owner.BUSINESS, _TOKEN, False),
        "unexpected": FieldRule(Owner.ENGINE, _BOOL, True),
    },
    "step_retry": {
        "attempt": FieldRule(Owner.ENGINE, _COUNT, True),
        "error": FieldRule(Owner.BUSINESS, _TOKEN, False),
    },
    "step_postcondition_failed": {
        # `req.name` — a tool name from the step's YAML postcondition.
        "require_tool_call": FieldRule(Owner.CONFIG, _TOKEN, True),
        "min_success": FieldRule(Owner.CONFIG, _COUNT, True),
        "actual_success": FieldRule(Owner.ENGINE, _COUNT, True),
        # `AgentResult.stop_reason.value` — our own enum, and a WIDENING:
        # the flat schema never declared `stop_reason` on an audit detail, so
        # this action discloses one field more than before. Deliberate. It is
        # the only thing in the entry that says WHY the postcondition failed
        # (turn ended vs. iteration cap vs. guardrail), it is a closed set we
        # produce, and the alternative is an operator reading a failure with
        # no cause.
        "stop_reason": FieldRule(Owner.ENGINE, _STOP_REASON, True),
    },
    "workflow_started": {
        # `definition.id` — operator-authored, and the reader already has it
        # on the instance surface (the at-rest widening's argument).
        "workflow_id": FieldRule(Owner.CONFIG, _TOKEN, True),
        # The stored trigger, rendered by `safe_trigger_payload`: routing ids
        # only, sender and content stripped. PROJECTION for the same reason
        # `step_completed.output` is.
        "trigger": FieldRule(Owner.PROJECTION, TriggerPayload(), True),
    },
    "workflow_completed": {
        "step_ids": FieldRule(Owner.CONFIG, Seq(_TOKEN), True),
        # Historical rows only — current code emits `step_ids`. Classified
        # anyway: moving the action onto the registry without this would
        # withdraw it from every entry already stored.
        #
        # It is a step-id LIST, the same content `step_ids` now holds — NOT
        # the context snapshot that `_AUDIT_DETAIL.steps` declares. v12 read
        # the flat schema's node off the shared key name and inherited the
        # wrong shape, redacting 6,416 stored rows. The lesson is the M1 one
        # from the other direction: a shared NAME is not a shared type, and
        # the only way to know was to look at what production stores.
        "steps": FieldRule(Owner.CONFIG, Seq(_TOKEN), True),
    },
    "workflow_failed": {
        "error": FieldRule(Owner.BUSINESS, _TOKEN, False),
        # `str(exc)` on the timeout path, beside `error`.
        "exception": FieldRule(Owner.BUSINESS, _TOKEN, False),
        "unexpected": FieldRule(Owner.ENGINE, _BOOL, True),
    },
    "workflow_forked": {
        "source_instance_id": FieldRule(Owner.ENGINE, _TOKEN, True),
        # A step id from the definition. `_TOKEN`, never `_ID`: the v8 defect
        # was `from_step_id: "victim@example.com"` surviving because `_ID`
        # admits `@` for operator-identity paths.
        "from_step_id": FieldRule(Owner.CONFIG, _TOKEN, True),
        "preserved_step_ids": FieldRule(Owner.CONFIG, Seq(_TOKEN), True),
    },
    # --- BUDGET. `budget_escalated` shares the dict verbatim, so it shares
    # the rules; two names for one shape is not two classifications.
    "budget_exceeded": {
        "tokens_used": FieldRule(Owner.ENGINE, _COUNT, True),
        "tokens_limit": FieldRule(Owner.CONFIG, _COUNT, True),
        "cost_usd": FieldRule(Owner.ENGINE, _AMOUNT, True),
        "action": FieldRule(Owner.CONFIG, _BUDGET_ACTION, True),
    },
    # --- MONITORING. Emitted by `MonitoringService`, never by a request, so
    # every threshold is config read off `MonitoringConfig` and every measure
    # is arithmetic the service did.
    "alert_stuck_workflow": {
        "instance_id": FieldRule(Owner.ENGINE, _TOKEN, True),
        "workflow_id": FieldRule(Owner.CONFIG, _TOKEN, True),
        "running_for_seconds": FieldRule(Owner.ENGINE, _AMOUNT, True),
        "threshold_seconds": FieldRule(Owner.CONFIG, _AMOUNT, True),
    },
    "alert_stale_trigger": {
        "workflow_id": FieldRule(Owner.CONFIG, _TOKEN, True),
        "trigger_type": FieldRule(Owner.CONFIG, _EMAIL_TRIGGER_TYPE, True),
        # `trigger.config["account"]` — operator-authored, and a MAILBOX
        # ADDRESS. Authorship is not the question here: releasing it names
        # the correspondent the platform reads for. Withheld under the same
        # rule as `memory_observed.user_id`; see G-Trace-Subject-Identity.
        "account": FieldRule(Owner.CONFIG, _ID, False),
        "last_run_at": FieldRule(Owner.ENGINE, _TS_, True),
        "threshold_seconds": FieldRule(Owner.CONFIG, _AMOUNT, True),
    },
    "alert_high_error_rate": {
        "rate": FieldRule(Owner.ENGINE, _AMOUNT, True),
        "threshold": FieldRule(Owner.CONFIG, _AMOUNT, True),
        "failed": FieldRule(Owner.ENGINE, _COUNT, True),
        "total_terminal": FieldRule(Owner.ENGINE, _COUNT, True),
        # `_AMOUNT`, not `_COUNT`: `MonitoringConfig.*_window_seconds` are
        # FLOATS, so `_COUNT` (int-only, bools excluded) redacted every live
        # value. v12 shipped with that wrong and the golden did not catch it,
        # because the corpus case used an int literal. The corpus now uses
        # the type production actually stores.
        "window_seconds": FieldRule(Owner.CONFIG, _AMOUNT, True),
    },
    "alert_abandoned_pause": {
        "instance_id": FieldRule(Owner.ENGINE, _TOKEN, True),
        "workflow_id": FieldRule(Owner.CONFIG, _TOKEN, True),
        "paused_for_seconds": FieldRule(Owner.ENGINE, _AMOUNT, True),
        "threshold_seconds": FieldRule(Owner.CONFIG, _AMOUNT, True),
    },
    "alert_high_queue_depth": {
        "depth": FieldRule(Owner.ENGINE, _COUNT, True),
        "threshold": FieldRule(Owner.CONFIG, _COUNT, True),
    },
    "alert_high_token_burn": {
        "tokens": FieldRule(Owner.ENGINE, _COUNT, True),
        "cost_usd": FieldRule(Owner.ENGINE, _AMOUNT, True),
        "threshold_tokens": FieldRule(Owner.CONFIG, _COUNT, True),
        "window_seconds": FieldRule(Owner.CONFIG, _AMOUNT, True),
    },
    # --- LEARNED MEMORY, read side. The write side is `memory_observed`.
    "memory_recalled": {
        # The namespace key: a mailbox address in production. Same rule, same
        # follow-up, as on the write side.
        "user_id": FieldRule(Owner.BUSINESS, _ID, False),
        "subject": FieldRule(Owner.ENGINE, _SUBJECT, True),
        # `recalled.query` — built from the correspondent's own message.
        "query": FieldRule(Owner.BUSINESS, _TOKEN, False),
        "context_hash": FieldRule(Owner.ENGINE, _TOKEN, True),
        "edges": FieldRule(Owner.ENGINE, _COUNT, True),
        "episodes": FieldRule(Owner.ENGINE, _COUNT, True),
        "token_budget": FieldRule(Owner.CONFIG, _COUNT, True),
        "injected": FieldRule(Owner.ENGINE, _BOOL, True),
        "uses_recorded": FieldRule(Owner.ENGINE, _USES_RECORDED, True),
        # Engine-measured wall clock. Disclosed because an operator cannot
        # reason about a 3-minute run without them, and because a number
        # nobody records is a number nobody checks: `triage` spent 92% of
        # its time in outcome recording for two months with no field saying
        # so (measured 2026-09-19).
        "recall_seconds": FieldRule(Owner.ENGINE, _AMOUNT, True),
        "outcomes_seconds": FieldRule(Owner.ENGINE, _AMOUNT, True),
    },
    # --- THE GOVERNANCE SURFACE (G-Trace-Chokepoint-Rest, 2026-09-19).
    # These are written by the API and auth routers, which now go through
    # the same chokepoint as the engine. Every one of them is
    # INSTANCE-LESS, and an instance-less entry cannot be vaulted — so for
    # these actions "withheld" does not mean "recoverable with a grant", it
    # means GONE. Registering them is therefore not a widening for
    # convenience; it is the only way routing them does not destroy the
    # operator trail it exists to keep.
    #
    # Audience: instance-less audit is Administrator-only (THREAT_MODEL §5),
    # so the reader is an operator, not a tenant.
    "auth_login": {
        # The account that logged in. `_ID` deliberately — it admits `@`,
        # which is what it exists for (operator identity).
        "email": FieldRule(Owner.CONFIG, _ID, True),
        "source_ip": FieldRule(Owner.ENGINE, _IP, True),
    },
    "auth_login_failed": {
        # CALLER-SUPPLIED: whatever was typed at the login form, so this one
        # is third-party by provenance. Disclosed anyway, shape-bounded,
        # because "who is being targeted, from where" IS the security trail
        # this entry exists to be — and the login path is already
        # enumeration-resistant, so the entry reveals nothing about whether
        # the account exists that the attacker does not already know.
        "email": FieldRule(Owner.BUSINESS, _ID, True),
        "source_ip": FieldRule(Owner.ENGINE, _IP, True),
        "cause": FieldRule(Owner.ENGINE, _LOGIN_FAILURE_CAUSE, True),
    },
    "user_created": {
        "user_id": FieldRule(Owner.ENGINE, _ID, True),
        "subject": FieldRule(Owner.ENGINE, _SUBJECT, True),
        "email": FieldRule(Owner.CONFIG, _ID, True),
        "origin": FieldRule(Owner.ENGINE, _USER_ORIGIN, True),
    },
    "user_updated": {
        "user_id": FieldRule(Owner.ENGINE, _ID, True),
        "subject": FieldRule(Owner.ENGINE, _SUBJECT, True),
        # Field NAMES that changed, never their values.
        "changed": FieldRule(Owner.ENGINE, Seq(_TOKEN), True),
        "sessions_revoked": FieldRule(Owner.ENGINE, _BOOL, True),
        "raw_grants_revoked": FieldRule(Owner.ENGINE, _COUNT, True),
    },
    "directory_resolved": {
        # Counts and outcomes only. The names the request returned are in
        # the RESPONSE and nowhere else — writing them here would put the
        # identities back into the store the subject reference exists to
        # keep them out of.
        "requested": FieldRule(Owner.ENGINE, _COUNT, True),
        "resolved": FieldRule(Owner.ENGINE, _COUNT, True),
        "not_found": FieldRule(Owner.ENGINE, _COUNT, True),
        "not_resolvable": FieldRule(Owner.ENGINE, _COUNT, True),
    },
    "org_created": {
        "org_id": FieldRule(Owner.ENGINE, _ID, True),
        "name": FieldRule(Owner.CONFIG, _LABEL, True),
    },
    "org_renamed": {
        "org_id": FieldRule(Owner.ENGINE, _ID, True),
        # The old name exists NOWHERE else once the rename lands.
        "from": FieldRule(Owner.CONFIG, _LABEL, True),
        "to": FieldRule(Owner.CONFIG, _LABEL, True),
    },
    "workflow_deleted": {
        "workflow_id": FieldRule(Owner.CONFIG, _TOKEN, True),
        "deleted_instances": FieldRule(Owner.ENGINE, _COUNT, True),
        "deleted_steps": FieldRule(Owner.ENGINE, _COUNT, True),
        "org_bypass": FieldRule(Owner.ENGINE, _BOOL, True),
    },
    "instance_deleted": {
        "workflow_id": FieldRule(Owner.CONFIG, _TOKEN, True),
        "deleted_steps": FieldRule(Owner.ENGINE, _COUNT, True),
        "org_bypass": FieldRule(Owner.ENGINE, _BOOL, True),
    },
    "memory_observe_failed": {
        # The observation's INDEX in the spec list, not its text.
        "observation": FieldRule(Owner.ENGINE, _COUNT, True),
        "error": FieldRule(Owner.BUSINESS, _TOKEN, False),
    },
}

#: Actions whose detail is built at ONE site and shared by a second action
#: name. The alias reuses the rules rather than copying them (M3: two tables
#: that must agree are one table waiting to disagree).
_RULE_ALIASES = {"budget_escalated": "budget_exceeded"}
for _alias, _source in _RULE_ALIASES.items():
    AUDIT_FIELD_RULES[_alias] = AUDIT_FIELD_RULES[_source]


def _project_by_rules(detail: dict[str, Any], rules: dict[str, FieldRule]) -> dict[str, Any]:
    """Project one detail under its action's rules. Default-deny: a field
    with no rule is withheld, exactly as an undeclared field is under the
    flat schema."""
    out: dict[str, Any] = {}
    withheld = False
    for key, value in detail.items():
        rule = rules.get(key)
        if rule is None or not rule.disclose:
            withheld = True
            continue
        projected = _project(rule.node, value)
        if projected != value:
            # Declared and disclosable, but the value failed its validator.
            out[key] = projected
            continue
        out[key] = value
    if withheld:
        out[_WITHHELD] = True
    return out


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
    # Per-(action, field) rules first. Only actions listed in the registry
    # take this path; everything else keeps the flat default-deny schema, so
    # adding an action can release more but never leak by omission.
    rules = AUDIT_FIELD_RULES.get(action or "")
    if rules is not None:
        return _project_by_rules(detail, rules)
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
