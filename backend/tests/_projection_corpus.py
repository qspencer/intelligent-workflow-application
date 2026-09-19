"""The projection corpus the golden guard freezes — one place, shared by the
generator and the guard.

Each case is deliberately a CLASS we have been burned by, not a happy path:
every round's findings are represented, so a regression on any of them moves a
golden output and fails the build. Inputs carry no real secrets — the
"sensitive" values are obviously synthetic.
"""

from __future__ import annotations

from typing import Any

#: (case id, asset kind, input[, audit action]). The expected OUTPUT is not
#: here — it lives in the frozen per-version fixture, so an old fixture stays
#: meaningful even if this corpus later grows.
#:
#: A 4th element routes the case through `project_audit_detail_at_rest(action,
#: …)` instead of `redact_tool_data`. R8 P2: the corpus could only reach ONE
#: entry point, so the action-dispatched at-rest path — where a `tool_call`
#: detail becomes `safe_tool_call` and denylisted fields are redacted — was
#: entirely unguarded, and the case NAMED `audit_detail.tool_call` in fact
#: projected a flat record through the generic schema and never called
#: `safe_tool_call` at all.
CORPUS: list[tuple[Any, ...]] = [
    # --- engine metadata + model-derived business fields (R6/R7 ownership) ---
    (
        "step_output.engine_and_business",
        "step_output",
        {
            "model": "haiku",
            "usage": {"input_tokens": 10, "output_tokens": 2, "iterations": 3},
            "cost_usd": 0.01,
            "faithfulness_score": 5,
            "category_score": 4,
            "needs_tests": True,
            "concern_count": 2,
            "parse_ok": True,
        },
    ),
    (
        "step_output.free_text_is_raw",
        "step_output",
        {
            "output_text": "SYNTHETIC raw body",
            "summary": "SYNTHETIC",
            "model": "haiku",
        },
    ),
    # --- keys are content (R4) ---
    ("step_output.token_shaped_key", "step_output", {"usage": {"AKIAIOSFODNN7EXAMPLE": 1}}),
    ("step_output.hostile_key", "step_output", {"a key with spaces": "x", "model": "m"}),
    # --- the withheld marker, current + legacy + forged (R5/R6/R7) ---
    ("step_output.withheld_current", "step_output", {"_withheld_keys": True, "model": "m"}),
    ("step_output.withheld_legacy", "step_output", {"_withheld_key_count": 3, "model": "m"}),
    ("step_output.withheld_forged", "step_output", {"_withheld_keys": 123456789}),
    # --- nesting: ownership must compose (R7 #1) ---
    (
        "context.nested_business",
        "context",
        {
            "steps": {"eval": {"faithfulness_score": 5, "model": "m", "output_text": "SYNTHETIC"}},
            "workflow_id": "wf",
            "total_tokens": 12,
            "dry_run": False,
        },
    ),
    (
        "step_row.nested_business",
        "step_row",
        {
            "id": "row-1",
            "step_id": "classify",
            "state": "completed",
            "output": {"faithfulness_score": 5, "model": "m"},
        },
    ),
    (
        "instance.nested_context",
        "instance",
        {
            "id": "inst-1",
            "workflow_id": "wf",
            "state": "completed",
            "total_tokens": 3,
            "context": {"steps": {"s": {"relevance_score": 3, "model": "m"}}},
            "trigger_payload": {"type": "webhook", "id": "sk_live_SYNTHETIC", "body": "SYNTHETIC"},
        },
    ),
    # --- tool calls: names, list fields, hostile shapes (R4/R5/R6) ---
    (
        "audit_detail.flat_record_under_generic_schema",
        "audit_detail",
        {
            "name": "file_read",
            "input": {"path": "/x"},
            "result": {"text": "SYNTHETIC"},
            "pinned": ["path"],
            "pin_overrides": ["path"],
        },
    ),
    # --- tool-summary numbers: the BOUNDED range (R7 §4.4) ---
    # Added after the guard PASSED on a change it should have caught: the
    # corpus had only small tool calls, so nothing reached the range where
    # bounding alters the output. A golden guard protects exactly what its
    # corpus reaches, and no more — which is the limit the reviewer named.
    (
        "step_output.tool_call_large_numbers",
        "step_output",
        {
            "tool_calls": [
                {
                    "name": "file_read",
                    "input_key_count": 123456789,
                    "content_bytes": 987654321,
                    "result_ok": True,
                    "error_present": False,
                }
            ]
        },
    ),
    (
        "step_output.tool_call_wide_arity",
        "step_output",
        {
            "tool_calls": [
                {
                    "name": "file_read",
                    "input": {f"p{i}": i for i in range(50)},
                    "result": {"text": "x" * 4096},
                }
            ]
        },
    ),
    # --- the ACTION-DISPATCHED at-rest path (R8 P2) ---
    (
        "at_rest.tool_call_action",
        "audit_detail",
        {
            "name": "file_read",
            "input": {"path": "/x", "mode": "r"},
            "result": {"text": "SYNTHETIC"},
            "pinned": ["path"],
            "pin_overrides": ["path"],
        },
        "tool_call",
    ),
    (
        "at_rest.memory_recalled_action",
        "audit_detail",
        {"query": "SYNTHETIC correspondent", "edges": 3, "context_hash": "abc"},
        "memory_recalled",
    ),
    (
        "at_rest.escalation_requested_action",
        "audit_detail",
        {"reason": "SYNTHETIC reason", "context": {"body": "SYNTHETIC"}, "grant_id": "g-1"},
        "escalation_requested",
    ),
    # --- the five fields DECLARED by the 2026-09-18 at-rest tightening.
    # Added because the pre-package corpus review (protocol step 6) found the
    # golden guard stayed GREEN when `original_id` was undeclared: behaviour
    # had changed in a range the corpus did not reach, so the guard could not
    # see a regression in the only fields the tightening released.
    (
        "at_rest.escalation_resolved_link",
        "audit_detail",
        {"original_id": "esc-1", "resolution": "SYNTHETIC free text"},
        "escalation_resolved",
    ),
    (
        "at_rest.fork_lineage_trail",
        "audit_detail",
        {
            "source_instance_id": "i-1",
            "from_step_id": "b",
            "preserved_step_ids": ["a", "b"],
            "note": "SYNTHETIC free text",
        },
        "workflow_forked",
    ),
    # The range v8 changed. Without a case holding an `@`, the corpus cannot
    # see the difference between `_ID` (admits an email) and `_TOKEN` (does
    # not) on a routing id — which is how v7 shipped the wrong validator.
    (
        "at_rest.routing_id_cannot_hold_an_email",
        "audit_detail",
        {
            "from_step_id": "victim@example.com",
            "source_instance_id": "also@example.com",
            "preserved_step_ids": ["ok-1", "leak@example.com"],
            "original_id": "esc@example.com",
        },
        "workflow_forked",
    ),
    (
        "at_rest.connector_identity",
        "audit_detail",
        {"connector": "browser", "detail": "SYNTHETIC free text"},
        "connector_opened",
    ),
    # --- the at-rest WIDENING (v9). Without cases holding these fields the
    # golden guard cannot see a regression in the only fields v9 released —
    # the same corpus gap the round-12 pre-package review found for v8.
    (
        "at_rest.engine_execution_metadata",
        "audit_detail",
        {
            "workflow_id": "dmarc-ingest",
            "instance_id": "i-1",
            "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "cost_usd": 0.0123,
            "input_tokens": 1200,
            "output_tokens": 47,
            "observation": 3,
            "free_form": "SYNTHETIC prose that must NOT survive",
        },
        "memory_observed",
    ),
    (
        "at_rest.completed_step_ids",
        "audit_detail",
        {"step_ids": ["extract_reports", "deliver_reports"]},
        "workflow_completed",
    ),
    (
        "at_rest.widened_fields_reject_content",
        "audit_detail",
        {
            # Each declared field handed a value its validator must refuse.
            "workflow_id": "an id with spaces",
            "instance_id": "victim@example.com",
            "model": "SYNTHETIC prose in a token field",
            "cost_usd": -1,
            "input_tokens": "not a number",
            "step_ids": ["ok-1", "leak@example.com"],
            "text_hash": "x" * 300,
        },
        "memory_observed",
    ),
    # v10 (R14 F1): source, not shape. The corpus must reach the fields the
    # widening WITHDREW, or the guard cannot see them being re-declared.
    (
        "at_rest.input_derived_reference_withheld",
        "audit_detail",
        {
            "evidence_ref": "SYNTHETIC-from-the-trigger",
            "event_type": "SYNTHETIC-author-controlled",
            "text_hash": "sha256:abc123",
            "facts": 2,
        },
        "memory_observed",
    ),
    (
        "at_rest.classifications_are_closed_enums",
        "audit_detail",
        {"author": "third_party", "derived_from": "system"},
        "memory_observed",
    ),
    (
        "at_rest.forged_classification_refused",
        "audit_detail",
        {"author": "SYNTHETIC-not-an-author", "derived_from": "also-not-one"},
        "memory_observed",
    ),
    (
        "at_rest.user_id_stays_withheld",
        "audit_detail",
        {"user_id": "person@example.com", "workflow_id": "wf"},
        "user_updated",
    ),
    # v11: the per-(action, field) registry. The corpus must reach the
    # registry path, or the golden guard cannot see a rule change.
    (
        "at_rest.registry_memory_observed",
        "audit_detail",
        {
            "observation": 3,
            "facts": 2,
            "quarantined": 0,
            "text_hash": "sha256:abc123",
            "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "cost_usd": 0.0123,
            "input_tokens": 100,
            "output_tokens": 20,
            "backfill": True,
            "author": "third_party",
            "derived_from": "system",
            # withheld by rule, not by omission:
            "event_type": "SYNTHETIC-author-controlled",
            "evidence_ref": "SYNTHETIC-from-the-trigger",
            "user_id": "alice@example.com",
        },
        "memory_observed",
    ),
    (
        "at_rest.registry_rejects_bad_values",
        "audit_detail",
        {
            # Declared and disclosable, but each value fails its validator.
            "author": "SYNTHETIC-not-an-author",
            "facts": "not a number",
            "cost_usd": -1,
            "text_hash": "x" * 300,
            "backfill": "not a bool",
        },
        "memory_observed",
    ),
    (
        "audit_detail.governance",
        "audit_detail",
        {
            "grant_id": "g-1",
            "purpose": "investigation",
            "kinds": ["step_output"],
            "query": "SYNTHETIC correspondent",
            "error": "SYNTHETIC trace",
        },
    ),
    # v12: the registry extended to the engine's own execution trail and the
    # monitoring alerts — 93% of the audit log by volume. One case per new
    # action, each carrying the fields production actually stores plus a
    # hostile value at the position the rules withhold, so the golden guard
    # sees a rule change rather than only a schema change.
    (
        "at_rest.registry_step_started",
        "audit_detail",
        {"type": "agentic", "attempt": 1},
        "step_started",
    ),
    (
        "at_rest.registry_step_started_forged_type",
        "audit_detail",
        {"type": "SYNTHETIC-not-a-step-type", "attempt": 1},
        "step_started",
    ),
    (
        "at_rest.registry_step_completed",
        "audit_detail",
        {
            "attempt": 2,
            "output": {
                "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "usage": {"input_tokens": 10, "output_tokens": 3},
                "cost_usd": 0.0001,
                # BUSINESS child of the nested schema — withheld by
                # `_STEP_OUTPUT.owners`, not by the registry rule.
                "faithfulness_score": 4,
                "output_text": "SYNTHETIC model prose",
            },
        },
        "step_completed",
    ),
    (
        "at_rest.registry_step_failed",
        "audit_detail",
        {"attempt": 3, "error": "SYNTHETIC exception text", "unexpected": True},
        "step_failed",
    ),
    (
        "at_rest.registry_step_postcondition_failed",
        "audit_detail",
        {
            "require_tool_call": "email_label_apply",
            "min_success": 1,
            "actual_success": 0,
            "stop_reason": "max_iterations",
        },
        "step_postcondition_failed",
    ),
    (
        "at_rest.registry_workflow_started",
        "audit_detail",
        {
            "workflow_id": "email-triage",
            "trigger": {
                "message_id": "msg-1",
                "subject": "SYNTHETIC subject",
                "from": "alice@example.com",
            },
        },
        "workflow_started",
    ),
    (
        "at_rest.registry_workflow_completed",
        "audit_detail",
        # `steps` is a step-id LIST in production, not a context snapshot —
        # the shape v12 got wrong by reading the flat schema's node off the
        # shared key name.
        {"step_ids": ["fetch", "classify"], "steps": ["fetch", "classify"]},
        "workflow_completed",
    ),
    (
        "at_rest.registry_workflow_failed",
        "audit_detail",
        {"error": "SYNTHETIC failure", "exception": "SYNTHETIC exc", "unexpected": True},
        "workflow_failed",
    ),
    (
        "at_rest.registry_workflow_forked",
        "audit_detail",
        {
            "source_instance_id": "inst-1",
            # The v8 defect, at the position it was found.
            "from_step_id": "victim@example.com",
            "preserved_step_ids": ["fetch", "classify"],
        },
        "workflow_forked",
    ),
    (
        "at_rest.registry_budget_exceeded",
        "audit_detail",
        {"tokens_used": 5000, "tokens_limit": 4000, "cost_usd": 0.12, "action": "pause"},
        "budget_exceeded",
    ),
    (
        "at_rest.registry_alert_stuck_workflow",
        "audit_detail",
        {
            "instance_id": "inst-1",
            "workflow_id": "email-triage",
            "running_for_seconds": 3600.5,
            "threshold_seconds": 900.0,
        },
        "alert_stuck_workflow",
    ),
    (
        "at_rest.registry_alert_stale_trigger",
        "audit_detail",
        {
            "workflow_id": "email-triage",
            "trigger_type": "email",
            # Operator-authored AND a mailbox identity: withheld.
            "account": "inbox@example.com",
            # A full `datetime.isoformat()` — microseconds AND offset. The
            # v12 case used a short `...Z` form, which is why `_TS_RE`'s tail
            # bound went unnoticed while 258 production values were redacted.
            "last_run_at": "2026-09-15T10:49:28.384385+00:00",
            "threshold_seconds": 86400.0,
        },
        "alert_stale_trigger",
    ),
    (
        "at_rest.registry_alert_abandoned_pause",
        "audit_detail",
        {
            "instance_id": "inst-1",
            "workflow_id": "email-triage-apply",
            "paused_for_seconds": 14400.5,
            "threshold_seconds": 10800.0,
        },
        "alert_abandoned_pause",
    ),
    (
        "at_rest.registry_alert_high_error_rate",
        "audit_detail",
        {
            "rate": 0.42,
            "threshold": 0.2,
            "failed": 21,
            "total_terminal": 50,
            # A FLOAT: `MonitoringConfig.error_rate_window_seconds` is one,
            # and the v12 int literal is what hid `_COUNT` rejecting it.
            "window_seconds": 900.0,
        },
        "alert_high_error_rate",
    ),
    (
        "at_rest.registry_memory_recalled",
        "audit_detail",
        {
            "user_id": "alice@example.com",
            "query": "SYNTHETIC correspondent question",
            "context_hash": "sha256:def456",
            "edges": 4,
            "episodes": 2,
            "token_budget": 800,
            "injected": True,
            "uses_recorded": {"recorded": 3, "upgraded": 1, "failed": 0},
        },
        "memory_recalled",
    ),
    (
        "at_rest.registry_memory_observe_failed",
        "audit_detail",
        {"observation": 0, "error": "SYNTHETIC veracium failure"},
        "memory_observe_failed",
    ),
]
