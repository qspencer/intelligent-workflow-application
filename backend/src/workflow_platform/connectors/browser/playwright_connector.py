"""PlaywrightConnector — concrete BrowserConnector against Playwright.

Lifecycle: one connector instance per workflow run. The engine
constructs it lazily (when a workflow definition references any
`browser_*` tool) and tears it down in the run's `finally` block via
the async context manager (`__aenter__` / `__aexit__`). Tests inject a
ready-made `page=` and bypass the launch chain entirely.

D2 scope: lifecycle + navigate + health_check + screenshot. The other
7 abstract methods raise NotImplementedError until D3 / D4 fills them
in.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from bs4 import BeautifulSoup, Tag

from workflow_platform.connectors.browser.base import BrowserConnector
from workflow_platform.connectors.browser.models import (
    BrowserDownload,
    BrowserScreenshot,
    WaitState,
)

logger = logging.getLogger(__name__)


def parse_html_table(html: str) -> list[dict[str, str]]:
    """Parse an HTML `<table>` into a list of dicts. Pure function — no
    Playwright involved — so it's unit-testable without a browser.

    Header source, in priority order:
      1. `<thead>` with `<th>` children (preferred)
      2. First `<tr>`'s `<th>` children (if no thead)
      3. First `<tr>`'s `<td>` children as `col_0`, `col_1`, ... (fallback
         for headerless tables)

    Body rows come from `<tbody>` if present, else all rows after the
    header row. Cell text is stripped of surrounding whitespace; HTML
    tags inside cells are flattened to text.

    Empty result is the right answer for empty tables — don't raise.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not isinstance(table, Tag):
        return []

    all_rows = [r for r in table.find_all("tr") if isinstance(r, Tag)]
    if not all_rows:
        return []

    headers: list[str] = []
    body_rows: list[Tag] = []

    thead = table.find("thead")
    if isinstance(thead, Tag):
        header_row = thead.find("tr")
        if isinstance(header_row, Tag):
            headers = [_cell_text(th) for th in header_row.find_all("th") if isinstance(th, Tag)]
        tbody = table.find("tbody")
        if isinstance(tbody, Tag):
            body_rows = [r for r in tbody.find_all("tr") if isinstance(r, Tag)]
        else:
            # thead but no tbody — body rows are everything outside the thead.
            thead_rows = {r for r in thead.find_all("tr") if isinstance(r, Tag)}
            body_rows = [r for r in all_rows if r not in thead_rows]
    else:
        # No thead — derive headers from first row.
        first_row = all_rows[0]
        ths = [th for th in first_row.find_all("th") if isinstance(th, Tag)]
        if ths:
            headers = [_cell_text(th) for th in ths]
            body_rows = all_rows[1:]
        else:
            tds = [td for td in first_row.find_all("td") if isinstance(td, Tag)]
            if tds:
                # Headerless — synthesize col_N names + include first row as data.
                headers = [f"col_{i}" for i in range(len(tds))]
                body_rows = all_rows
            else:
                return []

    if not headers:
        return []

    result: list[dict[str, str]] = []
    for row in body_rows:
        cells = [c for c in row.find_all(["td", "th"]) if isinstance(c, Tag)]
        row_data: dict[str, str] = {}
        for i, cell in enumerate(cells):
            if i < len(headers):
                row_data[headers[i]] = _cell_text(cell)
        if row_data:
            result.append(row_data)
    return result


def _cell_text(tag: Tag) -> str:
    """Cell-text extraction: stripped + internal whitespace normalized to
    a single space. Matches what Playwright's `inner_text` returns for
    the same cell — newlines and indent collapse to spaces."""
    # bs4 is typed as Any in our mypy overrides, hence the annotation.
    text: str = tag.get_text(separator=" ", strip=True)
    return " ".join(text.split())


