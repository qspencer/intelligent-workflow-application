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

from workflow_platform.trace_projection import (
    redact_tool_data,
    safe_tool_call,
    safe_trigger_payload,
)

__all__ = ["redact_tool_data", "safe_tool_call", "safe_trigger_payload"]
