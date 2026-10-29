"""`allowed_hosts` against a REAL Chromium — opt-in via `BROWSER_LIVE=1`.

The unit tests in `test_g30_capability_enforcement.py` cover every decision;
this covers what only a browser can show: that route interception stops an
off-list navigation a click sets off BEFORE the request is sent, and what a
redirect hop actually does. Two host names for one local server stand in for
two sites: `127.0.0.1` is allowed, `localhost` is not. No network, no AWS.

    BROWSER_LIVE=1 uv run pytest tests/test_browser_host_policy_live.py -q

The redirect test PINS THE RESIDUAL as a measured fact rather than a sentence:
the hop's request reaches the server (Playwright routes only a redirect's first
hop), and the guarantee that still holds is that nothing it returned reaches
the agent. If Playwright starts routing redirect hops, that assertion fails and
the documented residual can be withdrawn.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from workflow_platform.connectors.browser import BrowserHostDenied, PlaywrightConnector
from workflow_platform.security.capabilities import CapabilityPolicy, resolve_capabilities

pytestmark = [
    pytest.mark.browser_live,
    pytest.mark.skipif(
        os.environ.get("BROWSER_LIVE") != "1",
        reason="BROWSER_LIVE not set; skipping live browser tests",
    ),
]


class _Site:
    def __init__(self) -> None:
        self.hits: list[tuple[str, str]] = []  # (host header without port, path)
        self.port = 0


@pytest.fixture()
def site() -> Iterator[_Site]:
    state = _Site()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            host = (self.headers.get("Host") or "").split(":")[0]
            state.hits.append((host, self.path))
            other = f"http://localhost:{state.port}"
            if self.path == "/start":
                body = (
                    f'<a id="out" href="{other}/secret">out</a>'
                    f'<img src="{other}/pixel">'
                    '<p id="msg">on the allowed site</p>'
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)
            elif self.path in ("/redir", "/late-hop"):
                landing = "/late-landing" if self.path == "/late-hop" else "/landing"
                self.send_response(302)
                self.send_header("Location", f"{other}{landing}")
                self.end_headers()
            elif self.path in ("/late-start", "/late-stable", "/late-landing"):
                body = {
                    # No #late here; the page leaves on its own while a read waits.
                    "/late-start": "<script>setTimeout(() => location.href = '/late-hop', 300)"
                    "</script><p>on the allowed site</p>",
                    # The positive control: #late arrives on the page that was checked.
                    "/late-stable": "<script>setTimeout(() => document.body.insertAdjacentHTML("
                    "'beforeend', '<p id=late>late on the allowed site</p>'), 300)</script>",
                    "/late-landing": '<p id="late">SECRET-LATE from the disallowed host</p>',
                }[self.path].encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b'<p id="msg">SECRET from the disallowed host</p>')

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()


@pytest.fixture()
async def browser(tmp_path: Path) -> AsyncIterator[PlaywrightConnector]:
    conn = PlaywrightConnector(downloads_dir=tmp_path / "downloads")
    await conn.__aenter__()
    conn.set_host_policy(
        resolve_capabilities(CapabilityPolicy(allowed_hosts=["127.0.0.1"])).host_allowed
    )
    try:
        yield conn
    finally:
        await conn.__aexit__(None, None, None)


def _paths(site: _Site, host: str) -> list[str]:
    return [p for h, p in site.hits if h == host]


async def test_a_click_off_list_is_stopped_before_the_request_is_sent(
    site: _Site, browser: PlaywrightConnector
) -> None:
    """Route interception aborts the navigation, and Chromium then shows its
    own error page instead of staying put — learned from this test's first
    run. Whether `click` itself sees that depends on timing (the abort can land
    after it returns), so the guarantee is asserted on the NEXT action, which is
    always guarded."""
    base = f"http://127.0.0.1:{site.port}"
    await browser.navigate(f"{base}/start")
    # The block may land before click returns or after; either ordering is correct.
    with contextlib.suppress(BrowserHostDenied):
        await browser.click("#out")
    await browser._page.wait_for_timeout(500)  # let the aborted navigation settle
    assert "/secret" not in _paths(site, "localhost"), "the off-list navigation reached the server"
    # Whichever ordering ran, the guard every action runs first leaves the page
    # cleared: either it already was (click saw the block), or it clears it now.
    try:
        await browser._guard()
    except BrowserHostDenied as exc:
        assert "blocked" in str(exc)
    assert browser._page.url == "about:blank", "an aborted navigation left a readable page"
    # The agent can recover by navigating somewhere allowed.
    await browser.navigate(f"{base}/start")
    assert await browser.read_text("#msg") == "on the allowed site"
    # Scope, stated: a page's own subresources are not the agent's choice.
    assert "/pixel" in _paths(site, "localhost")


async def test_navigate_off_list_sends_nothing(site: _Site, browser: PlaywrightConnector) -> None:
    with pytest.raises(BrowserHostDenied):
        await browser.navigate(f"http://localhost:{site.port}/secret")
    assert _paths(site, "localhost") == []


async def test_a_redirect_hop_is_the_residual_and_its_content_never_reaches_the_agent(
    site: _Site, browser: PlaywrightConnector
) -> None:
    with pytest.raises(BrowserHostDenied, match="cleared"):
        await browser.navigate(f"http://127.0.0.1:{site.port}/redir")
    assert browser._page.url == "about:blank"
    # THE RESIDUAL, measured: Playwright routes only a redirect's first hop, so
    # the hop's request was sent. If this ever fails, the residual is gone.
    assert "/landing" in _paths(site, "localhost")


async def test_fetch_url_never_follows_a_redirect_off_list(
    site: _Site, browser: PlaywrightConnector
) -> None:
    with pytest.raises(BrowserHostDenied):
        await browser.fetch_url(f"http://127.0.0.1:{site.port}/redir")
    assert "/landing" not in _paths(site, "localhost"), "fetch_url followed the redirect off-list"


async def test_a_backslash_cannot_smuggle_a_disallowed_host_past_fetch_url(
    site: _Site, browser: PlaywrightConnector
) -> None:
    """The independent red-team's repro (2026-10-29), kept as a regression:
    Python's `urlsplit` read this host as 127.0.0.1 (allowed); Playwright's
    request API reads it per WHATWG — the backslash ends the host — and
    fetched from localhost."""
    smuggled = f"http://localhost:{site.port}\\@127.0.0.1:{site.port}/x"
    with pytest.raises(BrowserHostDenied):
        await browser.fetch_url(smuggled)
    assert _paths(site, "localhost") == [], "the smuggled request reached the disallowed host"


async def test_a_read_waiting_while_the_page_leaves_for_an_off_list_host_is_withheld(
    site: _Site, browser: PlaywrightConnector
) -> None:
    """Round-2 review, R2-F3, the sequence they asked to see in a real browser:
    the read is checked on an allowed page that has no `#late`; while
    Playwright waits for it, the page's own script navigates to an allowed hop
    that redirects off-list, where `#late` exists. Without the fence this read
    returns the disallowed host's text."""
    base = f"http://127.0.0.1:{site.port}"
    await browser.navigate(f"{base}/late-start")
    with pytest.raises(BrowserHostDenied, match="withheld"):
        await browser.read_text("#late")
    assert "/late-landing" in _paths(site, "localhost"), "the sequence did not run as designed"
    assert browser._page.url == "about:blank"


async def test_a_read_waiting_on_a_page_that_stays_put_returns_its_content(
    site: _Site, browser: PlaywrightConnector
) -> None:
    """The control beside it: the same wait, with `#late` arriving on the
    checked page, is returned — the fence refuses navigation, not waiting."""
    await browser.navigate(f"http://127.0.0.1:{site.port}/late-stable")
    assert await browser.read_text("#late") == "late on the allowed site"
