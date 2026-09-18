"""Boundary PROPERTIES of the trace projection — not example-based tests.

Written after the third external code review (2026-08-08) failed. Its central
finding was not any single bug but a method problem: every existing test asserts
a *known field name* (`does {"category": SECRET} redact?`), so each round of
review found the same classes of bypass under names nobody had enumerated yet.

These tests assert the boundary instead:

    P1  a below-grant projection contains NO sentinel, wherever it appeared
    P2  a projection marker cannot be supplied as INPUT
    P3  raw requires the vault, or the flip writes plaintext at rest
    P5  one authoritative projector version

They are generative over structure — depth, container type, key choice — rather
than over a hand-listed set of fields. A new field, a new nesting shape, or a
new container type is covered without editing the test.

The xfail markers are GONE as of the P1 re-primitive — these now assert, not
document. Any regression is a build failure, and a new field or nesting shape is
covered without editing the test.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from workflow_platform.trace_projection import (
    _REDACTED_FIELD,
    PROJECTOR_VERSION,
    has_redaction_marker,
    is_generated_marker,
    project_audit_detail_at_rest,
    redact_tool_data,
    safe_tool_call,
    safe_trigger_payload,
)
from workflow_platform.trace_vault import output_has_raw

# A space + `@` so NO leaf validator (token, id, amount, …) can accept it on
# its own. Survival then means one thing only — an undeclared container was
# recursed and laundered it — which is exactly the P1 property. A token-SHAPED
# value surviving a token path is a SEPARATE, accepted concern, pinned in
# test_p3_token_path_residual_is_accepted_and_bounded, not here.
SENTINEL = "SENTINEL raw victim@example.com 9f3c2a"

# Keys drawn from three classes, so the property holds regardless of which the
# generator picks: registered scalar, registered container, and unregistered.
_REGISTERED = ("model", "id", "state", "actor_id", "memory_hash")
_UNREGISTERED = ("summary", "unregistered_map", "notes", "payload")


def _dumps(obj: Any) -> str:
    """Whole projected structure as text — catches a value hiding in a KEY."""
    import json

    return json.dumps(obj, default=str)


def _containers(leaf: Any) -> list[Any]:
    """The same leaf wrapped every structural way the projector may meet it."""
    out: list[Any] = [leaf, [leaf], {"k": leaf}, [[leaf]], [{"k": leaf}], {"k": [leaf]}]
    return out


def _nested(depth: int, keys: tuple[str, ...], leaf: Any) -> Any:
    node: Any = leaf
    for k in itertools.islice(itertools.cycle(keys), depth):
        node = {k: node}
    return node


def _all_strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _all_strings(v)] + [
            s for k in obj for s in _all_strings(k)
        ]
    if isinstance(obj, list):
        return [s for v in obj for s in _all_strings(v)]
    return []


def _leaks(projected: Any) -> bool:
    return any(SENTINEL in s for s in _all_strings(projected))


# --- P1: no sentinel survives, at any depth, under any key ------------------


@pytest.mark.parametrize("depth", [1, 2, 3, 4])
@pytest.mark.parametrize("keys", [_REGISTERED, _UNREGISTERED, _REGISTERED + _UNREGISTERED])
def test_p1_sentinel_never_survives_nesting(depth: int, keys: tuple[str, ...]) -> None:
    """A raw value must not survive because some ANCESTOR OR DESCENDANT key
    happens to be registered. Safety is a property of the path, not of any key
    appearing somewhere along it."""
    for shaped in _containers(SENTINEL):
        payload = _nested(depth, keys, shaped)
        assert not _leaks(redact_tool_data(payload, admin=False, kind="step_output")), (
            f"leaked at depth={depth} keys={keys[:2]}… shape={type(shaped).__name__}"
        )


def test_p1_token_path_rejects_email_and_prose() -> None:
    """A token-declared path must reject the two channels that carry free
    content: an `@` (email) and whitespace (prose). This is the F1b fix — the
    old single permissive regex admitted `alice@example.com` on `model`.

    It deliberately does NOT assert that a token path rejects a structured id
    like `INV-2026-000184` or a dashed uuid: those ARE token-shaped and a token
    path accepts them by design. Whether such a value is raw is a PROVENANCE
    question (§1.4a), pinned as the accepted residual below — not a shape one."""
    for value in ("alice@example.com", "has a space", "subject: hi", f"{SENTINEL}"):
        out = redact_tool_data({"model": value}, admin=False, kind="step_output")
        assert out["model"] != value, f"`model` admitted free content {value!r} (F1b)"


def test_p1_trigger_routing_fields_are_withheld() -> None:
    """No routing id survives a projected trigger payload.

    SUPERSEDED CONTRACT, kept visible rather than deleted. This property used
    to assert the opposite for the id-shaped case — that `message_id:
    "18f3a2b9c4d"` was *retained* by design, §1.3 justifying it as operational
    routing metadata. Round 4 (GR4-R4) showed why that could not hold: these
    ids come from OUTSIDE (a webhook body, a mail provider), and a token-shaped
    secret is indistinguishable from a routing token by shape, so "retain if it
    looks like an id" retains `AKIAIOSFODNN7EXAMPLE` too. The id is now
    withheld below grant and recoverable from the vault by a grant holder.

    Verified before changing it: nothing in the backend or frontend reads a
    routing id back out of a PROJECTED payload."""
    out = safe_trigger_payload(
        {"id": f"{SENTINEL} with spaces", "thread_id": "subject: " + SENTINEL}
    )
    assert not _leaks(out), f"trigger routing kept prose: {out}"
    # …and the id-shaped case, which this property previously ALLOWED.
    assert safe_trigger_payload({"message_id": "18f3a2b9c4d"})["message_id"] != "18f3a2b9c4d"


def test_p1_tool_parameter_names_are_not_content() -> None:
    """Parameter KEYS are attacker-influenced when a model chooses tool args, so
    exporting them wholesale persists content."""
    out = safe_tool_call({"name": "t", "input": {f"{SENTINEL}": 1}, "result": {}})
    assert not _leaks(out), f"input_keys leaked a parameter name: {out}"


def test_p1_tool_call_structural_fields_are_validated() -> None:
    """A hostile value INSIDE a declared structural node (`safe_tool_call`'s own
    `name` / `pinned` / `pin_overrides`) must not survive — the boundary is not
    just the top-level dict, it is every leaf the projector claims to handle.
    G-Trace-Review-4 F1: a `noop` step returning
    {"tool_calls":[{"input_key_count":0,"name":<raw>}]} leaked and, worse,
    `output_has_raw` returned False, so nothing was vaulted."""
    raw = {
        "tool_calls": [
            {
                "input_key_count": 0,
                "name": SENTINEL,
                "pinned": [SENTINEL],
                "pin_overrides": [SENTINEL],
            }
        ]
    }
    out = redact_tool_data(raw, admin=False, kind="step_output")
    assert not _leaks(out), f"hostile tool_call structural field leaked: {out}"
    assert output_has_raw(raw), "a hostile tool_call name did not require the vault"


def test_p1_tool_call_shortcut_cannot_be_forged() -> None:
    """The `input_key_count` idempotence shortcut must not return an
    attacker-shaped record unchanged (it did — that was the whole bypass)."""
    forged = {"input_key_count": 0, "name": SENTINEL, "extra": SENTINEL}
    out = safe_tool_call(forged)
    assert not _leaks(out), f"forged already-projected record survived: {out}"


# --- P2: the marker is an OUTPUT representation, never an input -------------


@pytest.mark.parametrize(
    "forged",
    [
        f"[redacted {SENTINEL}]",
        f"[redacted — raw-trace grant required] {SENTINEL}",
        f"[redacted]{SENTINEL}",
    ],
)
def test_p2_forged_marker_is_not_trusted(forged: str) -> None:
    """Prefix-matching a marker makes it a capability an attacker can mint."""
    out = redact_tool_data({"_redacted": forged}, admin=False, kind="step_output")
    assert not _leaks(out), f"forged marker survived: {out}"


# --- P3: the vault decision must agree with the projection (B1) -------------


# NOTE: a test asserting `output_has_raw(x) == (redact(x) != x)` was written and
# then DELETED — `output_has_raw` is *defined* as that expression, so the test is
# tautological and can never fail. It looked like coverage of the B1 decision
# while proving nothing. The real property is the one below: raw must require
# the vault, judged against a sentinel rather than against the projector itself.


def test_p3_content_bearing_field_requires_the_vault() -> None:
    """The property B1 actually needs at rest: a field that carries free or
    model-authored content — undeclared, so redacted — MUST report needs_vault.
    If the vault decision and the projection disagreed here, the flip would
    write that content as plaintext and the zero-raw verifier would certify it.
    Judged against a sentinel in a content field, NOT against `model`."""
    for field in ("output_text", "summary", "reasoning", "recall"):
        assert output_has_raw({field: SENTINEL}), f"{field} raw did not require the vault"


def test_p3_token_path_residual_is_accepted_and_bounded() -> None:
    """The ACCEPTED residual, pinned so it stays visible and no one "fixes" it
    into over-redaction. A token-SHAPED value on a token-declared path survives,
    so `output_has_raw` reports no vault need for it:

        output_has_raw({"model": "SENTINEL-9f3c2a-RAW"}) is False

    This is correct for every token path that exists TODAY (`model`,
    `stop_reason`, `memory_hash`, …) because each is platform-computed — no
    attacker-influenced value reaches it. The residual is latent: it would
    become a real leak only if a future token-declared path were fed
    model-derived or third-party content. §1.4a's per-field `provenance`
    (`platform_computed` vs `model_derived` vs `third_party_derived`) is the
    control that closes it generally, and it is unbuilt. Until then, adding a
    token path fed by non-platform content is the thing a reviewer must catch."""
    token_shaped = "abc123-def456-ghi789"  # dashes ok, no @/space → valid token
    assert output_has_raw({"model": token_shaped}) is False


# --- P5: one authoritative projector version --------------------------------


def test_p5_single_authoritative_projector_version() -> None:
    """Operational rows and vault rows must identify the SAME projector, or the
    §4.3 agreement predicate compares against a version that never wrote it.

    Asserted against the value a vault row ACTUALLY writes — `RawTrace`'s default
    `projector_version` — not against a re-exported symbol, so the test cannot
    pass while the persisted default silently diverges (the F5 defect: the model
    hardcoded `"trace-projector@1"` while the projector said `"2"`)."""
    from workflow_platform.persistence.models import RawTrace

    written = RawTrace.model_fields["projector_version"].default
    assert written == PROJECTOR_VERSION, (
        f"vault writes projector {written!r} but the projector is {PROJECTOR_VERSION!r}"
    )


# --- Class-level properties added after the GR4-r2 external review -----------
# The review found the same classes recurring in unfixed spots (keys-are-content,
# marker-as-input, non-total validators). These assert the CLASS generatively.


_HOSTILE = [
    SENTINEL,
    [],
    {},
    0,
    1,
    True,
    None,
    ["x"],
    {"k": "v"},
    "a b c",
    "x@y.z",
    b"bytes",
    3.14,
    (1, 2),
    {SENTINEL: 1},
    [SENTINEL],
]


@pytest.mark.parametrize("kind", ["step_output", "context", "instance", "step_row", "audit_detail"])
def test_projector_is_total_over_hostile_input(kind: str) -> None:
    """The projector must NEVER raise, for ANY value at ANY declared field or as
    a dict KEY (GR4-r2 F6). A hostile deterministic output must not 500 a read
    or fail safe-only persistence."""
    for hv in _HOSTILE:
        for probe in (
            {"_redacted": hv},
            {"usage": hv},
            {"model": hv},
            {"output": hv},
            hv,
            {hv if isinstance(hv, str) else "k": hv},
        ):
            try:
                redact_tool_data(probe, admin=False, kind=kind)
            except Exception as e:
                raise AssertionError(f"projector raised on {probe!r} (kind={kind}): {e!r}") from e


def test_dict_keys_are_never_a_leak_channel() -> None:
    """A RAW dict key (email/prose) must not survive at any path — declared,
    wildcard, or undeclared (GR4-r2 F1). Only field-name tokens survive."""
    for container in (
        {"usage": {SENTINEL: 1}},
        {SENTINEL: "x"},
        {"context": {"steps": {SENTINEL: {"model": "m"}}}},
        {"output": {SENTINEL: 1}},
    ):
        for kind in ("step_output", "context", "instance"):
            assert not _leaks(redact_tool_data(container, admin=False, kind=kind)), (
                f"raw key leaked: {container} (kind={kind})"
            )


def test_marker_predicate_is_total_and_exact() -> None:
    """One marker predicate, total over non-strings and exact (never a prefix)."""
    assert is_generated_marker([]) is False  # no raise
    assert is_generated_marker(None) is False
    assert is_generated_marker(f"[redacted {SENTINEL}]") is False  # forged, not exact

    assert is_generated_marker(_REDACTED_FIELD) is True


def test_tool_call_list_fields_never_decompose_a_string() -> None:
    """`pinned`/`pin_overrides` given a STRING must not become per-character
    tokens (GR4-r2 F1).

    R5 §3.4: these now collapse to BOOLEANS — the parameter names come from
    definition config, so they are the tool-name origin problem one level down
    and shape cannot vet them. The property still holds and is strictly
    stronger: no element of the string survives in any form."""
    for field in ("pinned", "pin_overrides"):
        out = safe_tool_call({"name": "t", "input": {}, "result": {}, field: SENTINEL})
        assert not _leaks(out), f"{field} decomposed a string: {out}"
        assert out[f"{field}_present"] is False, f"{field} raw string counted as present: {out}"
        assert field not in out, f"{field} names were emitted: {out}"


def test_audit_denylist_covers_correspondent_query() -> None:
    """A memory_recalled detail's raw `query` must not survive at rest."""
    d = {"query": SENTINEL, "edges": 1, "context_hash": "abc"}
    assert not _leaks(project_audit_detail_at_rest("memory_recalled", d))


