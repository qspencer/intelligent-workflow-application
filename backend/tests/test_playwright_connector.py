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

from tests._browser_fakes import FakeDownload, FakePlaywrightPage
from workflow_platform.connectors.browser import (
    BrowserDownload,
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


async def test_read_table_resolves_relative_hrefs_against_page_url(tmp_path: Path) -> None:
    """D8a: cells containing `<a href="/relative">` come back with an
    absolute `*_href` field resolved against the page's current URL."""
    fake = FakePlaywrightPage(url="https://example.com/dashboard")
    fake.html_at["#t"] = """
        <thead><tr><th>ID</th><th>Invoice</th></tr></thead>
        <tbody>
            <tr><td>001</td><td><a href="/invoices/1.jpg"><span class="ico"></span></a></td></tr>
        </tbody>
    """
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        rows = await conn.read_table("#t")
    assert rows[0]["ID"] == "001"
    # The icon-anchor has no inner text — the URL is the only useful signal.
    assert rows[0]["Invoice"] == ""
    assert rows[0]["Invoice_href"] == "https://example.com/invoices/1.jpg"


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


# --- click ---


async def test_click_forwards_selector_and_default_timeout(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        await conn.click("#submit")
    click_call = next(kw for (m, kw) in fake.calls if m == "locator.click")
    assert click_call == {"selector": "#submit", "timeout": 5000}


async def test_click_honors_explicit_timeout(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        await conn.click("button.slow", timeout_ms=20000)
    click_call = next(kw for (m, kw) in fake.calls if m == "locator.click")
    assert click_call["timeout"] == 20000


async def test_click_propagates_locator_errors(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.raise_on_click["#missing"] = TimeoutError("not visible within 5s")
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        with pytest.raises(TimeoutError, match="not visible"):
            await conn.click("#missing")


# --- fill ---


async def test_fill_default_clear_first_uses_locator_fill(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        await conn.fill("#search", "hello")
    fill_call = next(kw for (m, kw) in fake.calls if m == "locator.fill")
    assert fill_call == {"selector": "#search", "value": "hello"}
    # press_sequentially should NOT have been called.
    assert not any(m == "locator.press_sequentially" for (m, _) in fake.calls)


async def test_fill_no_clear_uses_press_sequentially(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        await conn.fill("#field", "appended", clear_first=False)
    ps_call = next(kw for (m, kw) in fake.calls if m == "locator.press_sequentially")
    assert ps_call == {"selector": "#field", "value": "appended"}
    # locator.fill should NOT have been called.
    assert not any(m == "locator.fill" for (m, _) in fake.calls)


async def test_fill_propagates_errors(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.raise_on_fill["#readonly"] = ValueError("field is read-only")
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        with pytest.raises(ValueError, match="read-only"):
            await conn.fill("#readonly", "x")


# --- upload_file ---


async def test_upload_file_forwards_path_to_set_input_files(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    target_path = "/tmp/some-upload.csv"
    async with conn:
        await conn.upload_file("input[type='file']", target_path)
    upload_call = next(kw for (m, kw) in fake.calls if m == "locator.set_input_files")
    assert upload_call == {"selector": "input[type='file']", "files": target_path}


async def test_upload_file_propagates_errors(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.raise_on_set_files["#file"] = FileNotFoundError("no such file")
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        with pytest.raises(FileNotFoundError):
            await conn.upload_file("#file", "/missing")


# --- download_via_click ---


async def test_download_via_click_saves_under_per_run_dir(tmp_path: Path) -> None:
    """The canonical case: click a download link, capture the file under
    the connector's downloads_dir, return a BrowserDownload with the
    right source_url + suggested_filename + bytes."""
    fake = FakePlaywrightPage()
    fake.expect_download_value = FakeDownload(
        url="https://example.com/invoices/inv-001.pdf",
        suggested_filename="inv-001.pdf",
        content=b"%PDF-1.4 fake invoice bytes\n",
    )
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        result = await conn.download_via_click("a.download-link")

    assert isinstance(result, BrowserDownload)
    assert result.source_url == "https://example.com/invoices/inv-001.pdf"
    assert result.suggested_filename == "inv-001.pdf"
    assert result.local_path.endswith("/inv-001.pdf")
    assert result.local_path.startswith(str(tmp_path / "downloads"))
    assert result.bytes == len(b"%PDF-1.4 fake invoice bytes\n")

    # File actually on disk where claimed.
    saved = Path(result.local_path)
    assert saved.exists()
    assert saved.read_bytes() == b"%PDF-1.4 fake invoice bytes\n"

    # Order matters: expect_download must enter BEFORE the click; otherwise
    # we'd miss the download event. Verify the call sequence.
    methods_in_order = [m for (m, _) in fake.calls]
    expect_idx = methods_in_order.index("expect_download")
    click_idx = methods_in_order.index("locator.click")
    assert expect_idx < click_idx


async def test_download_via_click_honors_explicit_timeout(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.expect_download_value = FakeDownload()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        await conn.download_via_click("a", timeout_ms=60000)
    expect_call = next(kw for (m, kw) in fake.calls if m == "expect_download")
    assert expect_call["timeout"] == 60000
    # The click within the expect block also honors the same timeout.
    click_call = next(kw for (m, kw) in fake.calls if m == "locator.click")
    assert click_call["timeout"] == 60000


async def test_download_via_click_propagates_click_errors(tmp_path: Path) -> None:
    """If the click itself fails (e.g. element not found), the expect_download
    context unwinds cleanly and we see the original error."""
    fake = FakePlaywrightPage()
    fake.expect_download_value = FakeDownload()
    fake.raise_on_click["#missing"] = TimeoutError("not visible")
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        with pytest.raises(TimeoutError):
            await conn.download_via_click("#missing")


async def test_submit_form_calls_locator_evaluate(tmp_path: Path) -> None:
    """D8b: submit a form via Locator.evaluate('el => el.submit()') so
    we can complete UIs where the visible submit button is hidden /
    JS-gated."""
    fake = FakePlaywrightPage()
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        await conn.submit_form("#myform")
    eval_call = next(kw for (m, kw) in fake.calls if m == "locator.evaluate")
    assert eval_call["selector"] == "#myform"
    assert "submit" in eval_call["script"]


async def test_submit_form_propagates_errors(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.raise_on_evaluate["#bad"] = RuntimeError("form not found")
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        with pytest.raises(RuntimeError, match="form not found"):
            await conn.submit_form("#bad")


async def test_authenticate_is_noop(tmp_path: Path) -> None:
    """Browsers don't have a generic auth step. Sites that need login
    do it via the workflow's navigate + fill + click sequence."""
    conn = _make_connector(tmp_path)
    async with conn:
        # No exception is the assertion — authenticate() returns None by
        # design for browsers (Phase 3 work makes it a real op).
        await conn.authenticate()


# --- D8a: fetch_url ---


async def test_fetch_url_writes_body_to_downloads_dir(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.context.request.body_at["https://example.com/invoices/5.jpg"] = b"\xff\xd8fake-jpg"
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        download = await conn.fetch_url("https://example.com/invoices/5.jpg")
    assert download.source_url == "https://example.com/invoices/5.jpg"
    assert download.suggested_filename == "5.jpg"
    assert download.bytes == len(b"\xff\xd8fake-jpg")
    saved = Path(download.local_path)
    assert saved.read_bytes() == b"\xff\xd8fake-jpg"
    assert saved.parent == (tmp_path / "downloads")
    assert fake.context.request.gets == ["https://example.com/invoices/5.jpg"]


async def test_fetch_url_honors_explicit_dest_filename(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.context.request.body_at["https://example.com/file"] = b"hello"
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        download = await conn.fetch_url("https://example.com/file", dest_filename="renamed.bin")
    assert download.suggested_filename == "renamed.bin"
    assert Path(download.local_path).name == "renamed.bin"


async def test_fetch_url_falls_back_to_default_name_for_bare_paths(tmp_path: Path) -> None:
    """A URL ending with `/` (no filename segment) — we default to `download`."""
    fake = FakePlaywrightPage()
    fake.context.request.body_at["https://example.com/"] = b"x"
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        download = await conn.fetch_url("https://example.com/")
    assert download.suggested_filename == "download"


async def test_fetch_url_strips_query_string_from_default_name(tmp_path: Path) -> None:
    """`...image.jpg?token=abc` should save as `image.jpg`, not `image.jpg?token=abc`."""
    fake = FakePlaywrightPage()
    fake.context.request.body_at["https://example.com/i/x.jpg?token=abc"] = b"x"
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        download = await conn.fetch_url("https://example.com/i/x.jpg?token=abc")
    assert download.suggested_filename == "x.jpg"


async def test_fetch_url_propagates_request_errors(tmp_path: Path) -> None:
    fake = FakePlaywrightPage()
    fake.context.request.raise_on_get["https://example.com/dead"] = RuntimeError("502")
    conn = _make_connector(tmp_path, page=fake)
    async with conn:
        with pytest.raises(RuntimeError, match="502"):
            await conn.fetch_url("https://example.com/dead")


async def test_fetch_url_creates_nested_subdir_if_needed(tmp_path: Path) -> None:
    """downloads_dir might not exist yet inside __aenter__ (it's mkdir-d
    in lifecycle). Re-asserting here that the parent dir is created."""
    fake = FakePlaywrightPage()
    fake.context.request.body_at["https://example.com/file.bin"] = b"data"
    conn = PlaywrightConnector(downloads_dir=tmp_path / "nested" / "dir", page=fake)
    async with conn:
        download = await conn.fetch_url("https://example.com/file.bin")
    assert Path(download.local_path).exists()
