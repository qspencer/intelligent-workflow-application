"""Shared test fakes for the browser connector.

Mirrors the `tests/_bedrock_fakes.py` and `tests/_email_fakes.py`
patterns: importable from any test that needs to exercise browser-
touching code without launching a real Chromium process.

`FakePlaywrightPage` records every method call against it so tests can
assert the connector invoked Playwright with the right arguments. Each
test pre-stages whatever return values it needs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class FakeLocator:
    """Stand-in for `playwright.async_api.Locator`. Per-selector state
    lives on the parent FakePlaywrightPage so multiple locator() calls
    against the same selector return the same staged values."""

    def __init__(self, page: FakePlaywrightPage, selector: str) -> None:
        self._page = page
        self._selector = selector

    async def inner_text(self) -> str:
        self._page.calls.append(("locator.inner_text", {"selector": self._selector}))
        if self._selector in self._page.raise_on_text:
            raise self._page.raise_on_text[self._selector]
        return self._page.text_at.get(self._selector, "")

    async def inner_html(self) -> str:
        self._page.calls.append(("locator.inner_html", {"selector": self._selector}))
        if self._selector in self._page.raise_on_html:
            raise self._page.raise_on_html[self._selector]
        return self._page.html_at.get(self._selector, "")

    async def wait_for(self, *, state: str = "visible", timeout: int = 5000) -> None:
        self._page.calls.append(
            ("locator.wait_for", {"selector": self._selector, "state": state, "timeout": timeout})
        )
        if self._selector in self._page.raise_on_wait:
            raise self._page.raise_on_wait[self._selector]

    @property
    def first(self) -> FakeLocator:
        """Stand-in for Playwright's `Locator.first` property — the
        connector uses this on `wait_for` to defuse strict-mode multi-match
        errors. Tests don't need to differentiate; return self."""
        return self

    async def click(self, *, timeout: int = 5000) -> None:
        self._page.calls.append(("locator.click", {"selector": self._selector, "timeout": timeout}))
        if self._selector in self._page.raise_on_click:
            raise self._page.raise_on_click[self._selector]

    async def fill(self, value: str) -> None:
        self._page.calls.append(("locator.fill", {"selector": self._selector, "value": value}))
        if self._selector in self._page.raise_on_fill:
            raise self._page.raise_on_fill[self._selector]

    async def press_sequentially(self, value: str) -> None:
        self._page.calls.append(
            ("locator.press_sequentially", {"selector": self._selector, "value": value})
        )

    async def set_input_files(self, files: str) -> None:
        self._page.calls.append(
            ("locator.set_input_files", {"selector": self._selector, "files": files})
        )
        if self._selector in self._page.raise_on_set_files:
            raise self._page.raise_on_set_files[self._selector]

    async def evaluate(self, script: str) -> Any:
        """Stand-in for Playwright's `Locator.evaluate(script)` — runs JS
        with the matched element as the first argument. Tests can pre-stage
        `raise_on_evaluate[selector]` to simulate failures."""
        self._page.calls.append(
            ("locator.evaluate", {"selector": self._selector, "script": script})
        )
        if self._selector in self._page.raise_on_evaluate:
            raise self._page.raise_on_evaluate[self._selector]
        return None