# --- Round-4 containment (GR4-R4) -------------------------------------------
#
# The round-4 reviewer executed §3's claim that "all retained token paths are
# platform-computed" and showed it FALSE. These three properties pin the
# containment. They are SOURCE-aware, not shape-aware: the fix is to stop
# emitting values whose source is external/model-chosen, NOT to tighten the
# regex (which cannot separate a token-shaped secret from a safe token).

# Token-SHAPED secrets. Every one passes _short_token — that is the point:
# shape cannot save us here, so the key must be rejected on SOURCE.
TOKEN_SHAPED_SECRETS = (
    "AKIAIOSFODNN7EXAMPLE",  # AWS access key id
    "123456789",  # an SSN, unformatted
    "sk_live_51H8xQ2",  # a payment secret
)


@pytest.mark.parametrize("secret", TOKEN_SHAPED_SECRETS)
def test_token_shaped_dict_key_is_not_a_leak_channel(secret: str) -> None:
    """A TOKEN-SHAPED dict key must not survive on a data-keyed container.

    This is the case the round-3 key fix MISSED and the round-4 sidecar wrongly
    claimed was covered: `_safe_key` admits any field-name-shaped token, so a
    secret that happens to be token-shaped rode through as a KEY. Shape cannot
    tell them apart — a data-keyed wildcard must therefore drop unknown keys.
    """
    for container, kind in (
        ({"usage": {secret: 1}}, "step_output"),
        ({"output": {"usage": {secret: 1}}}, "step_row"),
    ):
        out = redact_tool_data(container, admin=False, kind=kind)
        assert secret not in _dumps(out), f"token-shaped KEY survived: {out} (kind={kind})"


