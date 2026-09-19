"""The per-(action, field) ownership registry (reviewer-specified, round 16).

A registry without a coverage check is a suggestion. These are the checks
that make it a constraint, plus the property the whole design exists for:
the same field NAME can have different producers under different actions,
which a flat per-field table cannot express — that was round-14 finding 1.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from workflow_platform.trace_projection import (
    AUDIT_FIELD_RULES,
    Owner,
    project_audit_detail_at_rest,
)


def test_every_rule_states_owner_validator_and_disclosure() -> None:
    """TOTALITY. The reviewer's round-16 answer: *"an ownership label
    documents an assertion; writer-side construction and tests must support
    it"* — so a rule must carry all three, and a disclosed field must have a
    validator that can actually reject something."""
    for action, rules in AUDIT_FIELD_RULES.items():
        assert rules, f"{action} has an empty rule set"
        for field, rule in rules.items():
            assert isinstance(rule.owner, Owner), f"{action}.{field} has no owner"
            assert rule.node is not None, f"{action}.{field} has no validator"
            assert isinstance(rule.disclose, bool), f"{action}.{field} has no disclosure rule"


def test_no_BUSINESS_owned_field_is_disclosed() -> None:
    """The rule the registry exists to enforce. BUSINESS = derived from the
    workflow's input, so it is the class `evidence_ref` belongs to — the
    round-14 P1, where a trigger value reached a grant-less reader because
    it was token-SHAPED. Ownership decides, not shape."""
    leaked = [
        f"{action}.{field}"
        for action, rules in AUDIT_FIELD_RULES.items()
        for field, rule in rules.items()
        if rule.owner is Owner.BUSINESS and rule.disclose
    ]
    assert not leaked, (
        f"input-derived fields declared disclosable: {leaked}. Shape bounds damage; "
        "only provenance justifies release."
    )


def test_a_field_absent_from_the_registry_is_WITHHELD() -> None:
    """Default-deny, so adding an action can release more but never leak by
    omission. A field nobody classified must not ride along."""
    out = project_audit_detail_at_rest(
        "memory_observed", {"facts": 1, "brand_new_field": "SYNTHETIC"}
    )
    assert "SYNTHETIC" not in str(out)
    assert out.get("facts") == 1, "classified fields must still come through"
    assert out.get("_withheld_keys") is True


def test_an_action_absent_from_the_registry_falls_back_to_default_deny() -> None:
    """The registry is incremental. An unlisted action keeps the flat
    schema, which is itself default-deny."""
    assert "unlisted_action" not in AUDIT_FIELD_RULES
    out = project_audit_detail_at_rest("unlisted_action", {"free_form": "SYNTHETIC"})
    assert "SYNTHETIC" not in str(out)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("author", "SYNTHETIC-not-an-author"),
        ("facts", "not a number"),
        ("cost_usd", -1),
        ("text_hash", "x" * 300),
        ("backfill", "not a bool"),
    ],
)
def test_a_disclosed_field_still_has_to_pass_its_validator(field: str, bad: Any) -> None:
    """Disclosure is permission, not a bypass. `disclose=True` releases the
    value ONLY if its node validates it."""
    out = project_audit_detail_at_rest("memory_observed", {field: bad})
    assert out.get(field) != bad, f"{field} was released without validating {bad!r}"


def test_the_SAME_field_is_classified_per_ACTION_not_globally() -> None:
    """The property the registry exists for, and the reason a flat table was
    wrong: `evidence_ref` is engine-set to an instance id in the fork and
    judge paths, and resolved from the workflow CONTEXT in the observe path.
    One name, two producers.

    Under `memory_observed` it is BUSINESS and withheld. Under an action
    with no rule it falls back to the flat schema — which also withholds it
    since v10. The point is that the registry can DISTINGUISH them; the flat
    table structurally could not.
    """
    rule = AUDIT_FIELD_RULES["memory_observed"]["evidence_ref"]
    assert rule.owner is Owner.BUSINESS and not rule.disclose

    from workflow_platform.trace_projection import _AUDIT_DETAIL

    assert "evidence_ref" not in _AUDIT_DETAIL.children, (
        "the flat schema must not re-declare a field the registry classifies "
        "per action, or the two disagree depending on which path runs"
    )


def test_the_registry_is_the_ONLY_audit_dispatch() -> None:
    """R17's M3 lesson, pinned. `api/redaction.project_audit_detail` carried
    its own copy of the dispatch; the two agreed only by coincidence, and
    adding the registry to one made them disagree while the read path
    silently kept the old behaviour. One implementation, so a new rule
    reaches every surface at once."""
    src = pathlib.Path("src/workflow_platform/api/redaction.py").read_text()
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "project_audit_detail"
    )
    body = ast.unparse(fn)
    assert "project_audit_detail_final" in body, "the read path no longer delegates"
    assert "redact_tool_data" not in body, (
        "the read path has re-grown its own dispatch; it must delegate so the "
        "registry cannot apply to one surface and not another"
    )


def test_a_registry_action_does_not_silently_withdraw_a_flat_field() -> None:
    """Counterpart check the first registry entry needed and step 0 missed.

    Moving an action onto the registry replaces the flat schema for it
    entirely. Any field the flat schema would have RELEASED, and the rules
    do not mention, is withdrawn silently — a behaviour change nobody
    declared. `workflow_id` and `instance_id` were withdrawn exactly that
    way on the first attempt, caught by an unrelated provenance test.

    A deliberate withdrawal is fine; it just has to be written down here.
    """
    from workflow_platform.trace_projection import _AUDIT_DETAIL

    #: Fields the flat schema declares that a registry action may omit, with
    #: the reason. Empty entries mean "this action must cover everything".
    DELIBERATE: dict[str, dict[str, str]] = {
        "memory_observed": {
            # Governance/grant vocabulary that never appears on this action.
            "request_id": "grant surface only",
            "surface": "grant surface only",
            "outcome": "grant surface only",
            "purpose": "grant surface only",
            "reason_code": "grant surface only",
            "scope": "grant surface only",
            "workload_identity": "grant surface only",
            "kinds": "grant surface only",
            "intended_kinds": "grant surface only",
            "released_kinds": "grant surface only",
            "withheld_kinds": "grant surface only",
            "principal_id": "grant surface only",
            "grant_id": "grant surface only",
            "approved_by": "grant surface only",
            "requested_by": "grant surface only",
            "revoked_by": "grant surface only",
            "approval_mode": "grant surface only",
            "raw_included": "grant surface only",
            "redaction_reason": "grant surface only",
            "org_bypass": "grant surface only",
            "org_scoped": "grant surface only",
            "state": "lifecycle actions only",
            "era": "codify actions only",
            "attempt": "step actions only",
            "type": "step actions only",
            "content_hash": "recall actions only",
            "context_hash": "recall actions only",
            "namespace": "org actions only",
            "mode": "memory-read actions only",
            "budget_action": "budget actions only",
            "unrecognized_ids": "codify actions only",
            "org_id": "not emitted on this action",
            "original_id": "escalation actions only",
            "source_instance_id": "fork actions only",
            "from_step_id": "fork actions only",
            "preserved_step_ids": "fork actions only",
            "connector": "connector actions only",
            "trigger": "not emitted on this action",
            "trigger_payload": "not emitted on this action",
            "tool_calls": "tool_call action only",
            "output": "step actions only",
            "steps": "not emitted on this action",
            "step_ids": "workflow_completed only",
            "_redacted": "a marker, never an input field",
            "emitter": "not emitted on this action",
            "edges": "memory_recalled only",
            "threshold_seconds": "alert actions only",
            "running_for_seconds": "alert actions only",
        }
    }
    flat_released = set(_AUDIT_DETAIL.children)
    for action, rules in AUDIT_FIELD_RULES.items():
        unexplained = flat_released - set(rules) - set(DELIBERATE.get(action, {}))
        assert not unexplained, (
            f"{action}: the flat schema releases {sorted(unexplained)} but the registry "
            "neither classifies nor deliberately excludes them — moving this action onto "
            "the registry silently withdrew them."
        )


def test_the_constructor_emits_exactly_the_classified_fields() -> None:
    """Writer-side half tied to the projection-side half. If the
    constructor grows a field the registry has no rule for, that field is
    withheld silently; if a rule loses its field, the rule is dead."""
    from workflow_platform.memory import LearnedObservation, memory_observed_detail

    obs = LearnedObservation(
        text_hash="sha256:abc",
        author="third_party",
        derived_from=None,
        event_type="email",
        evidence_ref="ref-1",
        facts=1,
        quarantined=0,
        input_tokens=10,
        output_tokens=2,
        cost_usd=0.001,
        model="m",
    )
    emitted = set(memory_observed_detail(obs, namespace="ns", index=0, backfill=True))
    classified = set(AUDIT_FIELD_RULES["memory_observed"])
    unclassified = emitted - classified
    assert not unclassified, (
        f"the constructor emits {sorted(unclassified)} with no rule — silently withheld"
    )
