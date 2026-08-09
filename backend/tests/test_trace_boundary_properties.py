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
    PROJECTOR_VERSION,
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


def test_p1_trigger_routing_fields_are_validated() -> None:
    """Routing ids kept in a projected trigger must pass the SAME validator as
    anywhere else — copying them verbatim made `id` a free-text channel.

    NOTE the deliberate scope. An id-SHAPED value in `message_id` still
    survives, because §1.3 individually justifies `message_id` as a retained
    routing field (it is needed for the pinned mutation). That is an ACCEPTED
    residual, not a bug, so this asserts the actual property — prose and
    whitespace are rejected — rather than the stronger claim that nothing
    recognisable survives, which the design does not make."""
    out = safe_trigger_payload(
        {"id": f"{SENTINEL} with spaces", "thread_id": "subject: " + SENTINEL}
    )
    assert not _leaks(out), f"trigger routing kept prose: {out}"
    # …and a legitimately id-shaped routing value is retained, by design.
    assert safe_trigger_payload({"message_id": "18f3a2b9c4d"})["message_id"] == "18f3a2b9c4d"


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
            {"input_key_count": 0, "name": SENTINEL, "pinned": [SENTINEL], "pin_overrides": [SENTINEL]}
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
