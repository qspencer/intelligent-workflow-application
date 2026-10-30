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

Capability gating runs at the `Agent` layer via `tool_allowed(name)`, and
`allowed_hosts` inside the connector (`BrowserConnector`, G30).
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from workflow_platform.connectors.browser import (
    BrowserBoundedError,
    BrowserConnector,
    BrowserUpload,
    is_vouched_page_error,
)
from workflow_platform.tools.base import Tool, ToolContext, ToolResult

CONNECTOR_KEY = "browser"


def _get_connector(context: ToolContext | None) -> BrowserConnector | None:
    """Resolve the browser connector from the per-run ToolContext, or
    None if no connector is wired (likely a config error or a test path
    that didn't populate context.connectors)."""
    if context is None:
        return None
    conn = context.connectors.get(CONNECTOR_KEY)
    if not isinstance(conn, BrowserConnector):
        return None
    return conn


def _host_policy(context: ToolContext | None) -> Callable[[str], bool] | None:
    """The step's `allowed_hosts` as a predicate (G30), or None when no layer
    restricts hosts — which keeps an unrestricted run exactly as it was. Every
    connector call runs inside `connector.operation(_host_policy(context))`, so
    the policy is this step's for the whole call (external review R2)."""
    caps = context.capabilities if context is not None else None
    return caps.host_allowed if caps is not None and caps.restricts_hosts else None


def _error(tool: str, exc: Exception, context: ToolContext | None) -> ToolResult:
    """The ONE place a browser tool turns an exception into what the agent
    sees. Under a host policy, an error's text passes only if this
    application built it (`BrowserBoundedError`, an upload refusal) or the
    connector vouched it is about the checked, unchanged page; any other
    error — a library's or a parser's — is reduced to its class, because its
    text can quote what a page or server sent (round-6 review, R6-F1: a
    truncated redirect's call log carried the off-list `Location`). This is
    a default: an error nobody anticipated is withheld, not passed on."""
    if (
        _host_policy(context) is None
        or isinstance(exc, (BrowserBoundedError, _UploadSourceError))
        or is_vouched_page_error(exc)
    ):
        return ToolResult(error=f"{tool} failed: {exc}")
    kind = exc.__class__.__name__
    return ToolResult(
        error=f"{tool} failed ({kind}); under this step's allowed_hosts its details were "
        f"withheld, because a library error can quote what a page or server sent "
        f"[call_failed; error={kind}]"
    )


def _selector_param(params: dict[str, Any]) -> str | ToolResult:
    """Common selector-validation. Returns the selector string or a
    ToolResult.error to be returned directly."""
    selector = params.get("selector")
    if not isinstance(selector, str) or not selector:
        return ToolResult(error="`selector` is required (non-empty string)")
    return selector


class BrowserNavigateTool(Tool):
    effect = "mutating"
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
            async with connector.operation(_host_policy(context)):
                await connector.navigate(url, wait_until=wait_until)
        except Exception as exc:
            return _error("browser_navigate", exc, context)
        return ToolResult(content={"url": url, "wait_until": wait_until})


class BrowserReadTextTool(Tool):
    effect = "read_only"
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
            async with connector.operation(_host_policy(context)):
                text = await connector.read_text(sel)
        except Exception as exc:
            return _error("browser_read_text", exc, context)
        return ToolResult(content={"selector": sel, "text": text})


class BrowserReadTableTool(Tool):
    effect = "read_only"
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
            async with connector.operation(_host_policy(context)):
                rows = await connector.read_table(sel)
        except Exception as exc:
            return _error("browser_read_table", exc, context)
        return ToolResult(content={"selector": sel, "rows": rows, "row_count": len(rows)})


class BrowserWaitForTool(Tool):
    effect = "read_only"
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
            async with connector.operation(_host_policy(context)):
                await connector.wait_for(sel, state=state, timeout_ms=timeout_ms)
        except Exception as exc:
            return _error("browser_wait_for", exc, context)
        return ToolResult(content={"selector": sel, "state": state})


class BrowserScreenshotTool(Tool):
    effect = "read_only"
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
            async with connector.operation(_host_policy(context)):
                shot = await connector.screenshot(path=path, full_page=full_page)
        except Exception as exc:
            return _error("browser_screenshot", exc, context)
        return ToolResult(
            content={
                "local_path": shot.local_path,
                "bytes": shot.bytes,
                "full_page": shot.full_page,
            }
        )


class BrowserClickTool(Tool):
    effect = "mutating"
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
            async with connector.operation(_host_policy(context)):
                await connector.click(sel, timeout_ms=timeout_ms)
        except Exception as exc:
            return _error("browser_click", exc, context)
        return ToolResult(content={"selector": sel, "clicked": True})


class BrowserFillTool(Tool):
    effect = "mutating"
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
            async with connector.operation(_host_policy(context)):
                await connector.fill(sel, value, clear_first=clear_first)
        except Exception as exc:
            return _error("browser_fill", exc, context)
        return ToolResult(
            content={"selector": sel, "value_len": len(value), "cleared": clear_first}
        )


#: Larger files belong on a path Playwright streams; none of ours come close.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class _UploadSourceError(Exception):
    """The upload source is not a single regular file this call can send."""


