"""G-Trace-Review-4 F4 (P1): under the safe-only flip, EVERY raw audit write must
be projected at rest, and the zero-raw verifier must dispatch on `entry.action`
(so it flags raw error text but does NOT false-flag a correctly-projected
tool_call row, and does NOT flag safe operational metadata).
"""

from __future__ import annotations

from workflow_platform.trace_projection import (
    project_audit_detail_at_rest as at_rest,
)
from workflow_platform.trace_projection import (
    safe_tool_call,
)


def test_raw_error_is_removed_at_rest() -> None:
    """The retry path stores `str(exc)` in the audit detail; at rest it must go."""
    d = {"attempt": 1, "error": "RETRY-RAW victim@example.com"}
    out = at_rest("step_retry", d)
    assert "victim@example.com" not in str(out), out
    assert out["attempt"] == 1, "safe operational field must be kept"


def test_exception_and_entity_are_removed_at_rest() -> None:
    assert "boom@x.com" not in str(at_rest("workflow_failed", {"exception": "boom@x.com"}))
    assert "corr@x.com" not in str(at_rest("memory_recall_failed", {"entity": "corr@x.com"}))


def test_escalation_reason_and_context_are_removed_at_rest() -> None:
    d = {"reason": "SEE victim@example.com", "context": {"body": "raw@x.com"}}
    out = at_rest("escalation_requested", d)
    assert "victim@example.com" not in str(out) and "raw@x.com" not in str(out), out


def test_operational_detail_is_untouched_at_rest() -> None:
    """A workflow_forked / connector detail carries only safe ids + flags — the
    at-rest projection must NOT redact them (else the verifier false-flags and
    operators lose the trail)."""
    d = {"source_instance_id": "i-1", "from_step_id": "b", "preserved_step_ids": ["a"]}
    assert at_rest("workflow_forked", d) == d
    assert at_rest("connector_opened", {"connector": "browser"}) == {"connector": "browser"}


def test_tool_call_dispatches_to_safe_tool_call() -> None:
    d = {"name": "email_send", "input": {"body": "SECRET@x.com"}, "result": {"content": "x"}}
    out = at_rest("tool_call", d)
    assert out == safe_tool_call(d)
    assert "SECRET@x.com" not in str(out)


def test_at_rest_is_idempotent() -> None:
    for action, d in [
        ("step_retry", {"attempt": 1, "error": "RAW@x.com"}),
        ("tool_call", {"name": "t", "input": {"a": 1}, "result": {}}),
        ("escalation_requested", {"reason": "RAW", "context": {"b": "RAW"}}),
    ]:
        once = at_rest(action, d)
        assert at_rest(action, once) == once, f"{action} not idempotent"


def test_verifier_dispatches_on_action() -> None:
    """`_has_raw` must be action-aware: a correctly-projected tool_call row is NOT
    raw; a raw error row IS; a safe operational row is NOT."""
    from workflow_platform.trace_migration import _audit_has_raw

    projected_tc = safe_tool_call({"name": "t", "input": {"a": 1}, "result": {}})
    assert _audit_has_raw(projected_tc, "tool_call") is False, "false-flagged a projected tool_call"
    assert _audit_has_raw({"error": "RAW@x.com"}, "step_retry") is True, "missed raw error"
    assert _audit_has_raw({"connector": "browser"}, "connector_opened") is False, (
        "false-flagged safe operational metadata"
    )
