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

SENTINEL = "SENTINEL-9f3c2a-RAW"

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
        assert not _leaks(redact_tool_data(payload, admin=False)), (
            f"leaked at depth={depth} keys={keys[:2]}… shape={type(shaped).__name__}"
        )


def test_p1_registered_key_cannot_launder_prose() -> None:
    """A registered field must not admit values its declared shape forbids —
    an opaque-id validator that accepts an email address is not a validator."""
    for value in ("alice@example.com", "INV-2026-000184", f"{SENTINEL}", "550e8400-e29b-41d4"):
        out = redact_tool_data({"model": value}, admin=False)
        assert out["model"] != value or value.isalnum(), (
            f"`model` admitted {value!r}; an approved opaque id may not carry an "
            "external identifier (TRACE_GOVERNANCE_PLAN §1.4a)"
        )


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
    out = redact_tool_data({"_redacted": forged}, admin=False)
    assert not _leaks(out), f"forged marker survived: {out}"


# --- P3: the vault decision must agree with the projection (B1) -------------


# NOTE: a test asserting `output_has_raw(x) == (redact(x) != x)` was written and
# then DELETED — `output_has_raw` is *defined* as that expression, so the test is
# tautological and can never fail. It looked like coverage of the B1 decision
# while proving nothing. The real property is the one below: raw must require
# the vault, judged against a sentinel rather than against the projector itself.


def test_p3_registered_looking_raw_still_needs_the_vault() -> None:
    assert output_has_raw({"model": SENTINEL}), (
        "a registered field holding raw reported needs_vault=False — under "
        "TRACE_SAFE_ONLY this commits plaintext to the operational store"
    )


# --- P5: one authoritative projector version --------------------------------


def test_p5_single_authoritative_projector_version() -> None:
    """Operational rows and vault rows must identify the SAME projector, or the
    §4.3 agreement predicate compares against a version that never wrote it."""
    from workflow_platform.persistence.models import PROJECTOR_VERSION as VAULT_PV

    assert PROJECTOR_VERSION == VAULT_PV, (
        f"two projector versions: projection={PROJECTOR_VERSION!r} vault={VAULT_PV!r}"
    )
