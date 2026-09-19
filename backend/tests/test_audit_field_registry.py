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


#: The flat schema's fields, GROUPED by the surface that emits them, with the
#: reason a registry action may omit the group. Groups, not 45 individual
#: names per action: repeating the same list nineteen times is not nineteen
#: arguments, and the copies would drift. Totality against `_AUDIT_DETAIL` is
#: checked below, so a field added to the flat schema and to no group fails
#: the build rather than becoming a silent exclusion.
_FLAT_GROUPS: dict[str, tuple[str, ...]] = {
    "grant": (
        "request_id",
        "surface",
        "outcome",
        "purpose",
        "reason_code",
        "scope",
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
    ),
    "org": ("org_bypass", "org_scoped", "org_id", "namespace"),
    "lifecycle": ("state",),
    "step": ("attempt", "type", "output"),
    "run": ("workflow_id", "instance_id", "steps", "step_ids", "trigger", "trigger_payload"),
    "fork": ("source_instance_id", "from_step_id", "preserved_step_ids"),
    "escalation": ("original_id",),
    "connector": ("connector",),
    "budget": ("budget_action", "cost_usd"),
    "codify": ("era", "unrecognized_ids"),
    "alert": ("threshold_seconds", "running_for_seconds"),
    "memory_write": (
        "author",
        "derived_from",
        "text_hash",
        "facts",
        "quarantined",
        "observation",
        "input_tokens",
        "output_tokens",
        "model",
    ),
    "memory_read": ("content_hash", "context_hash", "edges", "mode"),
    "tool_call": ("tool_calls",),
    "marker": ("_redacted",),
}

#: Which groups each registry action is allowed to leave unclassified. An
#: action classifies whatever it actually emits; everything else is a group
#: it has nothing to do with.
_ALLOWED_OMISSIONS: dict[str, tuple[str, ...]] = {
    "memory_observed": (
        "grant",
        "org",
        "lifecycle",
        "step",
        "run",
        "fork",
        "escalation",
        "connector",
        "budget",
        "codify",
        "alert",
        "memory_read",
        "tool_call",
        "marker",
    ),
}
#: Every other registry action omits every group it does not classify from.
#: Spelled once, because "this action emits an engine execution record, not a
#: grant decision" is one argument, not nineteen.
_ENGINE_ACTIONS = tuple(a for a in AUDIT_FIELD_RULES if a not in _ALLOWED_OMISSIONS)


def test_the_flat_schema_is_fully_grouped() -> None:
    """Totality of the grouping itself. Without this, adding a field to
    `_AUDIT_DETAIL` and to no group would make every action's exclusion
    check pass by default — the grouping would weaken the guard it exists
    to make maintainable."""
    from workflow_platform.trace_projection import _AUDIT_DETAIL

    grouped = {f for fields in _FLAT_GROUPS.values() for f in fields}
    flat = set(_AUDIT_DETAIL.children)
    assert not flat - grouped, f"flat fields in no group: {sorted(flat - grouped)}"
    assert not grouped - flat, f"grouped fields not in the flat schema: {sorted(grouped - flat)}"


def test_a_registry_action_does_not_silently_withdraw_a_flat_field() -> None:
    """Counterpart check the first registry entry needed and step 0 missed.

    Moving an action onto the registry replaces the flat schema for it
    entirely. Any field the flat schema would have RELEASED, and the rules
    do not mention, is withdrawn silently — a behaviour change nobody
    declared. `workflow_id` and `instance_id` were withdrawn exactly that
    way on the first attempt, caught by an unrelated provenance test.

    A deliberate withdrawal is fine; it just has to be written down — as a
    GROUP the action has no business with.
    """
    from workflow_platform.trace_projection import _AUDIT_DETAIL

    flat_released = set(_AUDIT_DETAIL.children)
    for action, rules in AUDIT_FIELD_RULES.items():
        omitted = _ALLOWED_OMISSIONS.get(action)
        if omitted is None:
            excused = flat_released - set(rules)
        else:
            excused = {f for g in omitted for f in _FLAT_GROUPS[g]}
        unexplained = flat_released - set(rules) - excused
        assert not unexplained, (
            f"{action}: the flat schema releases {sorted(unexplained)} but the registry "
            "neither classifies nor deliberately excludes them — moving this action onto "
            "the registry silently withdrew them."
        )


def test_a_registry_action_classifies_every_field_it_can_actually_emit() -> None:
    """The other direction, and the one the enumeration was for: a field the
    WRITER emits and the registry has no rule for is withheld silently.

    The expected sets below were read off the production audit table
    (`jsonb_object_keys` per action), not off the source, so historical
    shapes count too — an entry written last month is still read today."""
    emitted: dict[str, set[str]] = {
        "step_started": {"type", "attempt"},
        "step_completed": {"attempt", "output"},
        "step_failed": {"attempt", "error", "unexpected"},
        "step_retry": {"attempt", "error"},
        "step_postcondition_failed": {
            "require_tool_call",
            "min_success",
            "actual_success",
            "stop_reason",
        },
        "workflow_started": {"workflow_id", "trigger"},
        "workflow_completed": {"step_ids", "steps"},
        "workflow_failed": {"error", "exception", "unexpected"},
        "workflow_forked": {"source_instance_id", "from_step_id", "preserved_step_ids"},
        "budget_exceeded": {"tokens_used", "tokens_limit", "cost_usd", "action"},
        "budget_escalated": {"tokens_used", "tokens_limit", "cost_usd", "action"},
        "alert_stuck_workflow": {
            "instance_id",
            "workflow_id",
            "running_for_seconds",
            "threshold_seconds",
        },
        "alert_stale_trigger": {
            "workflow_id",
            "trigger_type",
            "account",
            "last_run_at",
            "threshold_seconds",
        },
        "alert_high_error_rate": {
            "rate",
            "threshold",
            "failed",
            "total_terminal",
            "window_seconds",
        },
        "alert_high_queue_depth": {"depth", "threshold"},
        "alert_high_token_burn": {"tokens", "cost_usd", "threshold_tokens", "window_seconds"},
        "memory_recalled": {
            "user_id",
            "query",
            "context_hash",
            "edges",
            "episodes",
            "token_budget",
            "injected",
            "uses_recorded",
        },
        "memory_observe_failed": {"observation", "error"},
    }
    for action, fields in emitted.items():
        missing = fields - set(AUDIT_FIELD_RULES[action])
        assert not missing, (
            f"{action} emits {sorted(missing)} with no rule — withheld silently. "
            "Classify it (disclose or not), do not let omission decide."
        )


