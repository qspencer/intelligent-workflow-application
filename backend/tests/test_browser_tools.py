"""Tests for the D3 browser tools — `BrowserReadTextTool`,
`BrowserReadTableTool`, `BrowserWaitForTool`, `BrowserScreenshotTool`.

Tools look up the per-run BrowserConnector from `ToolContext.connectors["browser"]`.
Tests construct a real `PlaywrightConnector` injected with a `FakePlaywrightPage`
and pass it through `ToolContext.connectors`. No real chromium is launched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests._browser_fakes import FakeDownload, FakePlaywrightPage
from workflow_platform.connectors.browser import PlaywrightConnector
from workflow_platform.tools import (
    BrowserClickTool,
    BrowserDownloadTool,
    BrowserFillTool,
    BrowserNavigateTool,
    BrowserReadTableTool,
    BrowserReadTextTool,
    BrowserScreenshotTool,
    BrowserUploadFileTool,
    BrowserWaitForTool,
    ToolContext,
)


async def _ctx_with_browser(
    tmp_path: Path, fake: FakePlaywrightPage
) -> tuple[ToolContext, PlaywrightConnector]:
    """Build a ToolContext wired to a freshly-entered PlaywrightConnector."""
    conn = PlaywrightConnector(downloads_dir=tmp_path / "downloads", page=fake)
    await conn.__aenter__()
    ctx = ToolContext(connectors={"browser": conn})
    return ctx, conn


# ---------- BrowserNavigateTool ----------


async def test_navigate_tool_calls_page_goto(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserNavigateTool().execute(
            {"url": "https://example.com/landing"}, context=ctx
        )
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    assert result.content == {"url": "https://example.com/landing", "wait_until": "load"}
    assert ("goto", {"url": "https://example.com/landing", "wait_until": "load"}) in fake.calls


async def test_navigate_tool_respects_wait_until_override(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserNavigateTool().execute(
            {"url": "https://example.com/spa", "wait_until": "networkidle"}, context=ctx
        )
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    assert ("goto", {"url": "https://example.com/spa", "wait_until": "networkidle"}) in fake.calls


async def test_navigate_tool_requires_url() -> None:
    result = await BrowserNavigateTool().execute({}, context=ToolContext())
    assert not result.ok
    assert "url" in (result.error or "").lower()


async def test_navigate_tool_rejects_invalid_wait_until(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserNavigateTool().execute(
            {"url": "https://example.com", "wait_until": "bogus"}, context=ctx
        )
    finally:
        await conn.__aexit__(None, None, None)
    assert not result.ok
    assert "wait_until" in (result.error or "").lower()


async def test_navigate_tool_errors_without_connector() -> None:
    result = await BrowserNavigateTool().execute(
        {"url": "https://example.com"}, context=ToolContext()
    )
    assert not result.ok
    assert "browser connector" in (result.error or "").lower()


# ---------- BrowserReadTextTool ----------


async def test_read_text_tool_returns_text_from_connector(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.text_at["#title"] = "Hello"
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserReadTextTool().execute({"selector": "#title"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    assert result.content == {"selector": "#title", "text": "Hello"}


async def test_read_text_tool_requires_selector() -> None:
    result = await BrowserReadTextTool().execute({}, context=ToolContext())
    assert not result.ok
    assert "selector" in (result.error or "").lower()


async def test_read_text_tool_errors_without_connector() -> None:
    """No `connectors["browser"]` populated — clear error pointing at config."""
    result = await BrowserReadTextTool().execute({"selector": "#x"}, context=ToolContext())
    assert not result.ok
    assert result.error is not None
    assert "browser connector" in result.error.lower()


async def test_read_text_tool_surfaces_connector_exceptions(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.raise_on_text["#timeout"] = TimeoutError("locator timeout")
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserReadTextTool().execute({"selector": "#timeout"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert not result.ok
    assert "timeout" in (result.error or "").lower()


# ---------- BrowserReadTableTool ----------


async def test_read_table_tool_returns_rows_and_count(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.html_at["#t"] = (
        "<thead><tr><th>A</th><th>B</th></tr></thead>"
        "<tbody><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></tbody>"
    )
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserReadTableTool().execute({"selector": "#t"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    content = result.content
    assert content["selector"] == "#t"
    assert content["row_count"] == 2
    assert content["rows"] == [{"A": "1", "B": "2"}, {"A": "3", "B": "4"}]


async def test_read_table_tool_empty_table_returns_zero_rows(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.html_at["#t"] = "<thead><tr><th>X</th></tr></thead><tbody></tbody>"
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserReadTableTool().execute({"selector": "#t"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    assert result.content["row_count"] == 0
    assert result.content["rows"] == []


# ---------- BrowserWaitForTool ----------


async def test_wait_for_tool_default_state_visible_default_timeout(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserWaitForTool().execute({"selector": "#row"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    wait_call = next(kw for (m, kw) in fake.calls if m == "locator.wait_for")
    assert wait_call == {"selector": "#row", "state": "visible", "timeout": 5000}


async def test_wait_for_tool_honors_explicit_state(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserWaitForTool().execute(
            {"selector": "#spinner", "state": "hidden", "timeout_ms": 10000},
            context=ctx,
        )
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    wait_call = next(kw for (m, kw) in fake.calls if m == "locator.wait_for")
    assert wait_call["state"] == "hidden"
    assert wait_call["timeout"] == 10000


async def test_wait_for_tool_rejects_invalid_state(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserWaitForTool().execute(
            {"selector": "#x", "state": "loaded"}, context=ctx
        )
    finally:
        await conn.__aexit__(None, None, None)
    assert not result.ok
    assert "state" in (result.error or "").lower()


async def test_wait_for_tool_rejects_tiny_timeout(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserWaitForTool().execute(
            {"selector": "#x", "timeout_ms": 50}, context=ctx
        )
    finally:
        await conn.__aexit__(None, None, None)
    assert not result.ok
    assert "timeout_ms" in (result.error or "").lower()


# ---------- BrowserScreenshotTool ----------


async def test_screenshot_tool_default_path(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserScreenshotTool().execute({}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    assert result.content["local_path"].startswith(str(tmp_path / "downloads"))
    assert result.content["bytes"] > 0
    assert result.content["full_page"] is False


async def test_screenshot_tool_explicit_path(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    target = str(tmp_path / "explicit-shot.png")
    try:
        result = await BrowserScreenshotTool().execute(
            {"path": target, "full_page": True}, context=ctx
        )
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    assert result.content["local_path"] == target
    assert result.content["full_page"] is True


async def test_screenshot_tool_rejects_non_bool_full_page(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserScreenshotTool().execute({"full_page": "yes"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert not result.ok


# ---------- Tool metadata + name discipline ----------


@pytest.mark.parametrize(
    "tool_cls,expected_name",
    [
        (BrowserNavigateTool, "browser_navigate"),
        (BrowserReadTextTool, "browser_read_text"),
        (BrowserReadTableTool, "browser_read_table"),
        (BrowserWaitForTool, "browser_wait_for"),
        (BrowserScreenshotTool, "browser_screenshot"),
        (BrowserClickTool, "browser_click"),
        (BrowserFillTool, "browser_fill"),
        (BrowserUploadFileTool, "browser_upload_file"),
        (BrowserDownloadTool, "browser_download"),
    ],
)
def test_browser_tool_names_match_plan(tool_cls: type[Any], expected_name: str) -> None:
    """Names are the contract — they appear in workflow YAMLs' `tools:`
    lists and capability allowlists. Don't change without coordinating."""
    assert tool_cls.name == expected_name


