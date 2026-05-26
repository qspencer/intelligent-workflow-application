"""Browser connector subpackage.

Provider-agnostic ABC + Pydantic models in this layer; concrete
backend connectors (Playwright in v1, future Selenium / CDP-direct)
live alongside in their own modules. See
`docs/BROWSER_CONNECTOR_PLAN.md` for the design.
"""

from workflow_platform.connectors.browser.base import (
    BrowserConnector,
    detect_selector_type,
)
from workflow_platform.connectors.browser.models import (
    BrowserDownload,
    BrowserScreenshot,
    BrowserSelector,
    SelectorType,
    WaitState,
)

__all__ = [
    "BrowserConnector",
    "BrowserDownload",
    "BrowserScreenshot",
    "BrowserSelector",
    "SelectorType",
    "WaitState",
    "detect_selector_type",
]
