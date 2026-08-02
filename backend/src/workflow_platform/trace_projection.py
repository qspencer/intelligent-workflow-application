"""The single trace projection (docs/TRACE_GOVERNANCE_PLAN.md §1). Produces
the SAFE form of a raw payload — used at READ time as a role-aware backstop
(re-exported by `api/redaction.py`) AND at WRITE time by the engine's
safe-only flip (TG3b), so it lives in the domain layer, not under `api`.

Three asset classes are projected, each with its own mechanism (they do NOT
share keys, so one function can't cover all): tool-call input/result + a
tool-bearing step's echoing `output_text`; the raw `trigger_payload`/`trigger`
(routing fields kept, content stripped — F4); and the `recall` correspondent
history (withheld — F1/F7).
"""

from __future__ import annotations

import json
from typing import Any

_REDACTED_OUTPUT = "[redacted — tool-bearing step output; admin-tier only]"
_REDACTED_TRIGGER = "raw trigger payload withheld (raw-trace privilege only)"
_REDACTED_RECALL = "[redacted — recalled correspondent history; raw-trace privilege only]"
# Routing fields kept in a redacted trigger payload: IDs, never content
# (subject/body/headers/arbitrary webhook fields are stripped).
_TRIGGER_ROUTING_KEYS = ("message_id", "thread_id", "id")


def safe_trigger_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """A trigger payload (raw inbound mail / webhook body / file event) →
    routing fields only (TRACE_GOVERNANCE_PLAN §1, F4). `redact_tool_data`
    is a no-op on a trigger payload — none of its keys are tool-call-shaped —
    so a below-grant caller recovers the raw email via the instance detail
    endpoint without this. Keeps message_id/thread_id/id (+ sender address)
    so IDs stay visible; strips subject/body/headers/webhook content."""
    safe: dict[str, Any] = {"_redacted": _REDACTED_TRIGGER}
    for key in _TRIGGER_ROUTING_KEYS:
        if key in payload:
            safe[key] = payload[key]
    frm = payload.get("from")
    if isinstance(frm, dict) and "address" in frm:
        safe["from"] = {"address": frm["address"]}
    return safe


def safe_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    """One tool-call record → non-sensitive metadata: parameter KEYS (not
    values), result status, and a content hash+size — never raw
    input/result/error text. IDEMPOTENT: an already-projected record (carries
    `_redacted`) is returned unchanged, so `redact_tool_data` is a fixed point
    on safe data (the zero-raw verifier and backfill rely on this)."""
    if "_redacted" in tc:
        return tc
    result = tc.get("result") or {}
    content = result.get("content")
    safe: dict[str, Any] = {
        "name": tc.get("name"),
        "input_keys": sorted((tc.get("input") or {}).keys()),
        "result_ok": not result.get("error"),
        "error_present": bool(result.get("error")),
        "pinned": tc.get("pinned", []),
        "pin_overrides": tc.get("pin_overrides", []),
        "_redacted": "raw tool input/result withheld (admin-tier only)",
    }
    if content is not None:
        # Byte length only — the truncated hash was dropped (external review
        # 2026-08-01 nonblocking note: a hash is an equality/dictionary
        # oracle for low-entropy results; there's no operational use for it
        # in ordinary-reader responses).
        safe["content_bytes"] = len(json.dumps(content, sort_keys=True, default=str).encode())
    return safe


def redact_tool_data(obj: Any, admin: bool) -> Any:
    """admin=True → unchanged. Else redact raw tool data wherever it appears:
    a `tool_calls` list (in a step output / step_completed detail) or a
    single tool-call-shaped detail (a `tool_call` audit entry). Recurses so
    nested `output` blocks are covered."""
    if admin or not isinstance(obj, dict):
        return obj
    out: dict[str, Any] = {}
    used_tool = False
    for k, v in obj.items():
        if k == "tool_calls" and isinstance(v, list):
            if v:
                used_tool = True
            out[k] = [safe_tool_call(c) if isinstance(c, dict) else c for c in v]
        elif k in ("trigger_payload", "trigger") and isinstance(v, dict):
            # Instance top-level `trigger_payload`, plus the `trigger` key in
            # `context` and the `workflow_started` audit detail — all carry the
            # raw inbound message (TRACE_GOVERNANCE_PLAN §1/§5a, F4).
            out[k] = safe_trigger_payload(v)
        elif k == "recall" and v:
            # Third-party correspondent history injected into an agentic step;
            # withheld entirely below the grant (F1/F7).
            out[k] = _REDACTED_RECALL
        elif isinstance(v, dict):
            out[k] = redact_tool_data(v, admin)
        else:
            out[k] = v
    if {"input", "result", "name"} <= out.keys() and "tool_calls" not in out:
        return safe_tool_call(out)
    # A tool-bearing step's FREE-TEXT output can echo a tool secret the model
    # read (external review 2026-08-01, F3 round 3 — structural redaction of
    # tool_calls isn't enough; the model may paraphrase the result into
    # output_text). Withhold it below the raw-trace privilege whenever the
    # step actually used a tool.
    if used_tool and "output_text" in out:
        out["output_text"] = _REDACTED_OUTPUT
    return out