@pytest.mark.parametrize("secret", TOKEN_SHAPED_SECRETS)
def test_externally_supplied_routing_ids_are_grant_gated(secret: str) -> None:
    """Routing ids come from OUTSIDE (a webhook body, a provider) — they are
    not platform-computed, so a token-shaped one must not be emitted below
    grant. It stays recoverable to a grant holder via the vault."""
    for key in ("id", "message_id", "thread_id"):
        out = safe_trigger_payload({"type": "webhook", key: secret})
        assert secret not in _dumps(out), f"external routing id survived at {key}: {out}"


def test_unresolved_tool_name_is_not_emitted() -> None:
    """A tool NAME is model-chosen. It may only be shown when it resolves to a
    known catalog entry; an unresolved name is the model's own string and must
    not be echoed into the trace (the reviewer's 'resolved catalog entry rather
    than the original requested string')."""
    known = frozenset({"file_read", "pdf_extract"})

    ok = safe_tool_call({"name": "file_read", "input": {"a": 1}}, known_tools=known)
    assert ok["name"] == "file_read", "a RESOLVED catalog name must still be shown"

    for hostile in ("exfiltrate_sk_live_51H8xQ2", "AKIAIOSFODNN7EXAMPLE", "not_a_real_tool"):
        out = safe_tool_call({"name": hostile, "input": {"a": 1}}, known_tools=known)
        assert hostile not in _dumps(out), f"unresolved model-chosen name survived: {out}"

    # Fail CLOSED: with an EMPTY catalog, no name is emitted — not even a real
    # tool's. Passed explicitly: the process-wide catalog
    # (`set_resolvable_tools`) is global mutable state that any ToolCatalog
    # construction in the same process widens, so asserting the default here
    # would be order-dependent. That order-dependence is itself a design
    # question raised for review, not something this test should paper over.
    blind = safe_tool_call({"name": "file_read", "input": {"a": 1}}, known_tools=frozenset())
    assert "file_read" not in _dumps(blind), f"name emitted with empty catalog: {blind}"


