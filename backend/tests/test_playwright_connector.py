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


# --- D3/D4 stubs: confirm they raise so the punch list is explicit ---


@pytest.mark.parametrize(
    "method_name,args,kwargs",
    [
        ("click", ("#submit",), {}),
        ("fill", ("#input", "value"), {}),
        ("read_text", ("p",), {}),
        ("read_table", ("table",), {}),
        ("upload_file", ("#file", "/tmp/x"), {}),
        ("download_via_click", ("a",), {}),
        ("wait_for", ("p",), {}),
    ],
)
async def test_unimplemented_methods_raise(
    tmp_path: Path, method_name: str, args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    """Pins the D3/D4 punch list. When each of these is implemented, the
    corresponding parametrize row should be deleted and a real test added
    in test_browser_tools.py or similar."""
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
