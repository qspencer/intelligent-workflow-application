"""BrowserConnector — provider-agnostic browser-automation ABC.

Sits between `Connector` (the generic connector interface) and concrete
backend implementations (`PlaywrightConnector` for v1; future
`SeleniumConnector`, `CdpConnector`). Subclasses implement the typed
abstract methods; the inherited `Connector` methods (`trigger_poll`,
`send`, `query`, `health_check`) get default impls here that make sense
for an interactive backend rather than a polling source.

Lifecycle: one `BrowserConnector` instance lives for the duration of one
workflow run — distinct from email connectors which live for the
process. The engine lazy-constructs this when a workflow definition
references any `browser_*` tool, and tears it down in the run's `finally`
block. See `docs/BROWSER_CONNECTOR_PLAN.md` for the rationale.

Selector auto-detection: CSS is the default; selectors starting with `/`
or `//` are treated as XPath. Subclasses are free to honor an explicit
`type` field on `BrowserSelector` if they need to disambiguate the rare
collision case.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from workflow_platform.connectors.base import Connector
from workflow_platform.connectors.browser.models import (
    BrowserDownload,
    BrowserScreenshot,
    SelectorType,
    WaitState,
)


def detect_selector_type(selector: str) -> SelectorType:
    """Auto-detect CSS vs XPath. XPath starts with `/` or `//`; everything
    else is CSS. Callers can override via an explicit `BrowserSelector.type`."""
    return "xpath" if selector.startswith("/") else "css"


class BrowserConnector(Connector):
    type: ClassVar[str]

    # --- abstract: subclasses must implement ---

    @abstractmethod
    async def navigate(self, url: str, *, wait_until: str = "load") -> None:
        """Load `url`. `wait_until` is the Playwright-style condition for
        deciding the navigation is complete: 'load' | 'domcontentloaded' |
        'networkidle' | 'commit'."""

    @abstractmethod
    async def click(self, selector: str, *, timeout_ms: int = 5000) -> None:
        """Click the first element matching `selector`. Raises if not found
        within timeout."""

    @abstractmethod
    async def fill(self, selector: str, value: str, *, clear_first: bool = True) -> None:
        """Type `value` into the first `<input>`/`<textarea>` matching
        `selector`. `clear_first=True` blanks the field before typing."""

    @abstractmethod
    async def read_text(self, selector: str) -> str:
        """Return `innerText` of the first element matching `selector`."""

    @abstractmethod
    async def read_table(self, selector: str) -> list[dict[str, str]]:
        """Parse the `<table>` matching `selector` into a list of dicts.
        Keys come from the first row of `<th>` headers; if absent, falls
        back to the first `<tr>`'s `<td>` cells (this fallback may be
        wrong for some templates — workflows can request a different
        selector that includes the header row)."""

    @abstractmethod
    async def submit_form(self, selector: str) -> None:
        """Submit the `<form>` matching `selector` via JS `form.submit()`.

        Use when the page has no visible submit button (or the button is
        gated behind UI state that's hard to drive). The browser issues
        the form's `action`/`method` POST directly. The page may
        navigate as a result; subsequent steps should re-read state.
        """

    @abstractmethod
    async def upload_file(self, selector: str, file_path: str) -> None:
        """Set the value of an `<input type="file">` matching `selector`
        to the local `file_path`. Does not click submit — that's a
        separate `click` call so the workflow controls the ordering."""

    @abstractmethod
    async def download_via_click(
        self, selector: str, *, timeout_ms: int = 30000
    ) -> BrowserDownload:
        """Click the element matching `selector` and capture the download
        Playwright surfaces. Saves to the per-run downloads dir; returns
        a `BrowserDownload` whose `local_path` subsequent steps can read."""

    @abstractmethod
    async def fetch_url(self, url: str, *, dest_filename: str | None = None) -> BrowserDownload:
        """Fetch a URL via the browser's session and save to the per-run
        downloads dir. Use this for `<a href="...">` links that don't
        trigger a browser-level download event (e.g. inline images,
        `target="_blank"` JPGs). `dest_filename` defaults to the URL's
        last path segment."""

    @abstractmethod
    async def screenshot(
        self, *, path: str | None = None, full_page: bool = False
    ) -> BrowserScreenshot:
        """Capture the page (or just the viewport if `full_page=False`).
        `path` defaults to a timestamped name under the per-run downloads
        dir."""

    @abstractmethod
    async def wait_for(
        self, selector: str, *, state: WaitState = "visible", timeout_ms: int = 5000
    ) -> None:
        """Wait until the element matching `selector` reaches `state`.
        Raises on timeout."""

    @abstractmethod
    async def authenticate(self) -> None:
        """Establish or refresh credentials. For browsers, this is a no-op
        on most sites — credentials are typically established via navigate
        + fill + click against a login form. Phase 3 work (cookie injection)
        gives this method a real implementation."""

    # --- concrete defaults from Connector base ---

    async def health_check(self) -> bool:
        """A browser connector is "healthy" if it can hand back the current
        URL — i.e., the underlying Page is alive. Subclasses override for
        a tighter check."""
        return True

    async def trigger_poll(self) -> list[dict[str, object]]:
        """Browsers are not triggers in the polling sense. Return empty."""
        return []