class PlaywrightConnector(BrowserConnector):
    type: ClassVar[str] = "browser:playwright"

    def __init__(
        self,
        *,
        downloads_dir: Path,
        headless: bool = True,
        viewport: dict[str, int] | None = None,
        page: Any = None,
    ) -> None:
        """Construct (but do not launch). Call `__aenter__` to actually
        start Playwright, or pass a ready-made `page=` for tests.

        `downloads_dir`: per-run directory where downloads + screenshots
        land. Owned + created by the engine; the connector treats it as
        write-only output.

        `headless` / `viewport`: passed straight to chromium launch.

        `page`: test injection point. If provided, the lifecycle methods
        are no-ops and the connector uses this page directly. None means
        a real Chromium will be launched on `__aenter__`.
        """
        self.downloads_dir = downloads_dir
        self.headless = headless
        self.viewport = viewport or {"width": 1280, "height": 720}
        self._page: Any = page
        self._injected = page is not None
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    # --- lifecycle ---

    async def __aenter__(self) -> PlaywrightConnector:
        if self._injected:
            # Test path — page already provided, no launch needed.
            self.downloads_dir.mkdir(parents=True, exist_ok=True)
            return self
        # Live path — launch chromium + create context + open page.
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(viewport=self.viewport)
        self._page = await self._context.new_page()
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "PlaywrightConnector launched (headless=%s, viewport=%s, downloads_dir=%s)",
            self.headless,
            self.viewport,
            self.downloads_dir,
        )
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._injected:
            return  # nothing we own to tear down
        # Close in reverse construction order. Each step is best-effort;
        # we want all of them to run even if one raises.
        for label, obj in (
            ("context", self._context),
            ("browser", self._browser),
            ("playwright", self._playwright),
        ):
            if obj is None:
                continue
            try:
                if label == "playwright":
                    await obj.stop()
                else:
                    await obj.close()
            except Exception:
                logger.exception("Error closing Playwright %s during teardown", label)
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    # --- BrowserConnector implementations (D2 surface) ---

    async def navigate(self, url: str, *, wait_until: str = "load") -> None:
        await self._page.goto(url, wait_until=wait_until)

    async def health_check(self) -> bool:
        """Healthy iff the Page object is alive and reports a URL."""
        if self._page is None:
            return False
        try:
            current = self._page.url
            return bool(current)
        except Exception:
            return False

    async def screenshot(
        self, *, path: str | None = None, full_page: bool = False
    ) -> BrowserScreenshot:
        if path is None:
            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            path = str(self.downloads_dir / f"screenshot-{ts}.png")
        await self._page.screenshot(path=path, full_page=full_page)
        size = Path(path).stat().st_size if Path(path).exists() else 0
        return BrowserScreenshot(local_path=path, bytes=size, full_page=full_page)

    # --- BrowserConnector implementations (D3/D4 stubs) ---

    async def click(self, selector: str, *, timeout_ms: int = 5000) -> None:
        raise NotImplementedError("click lands in D4")

    async def fill(self, selector: str, value: str, *, clear_first: bool = True) -> None:
        raise NotImplementedError("fill lands in D4")

    async def read_text(self, selector: str) -> str:
        # `_page` is Any (Playwright not type-checked) — annotate explicitly.
        text: str = await self._page.locator(selector).inner_text()
        return text

    async def read_table(self, selector: str) -> list[dict[str, str]]:
        html: str = await self._page.locator(selector).inner_html()
        # Wrap the locator's inner_html in a <table> shell so bs4 can parse
        # consistently — Playwright's inner_html returns the children of the
        # selected element (not the element itself).
        return parse_html_table(f"<table>{html}</table>")

    async def upload_file(self, selector: str, file_path: str) -> None:
        raise NotImplementedError("upload_file lands in D4")

    async def download_via_click(
        self, selector: str, *, timeout_ms: int = 30000
    ) -> BrowserDownload:
        raise NotImplementedError("download_via_click lands in D4")

    async def wait_for(
        self, selector: str, *, state: WaitState = "visible", timeout_ms: int = 5000
    ) -> None:
        await self._page.locator(selector).wait_for(state=state, timeout=timeout_ms)

    async def authenticate(self) -> None:
        # Browsers don't have a generic auth step — sites that need login
        # do it via navigate + fill + click in the workflow. Phase 3
        # work (cookie-state injection) gives this method real behavior.
        return None