def _read_upload_source(file_path: str) -> BrowserUpload:
    """Open `file_path` once and take its bytes from that open file, so what
    is sent is the file whose type was checked. O_NONBLOCK keeps a FIFO from
    blocking the open; it is then refused like any non-regular file."""
    try:
        fd = os.open(file_path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        raise _UploadSourceError(f"{file_path!r} does not exist") from None
    try:
        mode = os.fstat(fd).st_mode
        if stat.S_ISDIR(mode):
            raise _UploadSourceError(f"uploads one file; {file_path!r} is a directory")
        if not stat.S_ISREG(mode):
            raise _UploadSourceError(f"{file_path!r} is not a regular file")
        with os.fdopen(os.dup(fd), "rb") as source:
            content = source.read(MAX_UPLOAD_BYTES + 1)
    finally:
        os.close(fd)
    if len(content) > MAX_UPLOAD_BYTES:
        raise _UploadSourceError(f"{file_path!r} is larger than {MAX_UPLOAD_BYTES} bytes")
    name = Path(file_path).name
    mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return BrowserUpload(name=name, mime_type=mime_type, content=content)


class BrowserUploadFileTool(Tool):
    effect = "mutating"
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
        # An upload READS a local file and sends it to a page, so it is a
        # `file_read` like any other: an allowed destination host does not
        # authorize every local source (G30 external review R4). Checked before
        # Playwright sees the path, on exactly the string it will open.
        caps = context.capabilities if context is not None else None
        if caps is not None and not caps.can_read(file_path):
            return ToolResult(
                error=f"Capability denied: read {file_path!r} is outside file_read ACL"
            )
        # The FILE is examined only once this call owns the browser, and what
        # reaches the page is the bytes read from it — never the path, which
        # Playwright would classify again and upload a directory's contents if
        # it had become one. Checking the path before waiting for the lock let
        # a queued upload of a missing path become a directory upload (G30
        # round-2 review, R2-F2); the check above is on this path, not on each
        # file inside a directory.
        try:
            async with connector.operation(_host_policy(context)):
                upload = await asyncio.to_thread(_read_upload_source, file_path)
                await connector.upload_file(sel, upload)
        except _UploadSourceError as exc:
            return ToolResult(error=f"browser_upload_file {exc}")
        except Exception as exc:
            return _error("browser_upload_file", exc, context)
        return ToolResult(content={"selector": sel, "file_path": file_path})


class BrowserDownloadTool(Tool):
    effect = "mutating"
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
            async with connector.operation(_host_policy(context)):
                download = await connector.download_via_click(sel, timeout_ms=timeout_ms)
        except Exception as exc:
            return _error("browser_download", exc, context)
        return ToolResult(
            content={
                "selector": sel,
                "local_path": download.local_path,
                "source_url": download.source_url,
                "suggested_filename": download.suggested_filename,
                "bytes": download.bytes,
            }
        )


class BrowserSubmitFormTool(Tool):
    effect = "mutating"
    name: ClassVar[str] = "browser_submit_form"
    description: ClassVar[str] = (
        "Submit the `<form>` matching `selector` via JavaScript `form.submit()`. "
        "Use when the page has no visible submit button — e.g. a hidden submit "
        "div, or a form gated behind UI state. The browser POSTs to the form's "
        "`action` URL with its `method`. If a visible submit button exists, "
        "prefer `browser_click` so the page's JS handlers can run first."
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
            async with connector.operation(_host_policy(context)):
                await connector.submit_form(sel)
        except Exception as exc:
            return _error("browser_submit_form", exc, context)
        return ToolResult(content={"selector": sel})


class BrowserFetchUrlTool(Tool):
    effect = "read_only"
    name: ClassVar[str] = "browser_fetch_url"
    description: ClassVar[str] = (
        "Fetch a URL (image, PDF, JSON, etc.) via the browser session and "
        "save the body to the workflow's per-run downloads directory. Use "
        'this for `<a href="...">` links that don\'t trigger a browser-level '
        "download event when clicked — e.g. inline-displayed images, "
        '`target="_blank"` JPGs from a `read_table` result\'s `*_href` '
        "field. For click-and-capture downloads (the browser's own download "
        "popup), use `browser_download` instead."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": (
                    "Absolute URL to fetch. `read_table` returns absolute "
                    "URLs in `*_href` keys so you can pass them directly."
                ),
            },
            "dest_filename": {
                "type": "string",
                "description": (
                    "Optional filename for the saved file. Defaults to the "
                    "URL's last path segment (e.g. `5.jpg` for "
                    "`/invoices/5.jpg`)."
                ),
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
        dest_filename = params.get("dest_filename")
        if dest_filename is not None and (not isinstance(dest_filename, str) or not dest_filename):
            return ToolResult(error="`dest_filename` must be a non-empty string if provided")
        connector = _get_connector(context)
        if connector is None:
            return ToolResult(error="No browser connector wired into this run")
        try:
            async with connector.operation(_host_policy(context)):
                download = await connector.fetch_url(url, dest_filename=dest_filename)
        except Exception as exc:
            return _error("browser_fetch_url", exc, context)
        return ToolResult(
            content={
                "url": url,
                "local_path": download.local_path,
                "source_url": download.source_url,
                "suggested_filename": download.suggested_filename,
                "bytes": download.bytes,
            }
        )
