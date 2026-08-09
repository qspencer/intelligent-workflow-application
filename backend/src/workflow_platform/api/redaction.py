"""Role-aware trace projection at the API read layer (external review
2026-08-01, finding 3 + follow-ups). Raw payloads are stored in full but read
only by grant-holders; every read surface (audit endpoints, the instance
endpoint, explain, WebSocket events) applies this projection for below-grant
readers.

The projection itself lives in the domain layer (`workflow_platform.
trace_projection`) so the engine's write-time safe-only flip (TG3b) can share
it without importing `api`. Re-exported here for the existing API callers.
"""

from __future__ import annotations

from typing import Any

from workflow_platform.trace_projection import (
    has_redaction_marker,
    redact_error,
    redact_tool_data,
    safe_tool_call,
    safe_trigger_payload,
)

__all__ = [
    "has_redaction_marker",
    "project_audit_detail",
    "redact_error",
    "redact_tool_data",
    "safe_tool_call",
    "safe_trigger_payload",
]


def project_audit_detail(action: str | None, detail: dict[str, Any]) -> dict[str, Any]:
    """Project ONE below-grant audit/event detail. Its asset kind depends on the
    entry's ACTION, one level up from the detail itself:

    - a `tool_call` entry's detail IS a tool-call record (name/input/result), so
      it goes through `safe_tool_call` — yielding `input_key_count`, result
      status and the marker, never raw input/result values;
    - every other detail is generic governance metadata, projected against the
      `audit_detail` schema.

    The projector is path-keyed and stateless, so it cannot see the action; the
    dispatch belongs here, at the boundary that holds it. This is the same
    (asset kind, path) rule the re-primitive is built on, applied to audit."""
    if action == "tool_call":
        return safe_tool_call(detail)
    result: dict[str, Any] = redact_tool_data(detail, admin=False, kind="audit_detail")
    return result
