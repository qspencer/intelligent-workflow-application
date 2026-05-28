"""End-to-end test for the RPA Challenge OCR validation workflow.

Replay-mode: no real Bedrock, no real Chromium, no real tesseract.

What this proves:
1. The workflow YAML loads + validates as a DAG.
2. With injected fakes:
   - Each agentic step's `tools` list resolves through the engine's
     `ToolCatalog`.
   - The engine lazy-builds the browser connector once (verifying D5's
     detection logic), exits via the workflow_completed path, and
     tears down the connector even though no real browser was used.
   - The deterministic `filter_rows_by_date` and `write_csv` functions
     fire end-to-end with real-shape data flowing between agentic and
     deterministic steps.
3. The final CSV on disk has the expected header + body — closing
   the loop from "agent emits JSON" through to "operator opens CSV".

The agent steps speak in pre-canned `FakeBedrock` responses: tool-use
calls match the workflow's intent (navigate, click, wait_for, etc.)
and final text responses carry the JSON the next step parses. The
fake browser connector records every tool call so the assertions can
verify the agents drove the expected URLs / selectors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from tests.test_engine_browser_integration import _RecordingBrowserConnector
from workflow_platform.connectors.browser import BrowserDownload, BrowserScreenshot
from workflow_platform.engine import ToolCatalog, WorkflowEngine
from workflow_platform.engine.functions import default_function_registry
from workflow_platform.persistence import WorkflowInstanceState, in_memory_repositories
from workflow_platform.tools import (
    BrowserClickTool,
    BrowserFetchUrlTool,
    BrowserNavigateTool,
    BrowserReadTableTool,
    BrowserReadTextTool,
    BrowserScreenshotTool,
    BrowserSubmitFormTool,
    BrowserUploadFileTool,
    BrowserWaitForTool,
    ImageOcrTool,
    ToolResult,
)
from workflow_platform.tools.base import ToolContext
from workflow_platform.workflow import load_definition_from_yaml
from workflow_platform.world import real_world

EXAMPLE_DIR = Path(__file__).parent.parent.parent / "examples" / "rpa_challenge_ocr"


# ---------- Smart fake browser for the RPA challenge ----------


class _RpaFakeBrowser(_RecordingBrowserConnector):
    """RPA-challenge-specific stand-in. Adds table data + downloadable
    invoices so the workflow's read_table / browser_download paths land
    on something realistic."""

    def __init__(self) -> None:
        super().__init__()
        self.table_rows: list[dict[str, str]] = []
        self.download_dir: Path = Path("/tmp/rpa-fake-downloads")
        self.screenshot_path: str = "/tmp/rpa-result.png"
        self.uploaded_files: list[tuple[str, str]] = []
        self._download_queue: list[tuple[str, bytes]] = []

    async def read_table(self, selector: str) -> list[dict[str, str]]:
        # Mirrors the real challenge: returns the visible rows once Start has
        # been clicked + the table has populated.
        return list(self.table_rows)

    async def download_via_click(
        self, selector: str, *, timeout_ms: int = 30000
    ) -> BrowserDownload:
        # Each click corresponds to a row in order; pop them off so
        # consecutive clicks resolve to different invoices.
        url, content = self._next_download()
        target = self.download_dir / Path(url).name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return BrowserDownload(
            source_url=url,
            local_path=str(target),
            suggested_filename=target.name,
            bytes=len(content),
        )

    async def fetch_url(self, url: str, *, dest_filename: str | None = None) -> BrowserDownload:
        """Match the URL against the staged downloads queue. Each
        fetched URL pops one entry — same ordering contract as
        download_via_click."""
        _staged_url, content = self._next_download()
        name = dest_filename or Path(url).name or "download"
        target = self.download_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return BrowserDownload(
            source_url=url,
            local_path=str(target),
            suggested_filename=name,
            bytes=len(content),
        )

    async def upload_file(self, selector: str, file_path: str) -> None:
        self.uploaded_files.append((selector, file_path))

    async def submit_form(self, selector: str) -> None:
        self.submitted_forms: list[str] = getattr(self, "submitted_forms", [])
        self.submitted_forms.append(selector)

    async def screenshot(
        self, *, path: str | None = None, full_page: bool = False
    ) -> BrowserScreenshot:
        actual = path or self.screenshot_path
        Path(actual).parent.mkdir(parents=True, exist_ok=True)
        Path(actual).write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return BrowserScreenshot(local_path=actual, bytes=15, full_page=full_page)

    def stage_downloads(self, items: list[tuple[str, bytes]]) -> None:
        self._download_queue = list(items)

    def _next_download(self) -> tuple[str, bytes]:
        if not self._download_queue:
            raise RuntimeError("Out of staged downloads in _RpaFakeBrowser")
        return self._download_queue.pop(0)


# ---------- ImageOcrTool stub: bypass tesseract by returning canned text ----------


class _CannedImageOcrTool(ImageOcrTool):
    """Stand-in for ImageOcrTool that maps `filepath` → canned OCR text
    rather than shelling out to tesseract. Lets the workflow test
    verify the OCR-text → JSON path without depending on the tesseract
    binary or actual image bytes."""

    def __init__(self) -> None:
        self.canned: dict[str, str] = {}  # filename → ocr text

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        filepath = params.get("filepath")
        if not isinstance(filepath, str):
            return ToolResult(error="filepath required")
        text = self.canned.get(Path(filepath).name)
        if text is None:
            return ToolResult(error=f"no canned OCR text staged for {filepath!r}")
        return ToolResult(content={"text": text, "char_count": len(text), "lang": "eng"})


# ---------- Test ----------


async def test_rpa_challenge_workflow_runs_end_to_end(tmp_path: Path) -> None:
    repos = in_memory_repositories()

    # --- Stage browser fake with table + invoice downloads ---
    browser = _RpaFakeBrowser()
    browser.download_dir = tmp_path / "downloads"
    browser.table_rows = [
        {"id": "1", "due_date": "2024-05-20", "invoice_url": "/invoices/1.jpg"},
        {"id": "2", "due_date": "2026-12-31", "invoice_url": "/invoices/2.jpg"},
        {"id": "3", "due_date": "2024-08-01", "invoice_url": "/invoices/3.jpg"},
    ]
    browser.stage_downloads(
        [
            ("https://rpa/1.jpg", b"\xff\xd8jpg-bytes-1"),
            ("https://rpa/3.jpg", b"\xff\xd8jpg-bytes-3"),
        ]
    )

    # --- Stage OCR tool with canned text per filename ---
    ocr = _CannedImageOcrTool()
    ocr.canned = {
        "1.jpg": (
            "INVOICE\nInvoice Number: INV-001\nInvoice Date: 15/05/2024\n"
            "Company: Acme Corp\nTotal Due: $123.45"
        ),
        "3.jpg": (
            "INVOICE\nInvoice Number: INV-003\nInvoice Date: Jul 28, 2024\n"
            "Company: Globex LLC\nTotal Due: $4,567.89"
        ),
    }

    # --- Build the canned Bedrock conversation, step by step ---
    # open_challenge: 3 tool-use calls + final text
    open_calls = [
        ("c1", "browser_navigate", {"url": "https://rpachallengeocr.azurewebsites.net/"}),
        ("c2", "browser_click", {"selector": "#start"}),
        ("c3", "browser_wait_for", {"selector": "#tableSandbox tr", "state": "visible"}),
    ]
    bedrock_script = [
        tool_use_response(tool_uses=[open_calls[0]]),
        tool_use_response(tool_uses=[open_calls[1]]),
        tool_use_response(tool_uses=[open_calls[2]]),
        text_response("ready"),
        # read_table: agent walks pagination. First reads the paginate
        # strip to learn page count (1), then reads the single page of rows.
        tool_use_response(
            tool_uses=[
                ("rp", "browser_read_text", {"selector": "#tableSandbox_paginate"}),
            ]
        ),
        tool_use_response(tool_uses=[("r1", "browser_read_table", {"selector": "#tableSandbox"})]),
        text_response(
            json.dumps(
                [
                    {"id": "1", "due_date": "2024-05-20", "invoice_url": "/invoices/1.jpg"},
                    {"id": "2", "due_date": "2026-12-31", "invoice_url": "/invoices/2.jpg"},
                    {"id": "3", "due_date": "2024-08-01", "invoice_url": "/invoices/3.jpg"},
                ]
            )
        ),
        # extract_invoices: per kept row (1 + 3 — row 2 is dropped by filter),
        # fetch the JPG URL then ocr, then emit final JSON.
        tool_use_response(tool_uses=[("d1", "browser_fetch_url", {"url": "https://rpa/1.jpg"})]),
        tool_use_response(
            tool_uses=[
                ("o1", "image_ocr", {"filepath": str(browser.download_dir / "1.jpg")}),
            ]
        ),
        tool_use_response(tool_uses=[("d3", "browser_fetch_url", {"url": "https://rpa/3.jpg"})]),
        tool_use_response(
            tool_uses=[
                ("o3", "image_ocr", {"filepath": str(browser.download_dir / "3.jpg")}),
            ]
        ),
        text_response(
            json.dumps(
                [
                    {
                        "ID": "1",
                        "DueDate": "20-05-2024",
                        "InvoiceNo": "INV-001",
                        "InvoiceDate": "15-05-2024",
                        "CompanyName": "Acme Corp",
                        "TotalDue": "123.45",
                    },
                    {
                        "ID": "3",
                        "DueDate": "01-08-2024",
                        "InvoiceNo": "INV-003",
                        "InvoiceDate": "28-07-2024",
                        "CompanyName": "Globex LLC",
                        "TotalDue": "4567.89",
                    },
                ]
            )
        ),
        # submit: upload, click submit, screenshot, final text response
        tool_use_response(
            tool_uses=[
                (
                    "u1",
                    "browser_upload_file",
                    {"selector": "#csv", "file_path": "/tmp/rpa-challenge-output.csv"},
                )
            ]
        ),
        tool_use_response(tool_uses=[("c4", "browser_submit_form", {"selector": "#submit form"})]),
        tool_use_response(
            tool_uses=[
                (
                    "s1",
                    "browser_screenshot",
                    {"path": str(tmp_path / "result.png"), "full_page": True},
                )
            ]
        ),
        text_response(str(tmp_path / "result.png")),
    ]

    definition = load_definition_from_yaml((EXAMPLE_DIR / "workflow.yaml").read_text())

    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog(
            [
                BrowserNavigateTool(),
                BrowserClickTool(),
                BrowserWaitForTool(),
                BrowserReadTableTool(),
                BrowserReadTextTool(),
                BrowserFetchUrlTool(),
                BrowserUploadFileTool(),
                BrowserSubmitFormTool(),
                BrowserScreenshotTool(),
                ocr,
            ]
        ),
        bedrock=FakeBedrock(bedrock_script),
        world=real_world(),
        browser_connector_factory=lambda: browser,
    )

    instance = await engine.run(definition)

    # --- The big assertion: workflow ran clean through all 6 steps ---
    assert instance.state == WorkflowInstanceState.COMPLETED, instance.error
    step_outputs = instance.context["steps"]
    assert set(step_outputs) == {
        "open_challenge",
        "read_table",
        "filter_overdue",
        "extract_invoices",
        "build_csv",
        "submit",
    }

    # filter_overdue produced exactly the rows whose due_date is on/before today
    filter_out = step_outputs["filter_overdue"]
    assert filter_out["kept_count"] == 2
    assert filter_out["dropped_count"] == 1
    assert filter_out["unparseable_count"] == 0
    kept_ids = [r["id"] for r in filter_out["kept_rows"]]
    assert kept_ids == ["1", "3"]

    # build_csv wrote the expected CSV
    csv_out = step_outputs["build_csv"]
    assert csv_out["row_count"] == 2
    assert csv_out["column_count"] == 6
    csv_text = Path("/tmp/rpa-challenge-output.csv").read_text()
    assert csv_text.splitlines()[0] == ("ID,DueDate,InvoiceNo,InvoiceDate,CompanyName,TotalDue")
    assert "INV-001" in csv_text
    assert "INV-003" in csv_text

    # The browser connector saw the right tool calls
    assert browser.navigate_calls == ["https://rpachallengeocr.azurewebsites.net/"]
    assert browser.uploaded_files == [("#csv", "/tmp/rpa-challenge-output.csv")]

    # Lifecycle: connector entered once and exited once
    assert browser.entered == 1
    assert browser.exited == 1

    # Audit log carries the lifecycle entries
    audit = await repos.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert "connector_opened" in actions
    assert "connector_closed" in actions


# ---------- Sanity: stock functions are wired into default registry ----------


def test_default_function_registry_has_browser_workflow_helpers() -> None:
    reg = default_function_registry()
    assert reg.get("filter_rows_by_date") is not None
    assert reg.get("write_csv") is not None


def test_default_function_registry_does_not_register_image_ocr() -> None:
    """image_ocr is a tool (agent-callable), not a deterministic function."""
    reg = default_function_registry()
    assert reg.get("image_ocr") is None