# --- Round-5 remediation (GR4-R5) -------------------------------------------


def test_a_previous_projector_version_degrades_and_does_not_read_corrupt() -> None:
    """A row written by an OLDER projector must degrade, never read as
    tampering (criterion 17; R5 F1).

    Round 5 changed WHAT projection emits while still stamping "2", so an
    untouched round-4 record was re-projected under round-5 rules, disagreed,
    and surfaced as an integrity failure. The §4.3 predicate already had the
    right answer — it was simply never told the version had moved.
    """
    from workflow_platform.trace_rehydrate import verify_projection_agreement

    raw = {"output_text": "secret", "model": "m"}
    stored_under_the_old_projector = {"output_text": "[redacted — raw-trace grant required]"}

    assert verify_projection_agreement(raw, stored_under_the_old_projector, "2") == "unsupported", (
        "an older projector version must degrade, not report tampering"
    )
    # …and the CURRENT version still verifies normally.
    assert (
        verify_projection_agreement(
            raw, redact_tool_data(raw, admin=False, kind="step_output"), PROJECTOR_VERSION
        )
        == "ok"
    )


def test_the_withheld_flag_cannot_carry_raw_and_is_seen_as_a_marker() -> None:
    """The withheld signal is a BOOLEAN, so a forged one smuggles nothing, and
    completeness checks must recognise it (R5 F3a/F3b).

    Round 5 used a COUNT: `{"_withheld_keys": 123456789}` supplied on raw
    input was retained verbatim and `output_has_raw()` called it clean — the
    reserved field became a raw channel, the very marker-as-input class earlier
    rounds closed."""
    forged = redact_tool_data({"_withheld_keys": 123456789}, admin=False, kind="step_output")
    assert "123456789" not in _dumps(forged), f"reserved field carried a raw value: {forged}"
    assert not output_has_raw(forged)

    # a genuinely withheld object is visible to the completeness predicate
    withheld = redact_tool_data({"output_text": "secret"}, admin=False, kind="step_output")
    assert withheld.get("_withheld_keys") is True
    assert has_redaction_marker(withheld), "a withheld object must read as incomplete"


def test_every_dropped_entry_raises_the_withheld_flag() -> None:
    """R5 F3c: a key with spaces was dropped SILENTLY — dropped by a different
    branch than the undeclared-key one, and that branch never set the flag, so
    the record showed no sign anything had been withheld at all."""
    out = redact_tool_data(
        {"a key with spaces": "x", "model": "m"}, admin=False, kind="step_output"
    )
    assert out.get("_withheld_keys") is True, f"silent drop left no signal: {out}"
    assert out["model"] == "m"


@pytest.mark.parametrize("bad_name", [[], {"a": 1}, 7, None])
def test_tool_name_resolution_is_total_over_hostile_input(bad_name: Any) -> None:
    """R5 F6: a list/dict `name` is unhashable, so the catalog membership test
    raised TypeError — the projector must be total at every input."""
    out = safe_tool_call({"name": bad_name, "input": {}}, known_tools=frozenset({"file_read"}))
    assert out["name"] == _REDACTED_FIELD


