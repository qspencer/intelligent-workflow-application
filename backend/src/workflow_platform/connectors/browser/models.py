"""Provider-agnostic browser-automation types.

These shapes are what crosses the boundary between the engine and any
browser connector: agent tools take and return them, the engine stores
them in step outputs / audit log, etc. Concrete connectors (Playwright,
Selenium, CDP-direct) translate to/from backend-specific representations.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# CSS is the default; XPath is auto-detected by leading `/` or `//`.
# Explicit `selector_type` is available for the rare case where an XPath
# happens to look like a CSS selector (unusual but possible).
SelectorType = Literal["css", "xpath"]

# Wait-state values supported by Playwright's locator.wait_for().
WaitState = Literal["visible", "hidden", "attached", "detached"]


class BrowserSelector(BaseModel):
    """A DOM selector with optional explicit type. When `type` is None, the
    connector auto-detects by leading character (`/` or `//` → xpath, else
    css)."""

    selector: str = Field(min_length=1)
    type: SelectorType | None = None


class BrowserDownload(BaseModel):
    """Result of `browser_download_via_click`. The `local_path` is the file
    the agent / subsequent steps read from; `source_url` is informational
    (Playwright's `Download.url` — the URL the click ultimately fetched)."""

    source_url: str
    local_path: str
    suggested_filename: str
    bytes: int = Field(ge=0)


class BrowserScreenshot(BaseModel):
    """Result of `browser_screenshot`. The path is on the same per-run
    download dir as `BrowserDownload.local_path` so audit consumers find
    all per-run artifacts in one place."""

    local_path: str
    bytes: int = Field(ge=0)
    full_page: bool = False
