"""Tests for `workflow_platform.connectors.browser.models` +
`detect_selector_type`.

These shapes are the contract every browser connector implements
against and the format that crosses into agent step outputs / audit
log. Exhaustive on shape — if these regress, every browser-using
workflow regresses with them.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from workflow_platform.connectors.browser import (
    BrowserDownload,
    BrowserScreenshot,
    BrowserSelector,
    detect_selector_type,
)

# ---------- BrowserSelector ----------


def test_selector_minimal() -> None:
    sel = BrowserSelector(selector="#submit")
    assert sel.selector == "#submit"
    assert sel.type is None


def test_selector_with_explicit_type() -> None:
    sel = BrowserSelector(selector="div.row", type="css")
    assert sel.type == "css"


def test_selector_rejects_empty_string() -> None:
    with pytest.raises(ValidationError):
        BrowserSelector(selector="")


def test_selector_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        BrowserSelector(selector="x", type="jquery")


def test_selector_round_trip() -> None:
    src = BrowserSelector(selector="//button[text()='Submit']", type="xpath")
    dumped = src.model_dump()
    restored = BrowserSelector.model_validate(dumped)
    assert restored == src


# ---------- detect_selector_type ----------


@pytest.mark.parametrize(
    "selector,expected",
    [
        # XPath patterns (leading / or //)
        ("/html/body/div", "xpath"),
        ("//button[@id='submit']", "xpath"),
        ("//div[contains(@class, 'row')]//a", "xpath"),
        ("/*", "xpath"),  # arguable but matches Playwright behavior
        # CSS patterns (anything else)
        ("#submit", "css"),
        (".btn-primary", "css"),
        ("div.row > a", "css"),
        ("input[type='file']", "css"),
        ("button:has-text('Submit')", "css"),  # Playwright text engine
        ("table#tableSandbox tbody tr", "css"),
        ("text=Start", "css"),  # Playwright text-locator syntax
        ("p", "css"),
    ],
)
def test_detect_selector_type(selector: str, expected: str) -> None:
    assert detect_selector_type(selector) == expected


# ---------- BrowserDownload ----------


def test_download_minimal_round_trip() -> None:
    src = BrowserDownload(
        source_url="https://example.com/file.pdf",
        local_path="/tmp/browser-downloads/abc/file.pdf",
        suggested_filename="file.pdf",
        bytes=4096,
    )
    dumped = src.model_dump()
    restored = BrowserDownload.model_validate(dumped)
    assert restored == src


def test_download_rejects_negative_bytes() -> None:
    with pytest.raises(ValidationError):
        BrowserDownload(
            source_url="https://example.com/x.pdf",
            local_path="/tmp/x.pdf",
            suggested_filename="x.pdf",
            bytes=-1,
        )


def test_download_allows_zero_bytes() -> None:
    """An empty file is a legitimate (if unusual) outcome — don't reject."""
    BrowserDownload(
        source_url="https://example.com/empty",
        local_path="/tmp/empty",
        suggested_filename="empty",
        bytes=0,
    )


# ---------- BrowserScreenshot ----------


def test_screenshot_minimal() -> None:
    shot = BrowserScreenshot(local_path="/tmp/x.png", bytes=12345)
    assert shot.full_page is False  # default
    assert shot.local_path == "/tmp/x.png"


def test_screenshot_full_page_flag() -> None:
    shot = BrowserScreenshot(local_path="/tmp/x.png", bytes=99999, full_page=True)
    assert shot.full_page is True


def test_screenshot_rejects_negative_bytes() -> None:
    with pytest.raises(ValidationError):
        BrowserScreenshot(local_path="/tmp/x.png", bytes=-1)


# ---------- imports work from top-level connectors package ----------


def test_public_exports() -> None:
    """Confirms the models are reachable from
    workflow_platform.connectors (the canonical import path)."""
    from workflow_platform.connectors import (
        BrowserConnector,
        BrowserDownload,
        BrowserScreenshot,
        BrowserSelector,
    )

    assert BrowserConnector.__name__ == "BrowserConnector"
    assert BrowserDownload.__name__ == "BrowserDownload"
    assert BrowserScreenshot.__name__ == "BrowserScreenshot"
    assert BrowserSelector.__name__ == "BrowserSelector"
