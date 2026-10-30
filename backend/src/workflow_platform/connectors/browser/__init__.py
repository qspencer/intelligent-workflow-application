"""Browser connector subpackage.

Provider-agnostic ABC + Pydantic models in this layer; concrete
backend connectors (Playwright in v1, future Selenium / CDP-direct)
live alongside in their own modules. See
`docs/BROWSER_CONNECTOR_PLAN.md` for the design.
"""

from workflow_platform.connectors.browser.base import (
    BrowserBoundedError,
    BrowserCallFailed,
    BrowserConnector,
    BrowserHostDenied,
    BrowserPageNotCleared,
    detect_selector_type,
    is_vouched_page_error,
    url_host_allowed,
    vouch_checked_page_error,
)
from workflow_platform.connectors.browser.models import (
    BrowserDownload,
    BrowserScreenshot,
    BrowserSelector,
    BrowserUpload,
    SelectorType,
    WaitState,
)
from workflow_platform.connectors.browser.playwright_connector import (
    PlaywrightConnector,
)

__all__ = [
    "BrowserBoundedError",
    "BrowserCallFailed",
    "BrowserConnector",
    "BrowserDownload",
    "BrowserHostDenied",
    "BrowserPageNotCleared",
    "BrowserScreenshot",
    "BrowserSelector",
    "BrowserUpload",
    "PlaywrightConnector",
    "SelectorType",
    "WaitState",
    "detect_selector_type",
    "is_vouched_page_error",
    "url_host_allowed",
    "vouch_checked_page_error",
]