class FakeDownload:
    """Stand-in for `playwright.async_api.Download`. `save_as` writes
    the staged `content` bytes to the requested path so subsequent
    `Path(path).stat()` calls in the connector return real byte counts."""

    def __init__(
        self,
        *,
        url: str = "https://example.com/file.pdf",
        suggested_filename: str = "file.pdf",
        content: bytes = b"fake download content",
    ) -> None:
        self.url = url
        self.suggested_filename = suggested_filename
        self._content = content

    async def save_as(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(self._content)


class FakeEventInfo:
    """Stand-in for Playwright's `EventInfo`. `.value` returns an awaitable
    that resolves to the wrapped object — matches Playwright's
    `async with page.expect_download() as info: dl = await info.value`."""

    def __init__(self, value: Any) -> None:
        self._value = value

    @property
    def value(self) -> Any:
        async def _resolve() -> Any:
            return self._value

        return _resolve()


class FakeExpectDownload:
    """Async context manager returned by `FakePlaywrightPage.expect_download`."""

    def __init__(self, download: FakeDownload, timeout: int) -> None:
        self._info = FakeEventInfo(download)
        self.timeout = timeout

    async def __aenter__(self) -> FakeEventInfo:
        return self._info

    async def __aexit__(self, *args: Any) -> None:
        pass


class FakeAPIResponse:
    """Stand-in for Playwright's `APIResponse`. `body()` returns staged bytes."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    async def body(self) -> bytes:
        return self._body


class FakeRequest:
    """Stand-in for `BrowserContext.request` (`APIRequestContext`).
    Records every `get` and returns staged bytes per URL."""

    def __init__(self) -> None:
        self.gets: list[str] = []
        self.body_at: dict[str, bytes] = {}
        self.raise_on_get: dict[str, Exception] = {}

    async def get(self, url: str) -> FakeAPIResponse:
        self.gets.append(url)
        if url in self.raise_on_get:
            raise self.raise_on_get[url]
        return FakeAPIResponse(self.body_at.get(url, b""))


class FakeBrowserContext:
    """Stand-in for `BrowserContext`. The connector accesses
    `page.context.request.get(url)` for `fetch_url`."""

    def __init__(self) -> None:
        self.request = FakeRequest()


class FakePlaywrightPage:
    """Stand-in for `playwright.async_api.Page` with the surface we use.

    Tests construct it, optionally pre-stage `url` / `text_at` / `html_at`
    / `raise_on_*` behavior, then pass it to
    `PlaywrightConnector(page=<fake>)`. Calls are recorded in `self.calls`
    as `(method, kwargs)` tuples.
    """

    def __init__(self, *, url: str = "about:blank") -> None:
        self._url = url
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # Per-selector staged values.
        self.text_at: dict[str, str] = {}
        self.html_at: dict[str, str] = {}
        # Per-selector exceptions to raise instead of returning a value.
        self.raise_on_text: dict[str, Exception] = {}
        self.raise_on_html: dict[str, Exception] = {}
        self.raise_on_wait: dict[str, Exception] = {}
        self.raise_on_click: dict[str, Exception] = {}
        self.raise_on_fill: dict[str, Exception] = {}
        self.raise_on_set_files: dict[str, Exception] = {}
        self.raise_on_evaluate: dict[str, Exception] = {}
        # The FakeDownload that expect_download() should yield. Tests
        # stage this before the connector's download_via_click runs.
        self.expect_download_value: FakeDownload | None = None
        # Browser context for fetch_url. Exposes a FakeRequest at
        # `.context.request` matching Playwright's API surface.
        self.context = FakeBrowserContext()

    @property
    def url(self) -> str:
        return self._url

    async def goto(self, url: str, *, wait_until: str = "load") -> None:
        self.calls.append(("goto", {"url": url, "wait_until": wait_until}))
        self._url = url

    async def screenshot(self, *, path: str, full_page: bool = False) -> bytes:
        self.calls.append(("screenshot", {"path": path, "full_page": full_page}))
        # Write a tiny PNG-shaped file so the connector's stat() succeeds.
        # (Not a valid PNG, but enough to be a non-empty file on disk.)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-screenshot")
        return b""

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def expect_download(self, *, timeout: int = 30000) -> FakeExpectDownload:
        self.calls.append(("expect_download", {"timeout": timeout}))
        if self.expect_download_value is None:
            raise RuntimeError(
                "FakePlaywrightPage.expect_download_value not staged — tests "
                "must set this before triggering download_via_click()"
            )
        return FakeExpectDownload(self.expect_download_value, timeout)

    async def close(self) -> None:
        self.calls.append(("close", {}))


class FakeAsyncContextManager:
    """Async-context-manager wrapper for a fake `playwright` object, for
    the few tests that exercise the full lifecycle. Not needed for tests
    that inject `page=` directly into PlaywrightConnector."""

    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *args: Any) -> None:
        pass