def test_browser_tools_have_required_schema_fields() -> None:
    """Every tool's `parameters_schema` is a valid JSON-schema-like dict
    with `type: object` and either `required` or all-optional fields."""
    for tool_cls in (
        BrowserNavigateTool,
        BrowserReadTextTool,
        BrowserReadTableTool,
        BrowserWaitForTool,
        BrowserScreenshotTool,
        BrowserClickTool,
        BrowserFillTool,
        BrowserUploadFileTool,
        BrowserDownloadTool,
    ):
        schema: dict[str, Any] = tool_cls.parameters_schema
        assert schema["type"] == "object"
        assert "properties" in schema


# ---------- BrowserClickTool ----------


async def test_click_tool_happy_path(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserClickTool().execute({"selector": "#submit"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    assert result.content == {"selector": "#submit", "clicked": True}
    click_call = next(kw for (m, kw) in fake.calls if m == "locator.click")
    assert click_call == {"selector": "#submit", "timeout": 5000}


async def test_click_tool_explicit_timeout(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserClickTool().execute(
            {"selector": "#submit", "timeout_ms": 15000}, context=ctx
        )
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    click_call = next(kw for (m, kw) in fake.calls if m == "locator.click")
    assert click_call["timeout"] == 15000


async def test_click_tool_surfaces_locator_errors(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.raise_on_click["#missing"] = TimeoutError("not visible")
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserClickTool().execute({"selector": "#missing"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert not result.ok
    assert "not visible" in (result.error or "")


async def test_click_tool_rejects_tiny_timeout(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserClickTool().execute({"selector": "#x", "timeout_ms": 50}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert not result.ok


# ---------- BrowserFillTool ----------


async def test_fill_tool_happy_path_clears_first_by_default(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserFillTool().execute(
            {"selector": "#search", "value": "hello world"}, context=ctx
        )
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    assert result.content == {"selector": "#search", "value_len": 11, "cleared": True}
    fill_call = next(kw for (m, kw) in fake.calls if m == "locator.fill")
    assert fill_call == {"selector": "#search", "value": "hello world"}


async def test_fill_tool_clear_first_false(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserFillTool().execute(
            {"selector": "#field", "value": "tail", "clear_first": False}, context=ctx
        )
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    assert result.content["cleared"] is False
    # Verifies the press_sequentially path was taken, not the fill path.
    assert any(m == "locator.press_sequentially" for (m, _) in fake.calls)
    assert not any(m == "locator.fill" for (m, _) in fake.calls)


async def test_fill_tool_requires_value(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserFillTool().execute({"selector": "#x"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert not result.ok
    assert "value" in (result.error or "").lower()


# ---------- BrowserUploadFileTool ----------


async def test_upload_file_tool_happy_path(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    target_csv = str(tmp_path / "upload.csv")
    try:
        result = await BrowserUploadFileTool().execute(
            {"selector": "input[type='file']", "file_path": target_csv}, context=ctx
        )
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    assert result.content == {"selector": "input[type='file']", "file_path": target_csv}
    upload_call = next(kw for (m, kw) in fake.calls if m == "locator.set_input_files")
    assert upload_call["files"] == target_csv


async def test_upload_file_tool_requires_file_path(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserUploadFileTool().execute({"selector": "#file"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert not result.ok
    assert "file_path" in (result.error or "").lower()


# ---------- BrowserDownloadTool ----------


async def test_download_tool_happy_path(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.expect_download_value = FakeDownload(
        url="https://example.com/inv.pdf",
        suggested_filename="inv-1.pdf",
        content=b"PDF bytes",
    )
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserDownloadTool().execute({"selector": "a.download"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert result.ok
    content = result.content
    assert content["selector"] == "a.download"
    assert content["suggested_filename"] == "inv-1.pdf"
    assert content["source_url"] == "https://example.com/inv.pdf"
    assert content["bytes"] == len(b"PDF bytes")
    assert content["local_path"].endswith("/inv-1.pdf")
    assert Path(content["local_path"]).read_bytes() == b"PDF bytes"


async def test_download_tool_default_timeout_is_30s(tmp_path: Path) -> None:
    """30s is generous by design — downloads commonly take longer than
    clicks. The plan documents this default."""
    fake = FakePlaywrightPage()
    fake.expect_download_value = FakeDownload()
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        await BrowserDownloadTool().execute({"selector": "a"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    expect_call = next(kw for (m, kw) in fake.calls if m == "expect_download")
    assert expect_call["timeout"] == 30000


async def test_download_tool_propagates_errors(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.expect_download_value = FakeDownload()
    fake.raise_on_click["#missing"] = TimeoutError("element gone")
    ctx, conn = await _ctx_with_browser(tmp_path, fake)
    try:
        result = await BrowserDownloadTool().execute({"selector": "#missing"}, context=ctx)
    finally:
        await conn.__aexit__(None, None, None)
    assert not result.ok
    assert "element gone" in (result.error or "")


def test_browser_tools_exported_from_package_init() -> None:
    """Importing from workflow_platform.tools at the top level works
    (canonical import path)."""
    from workflow_platform.tools import (
        BrowserReadTableTool,
        BrowserReadTextTool,
        BrowserScreenshotTool,
        BrowserWaitForTool,
    )

    assert BrowserReadTextTool.name == "browser_read_text"
    assert BrowserReadTableTool.name == "browser_read_table"
    assert BrowserWaitForTool.name == "browser_wait_for"
    assert BrowserScreenshotTool.name == "browser_screenshot"