def test_engine_usage_counters_survive_the_closed_schema() -> None:
    """R5 F5: `AgentUsage` really produces `iterations` and `tool_calls`; the
    closed usage schema omitted them, so live engine counters were dropped and
    explain's `usage.get("iterations")` went None."""
    out = redact_tool_data(
        {"usage": {"input_tokens": 10, "output_tokens": 2, "iterations": 3, "tool_calls": 1}},
        admin=False,
        kind="step_output",
    )
    assert out["usage"] == {
        "input_tokens": 10,
        "output_tokens": 2,
        "iterations": 3,
        "tool_calls": 1,
    }


# --- Round-6 remediation (GR4-R6) -------------------------------------------


@pytest.mark.parametrize("bad", ["sekret", 123456789, {"a": 1}, [1], 0, False])
def test_an_invalid_reserved_field_is_dropped_AND_signalled(bad: Any) -> None:
    """R6 F2: a reserved field holding anything but `True` is raw input wearing
    our field name. Round 6 dropped it SILENTLY, so the object read COMPLETE —
    dropping an entry is precisely what the flag exists to report."""
    out = redact_tool_data({"_withheld_keys": bad}, admin=False, kind="step_output")
    assert str(bad) not in _dumps(out) or bad is False
    assert out.get("_withheld_keys") is True, f"dropped without a signal: {out}"
    assert has_redaction_marker(out)


def test_the_round5_withheld_representation_is_still_recognised() -> None:
    """R6 F2: a stored ROUND-5 projection carries `_withheld_key_count: n`. A
    build that does not recognise it calls an incomplete object complete and
    skips restoring it. Reading a historical shape is not emitting it."""
    from workflow_platform.trace_rehydrate import _output_projected

    legacy = {"_withheld_key_count": 1}
    assert has_redaction_marker(legacy), "historical marker unrecognised by completeness"
    assert _output_projected(legacy), "historical marker unrecognised by compatibility"

    # re-projecting carries the FACT forward in the current representation,
    # never the old count (which was itself a raw channel)
    out = redact_tool_data(legacy, admin=False, kind="step_output")
    assert out == {"_withheld_keys": True}


def test_completeness_and_compatibility_agree_on_withholding() -> None:
    """R6 F2: the two detectors disagreed — one predicate now serves both."""
    from workflow_platform.trace_rehydrate import _output_projected

    for obj in ({"_withheld_keys": True}, {"_withheld_key_count": 3}):
        assert has_redaction_marker(obj) == _output_projected(obj) is True


def test_projection_version_stamps_have_one_definition() -> None:
    """R6 F3: `PROJECTION_SCHEMA_VERSION` was declared independently in the
    projector and in persistence.models, and they drifted — the projected shape
    changed while the persisted stamp stayed at 1."""
    from workflow_platform import trace_projection as proj
    from workflow_platform.persistence import models

    assert models.PROJECTION_SCHEMA_VERSION is proj.PROJECTION_SCHEMA_VERSION
    assert models.PROJECTOR_VERSION is proj.PROJECTOR_VERSION
    # the shape changed in R5/R6, so the schema stamp must have moved off 1
    assert proj.PROJECTION_SCHEMA_VERSION >= 2


# --- Ownership typing (R6 §4.4) ---------------------------------------------


def test_every_declared_field_has_a_declared_owner() -> None:
    """TOTALITY. Every field declared at the root of an ownership-typed schema
    must have an owner — there is no default, for the same reason there is no
    default asset kind: a default is a guess, and guessing about the PRODUCER
    is the defect this taxonomy exists to remove."""
    from workflow_platform.trace_projection import _OWNERSHIP, SCHEMAS

    for kind, owners in _OWNERSHIP.items():
        schema = SCHEMAS[kind]
        declared = set(getattr(schema, "children", {}))
        missing = declared - set(owners)
        assert not missing, f"{kind}: fields with no declared owner: {sorted(missing)}"
        stray = set(owners) - declared
        assert not stray, f"{kind}: ownership declared for absent fields: {sorted(stray)}"


def test_model_derived_business_fields_are_withheld() -> None:
    """R6 §4.4: `faithfulness_score`, `category_score`, `relevance_score`,
    `needs_tests` and `concern_count` are PARSED FROM MODEL OUTPUT. Numeric or
    boolean shape does not make them platform measurements, so they are
    withheld until a release rule says who may see them."""
    out = redact_tool_data(
        {
            "model": "haiku",
            "usage": {"input_tokens": 9},
            "faithfulness_score": 5,
            "category_score": 4,
            "relevance_score": 3,
            "needs_tests": True,
            "concern_count": 2,
        },
        admin=False,
        kind="step_output",
    )
    for business in (
        "faithfulness_score",
        "category_score",
        "relevance_score",
        "needs_tests",
        "concern_count",
    ):
        assert business not in out, f"a model-derived claim was published: {business}"
    assert out["_withheld_keys"] is True, "withholding a business field must be signalled"
    # engine-owned metadata is unaffected
    assert out["model"] == "haiku" and out["usage"] == {"input_tokens": 9}


