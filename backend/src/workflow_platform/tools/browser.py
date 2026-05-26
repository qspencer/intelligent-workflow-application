"""Agent-callable browser-automation tools.

D3 surface: read-side tools that observe the current page without
mutating it. Each tool looks up the per-workflow-run `BrowserConnector`
from `ToolContext.connectors["browser"]`. The engine (D5 work) is
responsible for lazy-building the connector and populating that dict
when a workflow definition references any `browser_*` tool.

Capability gating runs at the `Agent` layer via `tool_allowed(name)`.
All D3 tools fall under the `browser_read` capability category (one of
the four coarse categories from `docs/BROWSER_CONNECTOR_PLAN.md`).

D4 adds the `browser_write` (click / fill / upload) and
`browser_download` tools alongside.
"""

from __future__ import annotations

from typing import Any, ClassVar

from workflow_platform.connectors.browser import BrowserConnector
from workflow_platform.tools.base import Tool, ToolContext, ToolResult

CONNECTOR_KEY = "browser"


def _get_connector(context: ToolContext | None) -> BrowserConnector | None:
    """Resolve the browser connector from the per-run ToolContext, or
    None if no connector is wired (likely a config error or a test path
    that didn't populate context.connectors)."""
    if context is None:
        return None
    conn = context.connectors.get(CONNECTOR_KEY)
    return conn if isinstance(conn, BrowserConnector) else None


def _selector_param(params: dict[str, Any]) -> str | ToolResult:
    """Common selector-validation. Returns the selector string or a
    ToolResult.error to be returned directly."""
    selector = params.get("selector")
    if not isinstance(selector, str) or not selector:
        return ToolResult(error="`selector` is required (non-empty string)")
    return selector


class BrowserReadTextTool(Tool):
    name: ClassVar[str] = "browser_read_text"
    description: ClassVar[str] = (
        "Read the innerText of the first DOM element matching `selector`. "
        "Selector can be CSS (default) or XPath (leading `/` or `//`). "
        "Returns the text content with surrounding whitespace stripped."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"selector": {"type": "string"}},
        "required": ["selector"],
    }

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        sel = _selector_param(params)
        if isinstance(sel, ToolResult):
            return sel
        connector = _get_connector(context)
        if connector is None:
            return ToolResult(error="No browser connector wired into this run")
        try:
            text = await connector.read_text(sel)
        except Exception as exc:
            return ToolResult(error=f"browser_read_text failed: {exc}")
        return ToolResult(content={"selector": sel, "text": text})


class BrowserReadTableTool(Tool):
    name: ClassVar[str] = "browser_read_table"
    description: ClassVar[str] = (
        "Read an HTML `<table>` matching `selector` and return its rows as "
        "a list of dicts keyed by header text. Headers come from `<thead>` "
        "if present, else the first `<tr>`'s `<th>` cells, else synthetic "
        "`col_0` / `col_1` / ... names for headerless tables."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"selector": {"type": "string"}},
        "required": ["selector"],
    }

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        sel = _selector_param(params)
        if isinstance(sel, ToolResult):
            return sel
        connector = _get_connector(context)
        if connector is None:
            return ToolResult(error="No browser connector wired into this run")
        try:
            rows = await connector.read_table(sel)
        except Exception as exc:
            return ToolResult(error=f"browser_read_table failed: {exc}")
        return ToolResult(content={"selector": sel, "rows": rows, "row_count": len(rows)})


class BrowserWaitForTool(Tool):
    name: ClassVar[str] = "browser_wait_for"
    description: ClassVar[str] = (
        "Wait for the first element matching `selector` to reach `state` "
        "(visible / hidden / attached / detached; default visible). Use "
        "after a click that triggers async content rendering (e.g. waiting "
        "for a table to populate via JavaScript before reading it). "
        "Raises on timeout."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "state": {
                "type": "string",
                "enum": ["visible", "hidden", "attached", "detached"],
            },
            "timeout_ms": {"type": "integer", "minimum": 100},
        },
        "required": ["selector"],
    }

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        sel = _selector_param(params)
        if isinstance(sel, ToolResult):
            return sel
        connector = _get_connector(context)
        if connector is None:
            return ToolResult(error="No browser connector wired into this run")
        state = params.get("state", "visible")
        timeout_ms = params.get("timeout_ms", 5000)
        if state not in ("visible", "hidden", "attached", "detached"):
            return ToolResult(error=f"Invalid state {state!r}")
        if not isinstance(timeout_ms, int) or timeout_ms < 100:
            return ToolResult(error="timeout_ms must be an integer >= 100")
        try:
            await connector.wait_for(sel, state=state, timeout_ms=timeout_ms)
        except Exception as exc:
            return ToolResult(error=f"browser_wait_for failed: {exc}")
        return ToolResult(content={"selector": sel, "state": state})


class BrowserScreenshotTool(Tool):
    name: ClassVar[str] = "browser_screenshot"
    description: ClassVar[str] = (
        "Capture a screenshot of the current page. Returns the local file "
        "path the screenshot was written to. Set `full_page=true` to "
        "capture beyond the viewport (default false — just the viewport)."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "full_page": {"type": "boolean"},
            "path": {
                "type": "string",
                "description": "Optional explicit destination. Default: per-run downloads dir with timestamped name.",
            },
        },
    }

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        connector = _get_connector(context)
        if connector is None:
            return ToolResult(error="No browser connector wired into this run")
        full_page = params.get("full_page", False)
        path = params.get("path")
        if not isinstance(full_page, bool):
            return ToolResult(error="full_page must be a boolean")
        if path is not None and not isinstance(path, str):
            return ToolResult(error="path must be a string if provided")
        try:
            shot = await connector.screenshot(path=path, full_page=full_page)
        except Exception as exc:
            return ToolResult(error=f"browser_screenshot failed: {exc}")
        return ToolResult(
            content={
                "local_path": shot.local_path,
                "bytes": shot.bytes,
                "full_page": shot.full_page,
            }
        )
