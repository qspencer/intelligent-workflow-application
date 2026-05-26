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