def test_a_function_cannot_produce_engine_metadata() -> None:
    """R6 §4.4, the reviewer's reproduction: the registered `noop` returns its
    CONFIG unchanged into the same schema engine output uses, so a step could
    emit `model` / `memory_hash` / `usage.input_tokens` and have them survive
    verbatim with `output_has_raw()` reporting clean.

    Resolved at the boundary the value enters — a function's dict simply cannot
    carry engine-owned keys — so nothing downstream must re-derive the
    producer, and projection stays a pure function of the record."""
    from workflow_platform.engine.executor import WorkflowEngine
    from workflow_platform.workflow import DeterministicStep

    step = DeterministicStep(id="s", type="deterministic", function="noop", config={})
    kept = WorkflowEngine._strip_engine_owned(
        {
            "model": "attacker-supplied",
            "memory_hash": "sha256:deadbeefdeadbeef",
            "usage": {"input_tokens": 999999},
            "copied_to": ["/out/a.xml"],
        },
        step,
    )
    assert "model" not in kept and "memory_hash" not in kept and "usage" not in kept
    assert kept == {"copied_to": ["/out/a.xml"]}, "the function's OWN output must survive"


# --- Round-7 return (GR4-R7) ------------------------------------------------


@pytest.mark.parametrize(
    ("obj", "kind", "where"),
    [
        ({"steps": {"eval": {"faithfulness_score": 5, "model": "m"}}}, "context", "context.steps"),
        ({"output": {"faithfulness_score": 5, "model": "m"}}, "step_row", "step_row.output"),
        ({"faithfulness_score": 5, "model": "m"}, "step_output", "step_output root"),
    ],
)
def test_business_withholding_composes_through_nesting(obj: Any, kind: str, where: str) -> None:
    """R7 P1: ownership must hold at EVERY node, not just the outermost.

    Keyed by asset kind at the entry point, the rule fired on the root alone —
    so one run withheld scores from its stored step output and RETAINED them in
    its own instance context. Ownership now lives on the schema NODE, which is
    the same object wherever a step output appears."""
    out = redact_tool_data(obj, admin=False, kind=kind)
    assert "faithfulness_score" not in _dumps(out), f"score survived at {where}: {out}"
    assert "m" in _dumps(out), "engine-owned metadata should be unaffected"


def test_a_function_cannot_forge_projection_metadata_or_an_unearned_parse_ok() -> None:
    """R7 P1: the producer check covered ENGINE only, so a `noop` returning
    `projector_version` / `projection_schema_version` / `parse_ok` had them
    stored verbatim with output_has_raw() clean.

    A field NAME cannot establish its producer: `parse_ok` is released, but
    only for the parsers that actually compute it."""
    from workflow_platform.engine.executor import WorkflowEngine
    from workflow_platform.workflow import DeterministicStep

    forged = {
        "parse_ok": False,
        "projector_version": "supplied_value",
        "projection_schema_version": 12345,
        "copied_to": ["/out/a"],
    }
    noop = WorkflowEngine._strip_engine_owned(
        dict(forged), DeterministicStep(id="s", type="deterministic", function="noop", config={})
    )
    assert noop == {"copied_to": ["/out/a"]}, f"a non-parser forged fields: {noop}"

    parser = WorkflowEngine._strip_engine_owned(
        dict(forged),
        DeterministicStep(id="s", type="deterministic", function="record_evaluation", config={}),
    )
    assert parser["parse_ok"] is False, "the AUTHORIZED parser may emit parse_ok"
    assert "projector_version" not in parser, "no function may write projection metadata"


def test_a_round6_projection_degrades_rather_than_reading_as_tampering() -> None:
    """R7 P1: round 7 withholds business fields round 6 emitted, and BOTH
    called themselves projector "3" — so a valid round-6 record re-projected
    under round 7 disagreed and read as TAMPERING. Second time this exact
    collision shipped; the version must move whenever output moves."""
    from workflow_platform.trace_rehydrate import verify_projection_agreement

    raw = {"faithfulness_score": 5, "output_text": "secret", "model": "m"}
    round6_stored = {"faithfulness_score": 5, "model": "m", "_withheld_keys": True}
    assert verify_projection_agreement(raw, round6_stored, "3") == "unsupported"
    # NOT a literal version: pinning "4" here duplicated the constant and broke
    # the moment the next real change bumped it to "5". The property is that a
    # SUPERSEDED version degrades; enforcing that the version MOVES when output
    # moves is the golden guard's job (test_projection_golden.py), and one
    # owner per rule is the point.
    assert PROJECTOR_VERSION != "3", "the round-6 version must have been superseded"
    assert (
        verify_projection_agreement(
            raw, redact_tool_data(raw, admin=False, kind="step_output"), PROJECTOR_VERSION
        )
        == "ok"
    )


@pytest.mark.parametrize("bad", ["example text", {"a": 1}, [1], -1, 0])
def test_an_invalid_legacy_withheld_value_still_signals(bad: Any) -> None:
    """R7 P2: `{"_withheld_key_count": "example text"}` projected to `{}` with
    no marker, so both completeness and compatibility called a record with
    content REMOVED complete. Invalid content under the legacy reserved key
    must raise the flag, exactly as under the current one."""
    from workflow_platform.trace_rehydrate import _output_projected

    out = redact_tool_data({"_withheld_key_count": bad}, admin=False, kind="step_output")
    assert str(bad) not in _dumps(out)
    assert out.get("_withheld_keys") is True, f"content removed with no signal: {out}"
    assert has_redaction_marker(out) and _output_projected(out)


