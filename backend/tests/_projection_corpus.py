"""The projection corpus the golden guard freezes — one place, shared by the
generator and the guard.

Each case is deliberately a CLASS we have been burned by, not a happy path:
every round's findings are represented, so a regression on any of them moves a
golden output and fails the build. Inputs carry no real secrets — the
"sensitive" values are obviously synthetic.
"""

from __future__ import annotations

from typing import Any

#: (case id, asset kind, input). The expected OUTPUT is not here — it lives in
#: the frozen per-version fixture, so an old fixture stays meaningful even if
#: this corpus later grows.
CORPUS: list[tuple[str, str, Any]] = [
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
        "audit_detail.tool_call",
        "audit_detail",
        {
            "name": "file_read",
            "input": {"path": "/x"},
            "result": {"text": "SYNTHETIC"},
            "pinned": ["path"],
            "pin_overrides": ["path"],
        },
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
]
