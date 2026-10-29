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
from collections.abc import Callable
from typing import ClassVar
from urllib.parse import urlsplit

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


class BrowserHostDenied(Exception):
    """A URL, or the page itself, is outside the step's `allowed_hosts`."""


def url_host_allowed(url: str, allowed: Callable[[str], bool]) -> bool:
    """Whether `url` passes a host policy. Only http(s) URLs with a host can:
    under a policy, `file:`, `data:` and hostless URLs are refused, so a step
    restricted to named hosts cannot read the local filesystem through the
    browser either."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    return allowed(parts.hostname)


class BrowserConnector(Connector):
    """`allowed_hosts` is enforced HERE (G30, 2026-10-29) — it was declared,
    resolved and displayed, and checked nowhere. The browser is the only
    surface where the agent chooses a network destination: email goes to a
    fixed API host and connectors take their URL from operator config.

    THE CONTRACT, stated precisely because the external reviewer (2026-10-29)
    showed the earlier wording overclaimed. This governs two things:

    1. requests the AGENT initiates — blocked before dispatch: `navigate`, the
       first hop of a navigation a click or form submit sets off, and every
       hop of `fetch_url`;
    2. CONTENT — nothing a disallowed host returns reaches the agent.

    It is NOT a destination (egress) control. Requests the agent did not choose
    can still reach a disallowed host: a server redirect hop during a page
    navigation (Playwright routes only the first hop), a page's own
    subresources, `preconnect` / DNS prefetch (a connection with no request at
    all), and WebSockets. A request can disclose metadata or cause a side
    effect even when nothing comes back, so "no content reached the agent" does
    not answer it. Destination enforcement must happen BEFORE dispatch, at the
    network layer, and is a prerequisite for enabling any browser tool in the
    system layer — `test_system_capabilities.py` refuses that until it exists."""

    type: ClassVar[str]

    #: The CURRENT step's host predicate; None = unrestricted. The connector
    #: lives for a whole run but capabilities are per step, so every tool call
    #: sets it (`tools.browser._get_connector`) before acting.
    _host_allowed: Callable[[str], bool] | None = None

    def set_host_policy(self, allowed: Callable[[str], bool] | None) -> None:
        self._host_allowed = allowed

    def check_url(self, url: str) -> None:
        """Refuse a URL BEFORE any request is made for it."""
        if self._host_allowed is not None and not url_host_allowed(url, self._host_allowed):
            raise BrowserHostDenied(f"{url!r} is outside this step's allowed_hosts")

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