def test_parse_ok_producers_match_the_functions_that_compute_it() -> None:
    """The authorized-producer list must track reality, or it silently strips a
    legitimate field (or authorizes one that does not exist).

    Written from memory, the first version named `record_invoice` for what is
    really `record_invoice_extraction`, and the invoice pipeline lost its
    parse_ok. This derives the truth from the source and compares."""
    import ast
    import pathlib

    from workflow_platform.engine.functions import default_function_registry
    from workflow_platform.trace_projection import _PARSE_OK_PRODUCERS

    src = pathlib.Path(
        workflow_platform_functions_path := str(
            pathlib.Path(__file__).resolve().parents[1]
            / "src/workflow_platform/engine/functions.py"
        )
    ).read_text()
    assert workflow_platform_functions_path  # path resolved
    tree = ast.parse(src)
    computes = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        and "parse_ok" in (ast.get_source_segment(src, n) or "")
    }
    registered = set(default_function_registry().names())
    truth = computes & registered

    assert truth == _PARSE_OK_PRODUCERS, (
        f"authorized-producer list has drifted.\n"
        f"  missing (compute parse_ok but not authorized): {sorted(truth - _PARSE_OK_PRODUCERS)}\n"
        f"  stale   (authorized but do not compute it):    {sorted(_PARSE_OK_PRODUCERS - truth)}"
    )


# --- Class detectors (docs/REVIEW_FINDINGS_LEDGER.md §3) ---------------------
#
# A class that has returned TWICE gets a detector, not another fix. These are
# the generalised forms of defects the external reviewer found repeatedly, run
# over every field/predicate rather than the one instance that was reported.


#: Every field the projection GENERATES. M2's whole history is these being
#: read back from a record an attacker can shape: a prefix-matched marker, a
#: forged `_withheld_key_count` carrying an SSN, a supplied `projector_version`,
#: unbounded tool-summary numbers.
_GENERATED_FIELDS = (
    "_withheld_keys",
    "_withheld_key_count",
    "_redacted",
    "projector_version",
    "projection_schema_version",
    "input_key_count",
    "content_bytes",
)

#: Values that would be worth smuggling. None is token-shaped by accident.
_SMUGGLE = (
    "AKIAIOSFODNN7EXAMPLE",
    123456789,
    987654321,
    "sk_live_51H8xQ2",
    {"nested": "SYNTHETIC victim@example.com"},
    ["SYNTHETIC"],
)


@pytest.mark.parametrize("field", _GENERATED_FIELDS)
@pytest.mark.parametrize("kind", ["step_output", "step_row", "instance", "context"])
def test_M2_no_generated_field_can_carry_a_supplied_value(field: str, kind: str) -> None:
    """M2 DETECTOR: a field the projection generates must never echo a value
    supplied as input, at any asset kind.

    Generalised from four separate reported instances. The property is not
    "this one field is safe" but "no generated field is a channel"."""
    for value in _SMUGGLE:
        out = redact_tool_data({field: value}, admin=False, kind=kind)
        rendered = _dumps(out)
        for needle in (str(value), _dumps(value).strip('"')):
            if needle in ("True", "true"):
                continue  # the one legitimate value of a boolean flag
            assert needle not in rendered, (
                f"generated field {field!r} carried a supplied value at kind={kind}: "
                f"{value!r} -> {rendered}"
            )


def test_M3_predicates_answering_the_same_question_agree() -> None:
    """M3 DETECTOR: two functions that answer the same question must answer it
    identically, on every shape we can think of.

    Round 6 found completeness (`has_redaction_marker`) and compatibility
    (`_output_projected`) disagreeing — the same object was 'complete' to one
    and 'projected' to the other. This asserts agreement over the whole marker
    vocabulary rather than the two shapes that were reported."""
    from workflow_platform.trace_rehydrate import _output_projected

    shapes: list[Any] = [
        {"_withheld_keys": True},
        {"_withheld_key_count": 1},
        {"_withheld_key_count": 99},
        {"_redacted": _REDACTED_FIELD},
        {"model": _REDACTED_FIELD},
        {"nested": {"_withheld_keys": True}},
        {"list": [{"_withheld_keys": True}]},
    ]
    for shape in shapes:
        assert has_redaction_marker(shape) == _output_projected(shape), (
            f"completeness and compatibility disagree on {shape}: "
            f"has_redaction_marker={has_redaction_marker(shape)} "
            f"_output_projected={_output_projected(shape)}"
        )


