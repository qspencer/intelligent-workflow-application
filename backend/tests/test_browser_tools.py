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

from tests._browser_fakes import FakePlaywrightPage
from workflow_platform.connectors.browser import PlaywrightConnector
from workflow_platform.tools import (
    BrowserReadTableTool,
    BrowserReadTextTool,
    BrowserScreenshotTool,
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
        (BrowserReadTextTool, "browser_read_text"),
        (BrowserReadTableTool, "browser_read_table"),
        (BrowserWaitForTool, "browser_wait_for"),
        (BrowserScreenshotTool, "browser_screenshot"),
    ],
)
def test_browser_tool_names_match_plan(
    tool_cls: type[Any], expected_name: str
) -> None:
    """Names are the contract — they appear in workflow YAMLs' `tools:`
    lists and capability allowlists. Don't change without coordinating."""
    assert tool_cls.name == expected_name


def test_browser_tools_have_required_schema_fields() -> None:
    """Every tool's `parameters_schema` is a valid JSON-schema-like dict
    with `type: object` and either `required` or all-optional fields."""
    for tool_cls in (
        BrowserReadTextTool,
        BrowserReadTableTool,
        BrowserWaitForTool,
        BrowserScreenshotTool,
    ):
        schema: dict[str, Any] = tool_cls.parameters_schema
        assert schema["type"] == "object"
        assert "properties" in schema


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