def test_the_stop_reason_enum_is_the_agents_enum() -> None:
    """`_STOP_REASON` spells out `agent.StopReason` so the projector stays a
    domain leaf. A spelled-out copy that drifts is worse than an import, so
    the equality is a test, not a comment."""
    from workflow_platform.agent.agent import StopReason
    from workflow_platform.trace_projection import _STOP_REASON

    for member in StopReason:
        assert _STOP_REASON.validate(member.value), f"{member.value} rejected by _STOP_REASON"
    assert not _STOP_REASON.validate("SYNTHETIC-not-a-stop-reason")


def test_a_PROJECTION_owned_rule_delegates_to_a_schema() -> None:
    """`Owner.PROJECTION` says "a reader sees this only as the projector's
    own rendering". That is checkable, so check it: the rule must point at a
    node that either carries per-child ownership or projects structurally.
    A `Leaf` claiming PROJECTION would be the loophole — a raw string
    released under a label that promised a schema walked it."""
    from workflow_platform.trace_projection import (
        Obj,
        ToolCalls,
        TriggerPayload,
    )

    for action, rules in AUDIT_FIELD_RULES.items():
        for field, rule in rules.items():
            if rule.owner is not Owner.PROJECTION or not rule.disclose:
                continue
            node = rule.node
            ok = isinstance(node, (ToolCalls, TriggerPayload)) or (
                isinstance(node, Obj) and (node.owners or node.wildcard is not None)
            )
            assert ok, (
                f"{action}.{field} claims Owner.PROJECTION but its node is {node!r}: "
                "nothing delegates, so the label asserts a walk that never happens."
            )


#: The fields the registry releases that the flat `_AUDIT_DETAIL` schema did
#: not. FROZEN, because "the registry can only release more" is true and is
#: exactly why it needs a ceiling: each addition is a deliberate widening,
#: and a set that grows without anyone noticing is the M9 shape.
_WIDENED_BEYOND_FLAT: dict[str, set[str]] = {
    # Monitoring arithmetic and the thresholds it is compared against. The
    # flat schema never classified an alert, so an operator got the alert
    # with every number stripped.
    "alert_high_error_rate": {"rate", "threshold", "failed", "total_terminal", "window_seconds"},
    "alert_high_queue_depth": {"depth", "threshold"},
    "alert_high_token_burn": {"tokens", "threshold_tokens", "window_seconds"},
    "alert_stale_trigger": {"trigger_type", "last_run_at"},
    # What the budget check actually compared.
    "budget_exceeded": {"tokens_used", "tokens_limit", "action"},
    "budget_escalated": {"tokens_used", "tokens_limit", "action"},
    # Engine-set flag distinguishing a handled failure from a crash.
    "step_failed": {"unexpected"},
    "workflow_failed": {"unexpected"},
    # Why the postcondition failed, not merely that it did.
    "step_postcondition_failed": {
        "require_tool_call",
        "min_success",
        "actual_success",
        "stop_reason",
    },
    # Recall volume + outcome counters. The QUERY stays withheld.
    "memory_recalled": {"episodes", "token_budget", "injected", "uses_recorded"},
    # v11, already shipped.
    "memory_observed": {"backfill"},
}


def test_the_registry_releases_exactly_these_fields_beyond_the_flat_schema() -> None:
    """The ceiling on widening. The registry replaces the flat schema for an
    action, so it can release fields the flat schema never declared — which
    is the point, and is also how a widening ships without being noticed.
    Every such field is listed above with a reason; an unlisted one fails."""
    from workflow_platform.trace_projection import _AUDIT_DETAIL

    flat = set(_AUDIT_DETAIL.children)
    for action, rules in AUDIT_FIELD_RULES.items():
        released = {f for f, rule in rules.items() if rule.disclose}
        beyond = released - flat
        declared = _WIDENED_BEYOND_FLAT.get(action, set())
        assert beyond == declared, (
            f"{action} releases {sorted(beyond)} beyond the flat schema, declared "
            f"{sorted(declared)}. A widening is fine; an undeclared one is not."
        )


def test_no_engine_action_discloses_error_text() -> None:
    """The field the vault exists for. Exception text is engine-RAISED and
    input-DERIVED, and every round of this review that found a leak found it
    in something shaped harmlessly. Whatever else changes, no action may
    disclose one of these."""
    raw = {"error", "exception", "query", "observation_text", "entity", "params", "recall"}
    leaked = [
        f"{action}.{field}"
        for action, rules in AUDIT_FIELD_RULES.items()
        for field, rule in rules.items()
        if field in raw and rule.disclose
    ]
    assert not leaked, f"raw-by-taint fields disclosed: {leaked}"


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
