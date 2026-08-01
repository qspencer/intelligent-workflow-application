"""The single role-aware tool-trace projection (external review 2026-08-01
finding 3). Raw tool payloads (mail bodies, file contents, error text) are
stored in full but read only by ADMIN-TIER roles; every read surface
(audit endpoints, the instance endpoint, explain, and WebSocket events)
applies this same redaction for below-admin readers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def safe_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    """One tool-call record → non-sensitive metadata: parameter KEYS (not
    values), result status, and a content hash+size — never raw
    input/result/error text."""
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
        blob = json.dumps(content, sort_keys=True, default=str).encode()
        safe["content_sha256"] = hashlib.sha256(blob).hexdigest()[:16]
        safe["content_bytes"] = len(blob)
    return safe


def redact_tool_data(obj: Any, admin: bool) -> Any:
    """admin=True → unchanged. Else redact raw tool data wherever it appears:
    a `tool_calls` list (in a step output / step_completed detail) or a
    single tool-call-shaped detail (a `tool_call` audit entry). Recurses so
    nested `output` blocks are covered."""
    if admin or not isinstance(obj, dict):
        return obj
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if k == "tool_calls" and isinstance(v, list):
            out[k] = [safe_tool_call(c) if isinstance(c, dict) else c for c in v]
        elif isinstance(v, dict):
            out[k] = redact_tool_data(v, admin)
        else:
            out[k] = v
    if {"input", "result", "name"} <= out.keys() and "tool_calls" not in out:
        return safe_tool_call(out)
    return out
