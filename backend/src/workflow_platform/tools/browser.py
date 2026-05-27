"""Agent-callable browser-automation tools.

Four categories of behavior, each with its own capability gate:
  - `browser_navigate`: load URLs. Solely `browser_navigate` tool.
  - `browser_read`: observe the page without mutating it — read_text,
    read_table, wait_for, screenshot.
  - `browser_write`: mutate the page — click, fill, upload_file.
  - `browser_download`: click-and-capture-file. Separated from
    `browser_write` because it produces a file artifact; a workflow
    can be permitted to download without being permitted to fill forms.

Each tool looks up the per-workflow-run `BrowserConnector` from
`ToolContext.connectors["browser"]`. The engine (D5 work) is
responsible for lazy-building the connector and populating that dict
when a workflow definition references any `browser_*` tool.

Capability gating runs at the `Agent` layer via `tool_allowed(name)`.
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


class BrowserNavigateTool(Tool):
    name: ClassVar[str] = "browser_navigate"
    description: ClassVar[str] = (
        "Load a URL in the current browser session. `wait_until` controls "
        "when navigation is considered complete: 'load' (default — fires "
        "after DOMContentLoaded + resources) / 'domcontentloaded' (DOM "
        "ready but resources may still load) / 'networkidle' (no network "
        "for 500ms — slow but reliable for SPAs) / 'commit' (URL committed; "
        "fastest but no content guaranteed)."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "wait_until": {
                "type": "string",
                "enum": ["load", "domcontentloaded", "networkidle", "commit"],
            },
        },
        "required": ["url"],
    }

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        url = params.get("url")
        if not isinstance(url, str) or not url:
            return ToolResult(error="`url` is required (non-empty string)")
        connector = _get_connector(context)
        if connector is None:
            return ToolResult(error="No browser connector wired into this run")
        wait_until = params.get("wait_until", "load")
        if wait_until not in ("load", "domcontentloaded", "networkidle", "commit"):
            return ToolResult(error=f"Invalid wait_until {wait_until!r}")
        try:
            await connector.navigate(url, wait_until=wait_until)
        except Exception as exc:
            return ToolResult(error=f"browser_navigate failed: {exc}")
        return ToolResult(content={"url": url, "wait_until": wait_until})


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


class BrowserClickTool(Tool):
    name: ClassVar[str] = "browser_click"
    description: ClassVar[str] = (
        "Click the first DOM element matching `selector`. Selector can be "
        "CSS (default) or XPath (leading `/` or `//`). Returns the selector "
        "that was clicked on success; raises a clear error if the element "
        "isn't visible within the timeout."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
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
        timeout_ms = params.get("timeout_ms", 5000)
        if not isinstance(timeout_ms, int) or timeout_ms < 100:
            return ToolResult(error="timeout_ms must be an integer >= 100")
        try:
            await connector.click(sel, timeout_ms=timeout_ms)
        except Exception as exc:
            return ToolResult(error=f"browser_click failed: {exc}")
        return ToolResult(content={"selector": sel, "clicked": True})


class BrowserFillTool(Tool):
    name: ClassVar[str] = "browser_fill"
    description: ClassVar[str] = (
        "Fill an `<input>` / `<textarea>` matching `selector` with `value`. "
        "By default the field is cleared first (typical use). Pass "
        "`clear_first=false` to append to the existing value."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "value": {"type": "string"},
            "clear_first": {"type": "boolean"},
        },
        "required": ["selector", "value"],
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
        value = params.get("value")
        if not isinstance(value, str):
            return ToolResult(error="`value` is required (string)")
        clear_first = params.get("clear_first", True)
        if not isinstance(clear_first, bool):
            return ToolResult(error="clear_first must be a boolean")
        try:
            await connector.fill(sel, value, clear_first=clear_first)
        except Exception as exc:
            return ToolResult(error=f"browser_fill failed: {exc}")
        return ToolResult(
            content={"selector": sel, "value_len": len(value), "cleared": clear_first}
        )


class BrowserUploadFileTool(Tool):
    name: ClassVar[str] = "browser_upload_file"
    description: ClassVar[str] = (
        'Set the value of an `<input type="file">` matching `selector` to '
        "the local file at `file_path`. Does NOT click submit — that's a "
        "separate `browser_click` call so the workflow controls ordering."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "file_path": {"type": "string"},
        },
        "required": ["selector", "file_path"],
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
        file_path = params.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return ToolResult(error="`file_path` is required (non-empty string)")
        try:
            await connector.upload_file(sel, file_path)
        except Exception as exc:
            return ToolResult(error=f"browser_upload_file failed: {exc}")
        return ToolResult(content={"selector": sel, "file_path": file_path})


class BrowserDownloadTool(Tool):
    name: ClassVar[str] = "browser_download"
    description: ClassVar[str] = (
        "Click an element matching `selector` and capture the resulting "
        "browser download. Returns the local file path the download was "
        "saved to (under the workflow's per-run downloads directory), the "
        "source URL, and the suggested filename. Use this for download "
        "links / buttons; for non-download clicks use `browser_click`."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "timeout_ms": {
                "type": "integer",
                "minimum": 100,
                "description": "Max wait for the download to start AND the click. Default 30000.",
            },
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
        timeout_ms = params.get("timeout_ms", 30000)
        if not isinstance(timeout_ms, int) or timeout_ms < 100:
            return ToolResult(error="timeout_ms must be an integer >= 100")
        try:
            download = await connector.download_via_click(sel, timeout_ms=timeout_ms)
        except Exception as exc:
            return ToolResult(error=f"browser_download failed: {exc}")
        return ToolResult(
            content={
                "selector": sel,
                "local_path": download.local_path,
                "source_url": download.source_url,
                "suggested_filename": download.suggested_filename,
                "bytes": download.bytes,
            }
        )
