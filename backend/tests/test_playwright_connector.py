"""Tests for `PlaywrightConnector` — lifecycle + the D2 method surface
(navigate, health_check, screenshot). The other 7 abstract methods are
covered as "raises NotImplementedError" so D3/D4 has a clear punch list.

Live integration against real Chromium lives at `test_browser_live.py`
behind `BROWSER_LIVE=1` (lands D7a). These tests use the FakePlaywrightPage
from `_browser_fakes.py` and don't launch a browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._browser_fakes import FakePlaywrightPage
from workflow_platform.connectors.browser import (
    BrowserScreenshot,
    PlaywrightConnector,
)


def _make_connector(
    tmp_path: Path, *, page: FakePlaywrightPage | None = None
) -> PlaywrightConnector:
    """Standard construction for tests — inject a FakePlaywrightPage so the
    real Playwright launch chain is never touched."""
    fake = page if page is not None else FakePlaywrightPage()
    return PlaywrightConnector(downloads_dir=tmp_path / "downloads", page=fake)


# --- lifecycle ---


async def test_aenter_creates_downloads_dir(tmp_path: Path) -> None:
    """Even with a pre-injected page, __aenter__ should still mkdir the
    downloads dir so subsequent screenshot() / download_via_click() calls
    have somewhere to land."""
    downloads = tmp_path / "downloads"
    assert not downloads.exists()
    conn = PlaywrightConnector(downloads_dir=downloads, page=FakePlaywrightPage())
    async with conn:
        assert downloads.exists()
        assert downloads.is_dir()


async def test_aexit_is_noop_when_page_injected(tmp_path: Path) -> None:
    """When tests inject `page=`, __aexit__ should not try to close the
    page (we don't own it). This is what keeps the test fakes simple."""
    fake = FakePlaywrightPage()
    conn = PlaywrightConnector(downloads_dir=tmp_path, page=fake)
    async with conn:
        pass
    # FakePlaywrightPage records all calls. If aexit accidentally called
    # close(), we'd see it here.
    assert not any(call[0] == "close" for call in fake.calls)


async def test_default_viewport_and_headless(tmp_path: Path) -> None:
    """Defaults match what the plan documents."""
    conn = PlaywrightConnector(downloads_dir=tmp_path, page=FakePlaywrightPage())
    assert conn.headless is True
    assert conn.viewport == {"width": 1280, "height": 720}


# --- navigate ---


async def test_navigate_calls_page_goto_with_url_and_default_wait_until(
    tmp_path: Path,
) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        await conn.navigate("https://example.com")
    goto_call = next(kw for (m, kw) in fake.calls if m == "goto")
    assert goto_call == {"url": "https://example.com", "wait_until": "load"}


async def test_navigate_honors_explicit_wait_until(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        await conn.navigate("https://example.com", wait_until="networkidle")
    goto_call = next(kw for (m, kw) in fake.calls if m == "goto")
    assert goto_call["wait_until"] == "networkidle"


# --- health_check ---


async def test_health_check_returns_true_after_navigation(tmp_path: Path) -> None:
    fake = FakePlaywrightPage(url="about:blank")
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        await conn.navigate("https://example.com")
        assert await conn.health_check() is True


async def test_health_check_returns_false_when_page_raises(tmp_path: Path) -> None:
    """If accessing page.url raises (browser crashed, etc.), health_check
    should report unhealthy rather than propagate."""

    class _BadPage(FakePlaywrightPage):
        @property
        def url(self) -> str:
            raise RuntimeError("browser process died")

    fake = _BadPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        assert await conn.health_check() is False


async def test_health_check_returns_false_when_page_is_none() -> None:
    """An un-entered connector reports unhealthy — no live Playwright to ping."""
    conn = PlaywrightConnector(downloads_dir=Path("/tmp/x"), page=None)
    # __aenter__ NOT called; _page stays None.
    assert await conn.health_check() is False


# --- screenshot ---


async def test_screenshot_with_explicit_path(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        target = str(tmp_path / "explicit.png")
        result = await conn.screenshot(path=target, full_page=False)
    assert isinstance(result, BrowserScreenshot)
    assert result.local_path == target
    assert result.bytes > 0  # FakePage writes a non-empty stub PNG
    assert result.full_page is False
    # Confirm the connector forwarded the right args to page.screenshot.
    shot_call = next(kw for (m, kw) in fake.calls if m == "screenshot")
    assert shot_call == {"path": target, "full_page": False}


async def test_screenshot_default_path_lands_in_downloads_dir(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        result = await conn.screenshot()
    assert result.local_path.startswith(str(tmp_path / "downloads"))
    assert "screenshot-" in Path(result.local_path).name
    assert result.local_path.endswith(".png")
    assert result.bytes > 0


async def test_screenshot_full_page_flag_round_trips(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        result = await conn.screenshot(full_page=True)
    assert result.full_page is True
    shot_call = next(kw for (m, kw) in fake.calls if m == "screenshot")
    assert shot_call["full_page"] is True


# --- read_text ---


async def test_read_text_returns_text_at_selector(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.text_at["#title"] = "Hello World"
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        text = await conn.read_text("#title")
    assert text == "Hello World"
    inner_text_call = next(kw for (m, kw) in fake.calls if m == "locator.inner_text")
    assert inner_text_call["selector"] == "#title"


async def test_read_text_returns_empty_for_unstaged_selector(tmp_path: Path) -> None:
    """The fake returns empty string for unstaged selectors. Real Playwright
    would raise on a missing selector after timeout — that path is exercised
    via raise_on_text below."""
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        text = await conn.read_text("#missing")
    assert text == ""


async def test_read_text_propagates_locator_errors(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.raise_on_text["#timeout"] = TimeoutError("locator timeout")
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        with pytest.raises(TimeoutError, match="locator timeout"):
            await conn.read_text("#timeout")


# --- read_table ---


async def test_read_table_against_thead_tbody_template(tmp_path: Path) -> None:
    """The canonical case: DataTables-style table with <thead><tr><th>
    headers + <tbody><tr><td> rows."""
    fake = FakePlaywrightPage()
    fake.html_at["#tableSandbox"] = """
        <thead>
            <tr><th>ID</th><th>Due Date</th><th>Invoice</th></tr>
        </thead>
        <tbody>
            <tr><td>001</td><td>2024-01-15</td><td>inv-001.jpg</td></tr>
            <tr><td>002</td><td>2024-02-20</td><td>inv-002.jpg</td></tr>
        </tbody>
    """
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        rows = await conn.read_table("#tableSandbox")
    assert rows == [
        {"ID": "001", "Due Date": "2024-01-15", "Invoice": "inv-001.jpg"},
        {"ID": "002", "Due Date": "2024-02-20", "Invoice": "inv-002.jpg"},
    ]


async def test_read_table_empty(tmp_path: Path) -> None:
    """An empty <tbody> returns an empty list, not an error."""
    fake = FakePlaywrightPage()
    fake.html_at["table"] = "<thead><tr><th>X</th></tr></thead><tbody></tbody>"
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        rows = await conn.read_table("table")
    assert rows == []


# --- wait_for ---


async def test_wait_for_default_state_visible_default_timeout(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        await conn.wait_for("#tableSandbox tbody tr")
    wait_call = next(kw for (m, kw) in fake.calls if m == "locator.wait_for")
    assert wait_call == {"selector": "#tableSandbox tbody tr", "state": "visible", "timeout": 5000}


async def test_wait_for_honors_explicit_state_and_timeout(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        await conn.wait_for("#spinner", state="hidden", timeout_ms=15000)
    wait_call = next(kw for (m, kw) in fake.calls if m == "locator.wait_for")
    assert wait_call["state"] == "hidden"
    assert wait_call["timeout"] == 15000


async def test_wait_for_propagates_timeout_errors(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.raise_on_wait["#never"] = TimeoutError("not visible within 5s")
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        with pytest.raises(TimeoutError):
            await conn.wait_for("#never")


# --- D4 stubs: confirm they still raise so the punch list is explicit ---


@pytest.mark.parametrize(
    "method_name,args,kwargs",
    [
        ("click", ("#submit",), {}),
        ("fill", ("#input", "value"), {}),
        ("upload_file", ("#file", "/tmp/x"), {}),
        ("download_via_click", ("a",), {}),
    ],
)
async def test_unimplemented_methods_raise(
    tmp_path: Path, method_name: str, args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    """Pins the D4 punch list. When each of these is implemented, the
    corresponding parametrize row should be deleted and a real test
    added in test_browser_tools.py or here."""
    conn = _make_connector(tmp_path)
    async with conn:
        method = getattr(conn, method_name)
        with pytest.raises(NotImplementedError):
            await method(*args, **kwargs)


async def test_authenticate_is_noop(tmp_path: Path) -> None:
    """Browsers don't have a generic auth step. Sites that need login
    do it via the workflow's navigate + fill + click sequence."""
    conn = _make_connector(tmp_path)
    async with conn:
        # No exception is the assertion — authenticate() returns None by
        # design for browsers (Phase 3 work makes it a real op).
        await conn.authenticate()