def test_M3_version_constants_have_exactly_one_definition() -> None:
    """M3/M4 DETECTOR: a constant defined in two modules drifts. It has
    happened twice — the projector version (round 3) and the schema version
    (round 6) — so this sweeps for a THIRD rather than waiting for it."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "workflow_platform"
    watched = {"PROJECTOR_VERSION", "PROJECTION_SCHEMA_VERSION", "RAW_SCHEMA_VERSION"}
    definitions: dict[str, list[str]] = {name: [] for name in watched}
    for py in root.rglob("*.py"):
        tree = ast.parse(py.read_text())
        for node in tree.body:  # module level only
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id in watched:
                        definitions[tgt.id].append(py.name)
    for name, where in definitions.items():
        assert len(where) <= 1, (
            f"{name} is assigned in {len(where)} modules ({where}) — two definitions "
            f"of one constant drift; re-export instead"
        )


def test_function_reference_declaration_matches_the_source() -> None:
    """R9 P2 DRIFT GUARD, per FUNCTION.

    The previous version merged every `*_from` key and every default into two
    global tables, so `record_email_triage` — which READS `route_from` but
    whose default lives in the helper `_record_codified` — had that default
    materialised onto it, switching on routing the original never had. Keys
    and defaults are now declared per function, and this derives both from
    source and fails on drift in either direction."""
    import ast
    import pathlib as _pathlib

    from workflow_platform.scaffold import FUNCTION_REFERENCES

    src = _pathlib.Path(
        str(
            _pathlib.Path(__file__).resolve().parents[1]
            / "src/workflow_platform/engine/functions.py"
        )
    ).read_text()
    tree = ast.parse(src)
    truth: dict[str, dict[str, Any]] = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]:
        keys: set[str] = set()
        defaults: dict[str, str] = {}
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.endswith("_from")
            ):
                keys.add(node.args[0].value)
                if (
                    len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                    and node.args[1].value.startswith("steps.")
                ):
                    defaults[node.args[0].value] = node.args[1].value
        if keys:
            truth[fn.name] = {"fields": sorted(keys), "defaults": defaults}

    declared = {
        k: {"fields": sorted(v["fields"]), "defaults": v["defaults"]}
        for k, v in FUNCTION_REFERENCES.items()
    }
    assert declared == truth, (
        "per-function reference declaration has drifted from the source.\n"
        f"  missing functions: {sorted(set(truth) - set(declared))}\n"
        f"  stale functions:   {sorted(set(declared) - set(truth))}\n"
        f"  differing:         {sorted(k for k in set(truth) & set(declared) if truth[k] != declared[k])}"
    )


def test_M3_no_module_defines_the_same_function_twice() -> None:
    """M3 DETECTOR, extended to FUNCTIONS (ledger gap, found after R10).

    The duplicate-definition sweep watched CONSTANTS. It did not watch
    functions — and two live definitions of `_rewrite_context_path` coexisted
    with Python silently using the stale one, which is why a round-10 fix
    appeared to do nothing. A shadowed definition is invisible at the call
    site and passes every type check."""
    import ast
    import pathlib
    from collections import Counter

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "workflow_platform"
    problems: list[str] = []
    for py in sorted(root.rglob("*.py")):
        tree = ast.parse(py.read_text())
        defs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
        problems += [
            f"{py.relative_to(root)}: {name} defined {count} times at module level"
            for name, count in Counter(defs).items()
            if count > 1
        ]
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            methods = [
                n.name for n in cls.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            problems += [
                f"{py.relative_to(root)}: {cls.name}.{name} defined {count} times"
                for name, count in Counter(methods).items()
                if count > 1
            ]
    assert not problems, "a later definition silently shadows an earlier one:\n  " + "\n  ".join(
        problems
    )


def test_M3_the_scaffold_and_the_engine_share_one_placeholder_grammar() -> None:
    """M3 DETECTOR: two components parsing the same thing must accept the same
    language.

    R10: the rewriter's identifier grammar was NARROWER than the resolver's,
    so a valid step id kept a dangling reference. The placeholder grammar had
    the same exposure — a hand-copy of the engine's pattern, identical that
    day. It is imported now, and this pins that: one object, not two strings
    that happen to match."""
    from workflow_platform.engine.executor import _TEMPLATE_PLACEHOLDER
    from workflow_platform.scaffold import _ENGINE_PLACEHOLDER

    assert _ENGINE_PLACEHOLDER is _TEMPLATE_PLACEHOLDER, (
        "the scaffold has its own copy of the engine's placeholder grammar; "
        "import it instead, so divergence is impossible rather than detectable"
    )


@pytest.mark.parametrize(
    "step_id",
    ["plain", "with_underscore", "with-hyphen", "prépare", "步骤", "a.b", "x" * 80, "1", "_"],
)
def test_M3_whatever_the_ENGINE_can_resolve_the_scaffold_can_rewrite(step_id: str) -> None:
    """M3 DETECTOR, the general form: for every step id the ENGINE can resolve
    a reference to, minting must rewrite that reference.

    This is the property round 10's `prépare` finding violated. Asserting it
    over a range of id shapes — rather than the one shape reported — is the
    §3 R-c rule: enumerate the space, do not patch the instance."""
    import json

    from workflow_platform.engine.context import WorkflowContext
    from workflow_platform.engine.executor import _resolve_context_value
    from workflow_platform.scaffold import mint_platform_step_ids

    ctx = WorkflowContext(instance_id="i", workflow_id="w")
    ctx.steps = {step_id: {"value": "SYNTHETIC"}}
    path = f"steps.{step_id}.value"
    resolvable = _resolve_context_value(ctx, path) == "SYNTHETIC"
    if not resolvable:
        pytest.skip(f"the engine cannot resolve a reference to {step_id!r} anyway")

    draft = {
        "steps": [
            {"id": step_id, "type": "deterministic", "function": "noop", "config": {}},
            {"id": "consumer", "type": "agentic", "goal": "g", "model": "m", "inputs": [path]},
        ]
    }
    minted = mint_platform_step_ids(json.loads(json.dumps(draft)))
    assert minted["steps"][1]["inputs"] == ["steps.step_1.value"], (
        f"the engine resolves a reference to step id {step_id!r}, but minting left it "
        f"dangling: {minted['steps'][1]['inputs']}"
    )
