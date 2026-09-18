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
    tokens (GR4-r2 F1)."""
    for field in ("pinned", "pin_overrides"):
        out = safe_tool_call({"name": "t", "input": {}, "result": {}, field: SENTINEL})
        assert not _leaks(out) and out[field] == [], f"{field} decomposed a string: {out}"


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

    # Fail CLOSED: with no catalog to resolve against, no name is emitted.
    blind = safe_tool_call({"name": "file_read", "input": {"a": 1}})
    assert "file_read" not in _dumps(blind), f"name emitted with no catalog: {blind}"
