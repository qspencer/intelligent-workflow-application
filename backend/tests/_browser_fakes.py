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


class FakePlaywrightPage:
    """Stand-in for `playwright.async_api.Page` with the surface we use.

    Tests construct it, optionally pre-stage `url` and `text_at` / `inner_text`
    behavior, then pass it to `PlaywrightConnector(page=<fake>)`. Calls
    are recorded in `self.calls` as `(method, kwargs)` tuples.
    """

    def __init__(self, *, url: str = "about:blank") -> None:
        self._url = url
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # Per-selector responses for read_text and similar (filled in D3+).
        self.text_at: dict[str, str] = {}
        # Per-selector behavior overrides (e.g. raise on click).
        self.raise_on: dict[str, Exception] = {}

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
