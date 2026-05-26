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

from workflow_platform.connectors.browser.base import BrowserConnector
from workflow_platform.connectors.browser.models import (
    BrowserDownload,
    BrowserScreenshot,
    WaitState,
)

logger = logging.getLogger(__name__)


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
        raise NotImplementedError("click lands in D3")

    async def fill(self, selector: str, value: str, *, clear_first: bool = True) -> None:
        raise NotImplementedError("fill lands in D4")

    async def read_text(self, selector: str) -> str:
        raise NotImplementedError("read_text lands in D3")

    async def read_table(self, selector: str) -> list[dict[str, str]]:
        raise NotImplementedError("read_table lands in D3")

    async def upload_file(self, selector: str, file_path: str) -> None:
        raise NotImplementedError("upload_file lands in D4")

    async def download_via_click(
        self, selector: str, *, timeout_ms: int = 30000
    ) -> BrowserDownload:
        raise NotImplementedError("download_via_click lands in D4")

    async def wait_for(
        self, selector: str, *, state: WaitState = "visible", timeout_ms: int = 5000
    ) -> None:
        raise NotImplementedError("wait_for lands in D3")

    async def authenticate(self) -> None:
        # Browsers don't have a generic auth step — sites that need login
        # do it via navigate + fill + click in the workflow. Phase 3
        # work (cookie-state injection) gives this method real behavior.
        return None
