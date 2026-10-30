"""`max_tokens_per_call` and `allowed_hosts` — enforced as of G30 (2026-10-29).

Both were declared on `CapabilityPolicy`, resolved most-restrictive-wins, and
shown on the canvas, and NEITHER was checked anywhere: the trace projector's
schema was their only reader. A displayed control that is never enforced is
worse than none, because it is believed.

`max_tokens_per_call` bounds each model call's OUTPUT (Bedrock `maxTokens`).

`allowed_hosts` is enforced in the BROWSER connector, the one surface where the
agent chooses a network destination. Guarantee under a policy: no content from
a disallowed host reaches the agent, and no agent-initiated request is sent to
one — except a server redirect hop during a page navigation, which is sent
before anything in-process can see it (its response is discarded and the action
fails). The route-interception half needs a real browser and lives in the
opt-in `BROWSER_LIVE` suite; everything else is here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response
from tests._browser_fakes import FakeDownload, FakeFrame, FakePlaywrightPage
from workflow_platform.agent import Agent
from workflow_platform.connectors.browser import (
    BrowserHostDenied,
    BrowserPageNotCleared,
    BrowserUpload,
    PlaywrightConnector,
    url_host_allowed,
)
from workflow_platform.security.capabilities import CapabilityPolicy, resolve_capabilities
from workflow_platform.tools import ToolContext
from workflow_platform.tools.browser import BrowserFetchUrlTool, BrowserNavigateTool

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
ALLOWED = "https://allowed.example/start"
OTHER = "https://evil.example/page"


# --- max_tokens_per_call ----------------------------------------------------


async def _sent_inference_config(
    *layers: CapabilityPolicy | None, inference_config: dict[str, Any] | None = None
) -> Any:
    from workflow_platform.agent.agent import AgentPolicy

    fake = FakeBedrock([text_response("ok")])
    agent = Agent(
        system_prompt="s",
        tools=[],
        model_id=MODEL,
        bedrock=fake,
        policy=AgentPolicy(inference_config=inference_config),
    )
    caps = resolve_capabilities(*layers) if layers else None
    await agent.run("hi", context=ToolContext(capabilities=caps))
    return fake.calls[0]["inference_config"]


async def test_no_cap_leaves_the_request_exactly_as_it_was() -> None:
    """Unchanged — not merely equal — so a recorded replay hashes the same."""
    step_config = {"temperature": 0}
    assert await _sent_inference_config(inference_config=step_config) is step_config
    assert await _sent_inference_config(CapabilityPolicy(tools=[])) is None


async def test_a_cap_sets_max_tokens_when_the_step_asked_for_none() -> None:
    assert await _sent_inference_config(CapabilityPolicy(max_tokens_per_call=4096)) == {
        "maxTokens": 4096
    }


async def test_a_step_asking_for_less_keeps_its_own_figure() -> None:
    sent = await _sent_inference_config(
        CapabilityPolicy(max_tokens_per_call=4096), inference_config={"maxTokens": 200}
    )
    assert sent == {"maxTokens": 200}


async def test_a_step_asking_for_more_is_clamped_and_other_settings_survive() -> None:
    sent = await _sent_inference_config(
        CapabilityPolicy(max_tokens_per_call=4096),
        inference_config={"maxTokens": 64_000, "temperature": 0},
    )
    assert sent == {"maxTokens": 4096, "temperature": 0}


async def test_the_most_restrictive_layer_wins() -> None:
    sent = await _sent_inference_config(
        CapabilityPolicy(max_tokens_per_call=4096), CapabilityPolicy(max_tokens_per_call=300)
    )
    assert sent == {"maxTokens": 300}


# --- allowed_hosts: the policy itself ---------------------------------------


def test_no_restricting_layer_means_no_host_policy() -> None:
    """`host_allowed` passes vacuously, but a host policy would also refuse
    `file:` URLs — so "no layer restricts hosts" must mean NO policy."""
    assert not resolve_capabilities(CapabilityPolicy(tools=["x"])).restricts_hosts
    assert resolve_capabilities(CapabilityPolicy(allowed_hosts=[])).restricts_hosts


async def _connector(
    tmp_path: Path, *hosts: str, page: FakePlaywrightPage | None = None
) -> tuple[PlaywrightConnector, FakePlaywrightPage]:
    page = page or FakePlaywrightPage()
    conn = PlaywrightConnector(downloads_dir=tmp_path / "dl", page=page)
    await conn.__aenter__()
    caps = resolve_capabilities(CapabilityPolicy(allowed_hosts=list(hosts)))
    conn.set_host_policy(caps.host_allowed)
    return conn, page


def _gotos(page: FakePlaywrightPage) -> list[str]:
    return [c[1]["url"] for c in page.calls if c[0] == "goto"]


async def test_navigation_to_a_disallowed_host_is_refused_before_any_request(
    tmp_path: Path,
) -> None:
    conn, page = await _connector(tmp_path, "allowed.example")
    await conn.navigate(ALLOWED)
    with pytest.raises(BrowserHostDenied):
        await conn.navigate(OTHER)
    assert _gotos(page) == [ALLOWED], "a goto was issued for the disallowed host"


async def test_globs_match_hosts(tmp_path: Path) -> None:
    conn, _ = await _connector(tmp_path, "*.example")
    await conn.navigate("https://sub.allowed.example/x")


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "data:text/html,<b>x</b>", "javascript:alert(1)", "https:///x"]
)
async def test_hostless_and_non_http_urls_are_refused_under_a_policy(
    tmp_path: Path, url: str
) -> None:
    conn, page = await _connector(tmp_path, "*")
    with pytest.raises(BrowserHostDenied):
        await conn.navigate(url)
    assert _gotos(page) == []


async def test_a_redirect_landing_off_list_is_cleared_and_fails(tmp_path: Path) -> None:
    """The documented residual: the hop's request is sent, but the page is
    cleared before the agent can read anything it returned."""
    page = FakePlaywrightPage()
    page.redirect_to[ALLOWED] = OTHER
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied, match="cleared"):
        await conn.navigate(ALLOWED)
    assert page.url == "about:blank"


async def test_a_click_that_leaves_the_site_fails_and_clears(tmp_path: Path) -> None:
    page = FakePlaywrightPage(url=ALLOWED)
    page.click_navigates["a#out"] = OTHER
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied):
        await conn.click("a#out")
    assert page.url == "about:blank"


async def test_an_aborted_navigation_reports_it_was_blocked(tmp_path: Path) -> None:
    """In a real Chromium, an aborted navigation leaves the browser's own error
    page in place (found by the live suite). That is not a host the agent
    reached, so the error must say BLOCKED rather than name a destination."""
    page = FakePlaywrightPage(url=ALLOWED)
    page.click_navigates["a#out"] = "chrome-error://chromewebdata/"
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied, match="was blocked"):
        await conn.click("a#out")
    assert page.url == "about:blank"


@pytest.mark.parametrize("action", ["read_text", "read_table", "wait_for", "fill", "screenshot"])
async def test_nothing_reads_or_acts_on_a_page_that_is_off_list(
    tmp_path: Path, action: str
) -> None:
    """Every read and every action checks where the page IS first, so content
    from a disallowed host never reaches the agent however the page got there."""
    page = FakePlaywrightPage(url=OTHER)
    page.text_at["#x"] = "SECRET"
    page.html_at["#x"] = "<tr><td>SECRET</td></tr>"
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    actions: dict[str, Callable[[], Awaitable[object]]] = {
        "read_text": lambda: conn.read_text("#x"),
        "read_table": lambda: conn.read_table("#x"),
        "wait_for": lambda: conn.wait_for("#x"),
        "fill": lambda: conn.fill("#x", "v"),
        "screenshot": lambda: conn.screenshot(path=str(tmp_path / "s.png")),
    }
    with pytest.raises(BrowserHostDenied):
        await actions[action]()
    assert page.url == "about:blank"
    assert not [c for c in page.calls if c[0].startswith("locator") or c[0] == "screenshot"]


# --- the check's lifetime: a page that navigates DURING a call (round-2 review R2-F3) ---

_READS = ["read_text", "read_table", "wait_for", "screenshot"]
_PAGE_CALL = {
    "read_text": "inner_text",
    "read_table": "inner_html",
    "wait_for": "wait_for",
    "screenshot": "screenshot",
}


def _read(conn: PlaywrightConnector, name: str, tmp_path: Path) -> Awaitable[object]:
    reads: dict[str, Callable[[], Awaitable[object]]] = {
        "read_text": lambda: conn.read_text("#late"),
        "read_table": lambda: conn.read_table("#late"),
        "wait_for": lambda: conn.wait_for("#late"),
        "screenshot": lambda: conn.screenshot(path=str(tmp_path / "s.png")),
    }
    return reads[name]()


def _page_with_late_content() -> FakePlaywrightPage:
    page = FakePlaywrightPage(url=ALLOWED)
    page.text_at["#late"] = "OFF_LIST_PAGE_FIXTURE"
    page.html_at["#late"] = "<tr><td>OFF_LIST_PAGE_FIXTURE</td></tr>"
    return page


@pytest.mark.parametrize("name", _READS)
async def test_a_read_whose_page_leaves_for_an_off_list_host_mid_read_is_withheld(
    tmp_path: Path, name: str
) -> None:
    """The reviewer's sequence: the check passes on the allowed page, the read
    waits, a navigation commits to an off-list page, and the read's reply comes
    from there. Before the fix the content was returned and the page left."""
    page = _page_with_late_content()
    page.during[_PAGE_CALL[name]] = lambda: page.commit(OTHER)
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied, match="withheld"):
        await _read(conn, name, tmp_path)
    assert page.url == "about:blank"
    assert not (tmp_path / "dl" / "s.png").exists(), "an image of the unchecked page was kept"


@pytest.mark.parametrize("name", _READS)
async def test_a_read_through_an_off_list_page_and_back_is_withheld(
    tmp_path: Path, name: str
) -> None:
    """Why a URL comparison is not enough: the page goes off-list and returns
    to the very URL that was checked, so start and end agree while the reply
    may have come from the page in between. The navigation count differs."""
    page = _page_with_late_content()

    def away_and_back() -> None:
        page.commit(OTHER)
        page.commit(ALLOWED)

    page.during[_PAGE_CALL[name]] = away_and_back
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied, match="withheld"):
        await _read(conn, name, tmp_path)
    assert page.url == ALLOWED, "an allowed page is left in place; only the result is withheld"


async def test_the_reviewers_probe_a_url_change_with_no_event_is_withheld(tmp_path: Path) -> None:
    """Their probe changed the URL without firing a navigation event; the URL
    half of the comparison refuses that on its own."""
    page = _page_with_late_content()
    page.during["inner_text"] = lambda: setattr(page, "_url", OTHER)
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied, match="withheld"):
        await conn.read_text("#late")
    assert page.url == "about:blank"


@pytest.mark.parametrize("name", _READS)
async def test_a_read_that_waits_on_a_page_that_stays_put_is_returned(
    tmp_path: Path, name: str
) -> None:
    """The positive control: waiting is not what is refused. Content that
    arrives late on the checked page, and a child frame navigating, pass."""
    page = _page_with_late_content()
    page.during[_PAGE_CALL[name]] = lambda: page.commit(
        "https://ads.other.example/f", frame=FakeFrame(parent_frame=page.main_frame)
    )
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    result = await _read(conn, name, tmp_path)
    if name == "read_text":
        assert result == "OFF_LIST_PAGE_FIXTURE"
    assert page.url == ALLOWED


async def test_without_a_policy_a_read_is_not_fenced(tmp_path: Path) -> None:
    """An unrestricted run is unchanged: nothing is checked, so nothing is withheld."""
    page = _page_with_late_content()
    page.during["inner_text"] = lambda: page.commit(OTHER)
    conn = PlaywrightConnector(downloads_dir=tmp_path / "dl", page=page)
    await conn.__aenter__()
    assert await conn.read_text("#late") == "OFF_LIST_PAGE_FIXTURE"


@pytest.mark.parametrize("name", ["click", "fill", "upload_file", "submit_form"])
async def test_an_action_whose_page_leaves_mid_action_fails_and_clears(
    tmp_path: Path, name: str
) -> None:
    """An action cannot be withheld — its effect may already have landed — so
    the contract for actions is narrower: the page is checked when the action
    ends, and an off-list page fails the call and is cleared. `fill` and
    `upload_file` did not check afterwards before this."""
    page = FakePlaywrightPage(url=ALLOWED)
    page_call = {"fill": "fill", "upload_file": "set_input_files"}
    if name in page_call:
        page.during[page_call[name]] = lambda: page.commit(OTHER)
    else:
        page.click_navigates["#late"] = OTHER  # click
        page.during["evaluate"] = lambda: page.commit(OTHER)  # submit_form
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    upload = BrowserUpload(name="a.csv", mime_type="text/csv", content=b"a\n")
    actions: dict[str, Callable[[], Awaitable[object]]] = {
        "click": lambda: conn.click("#late"),
        "fill": lambda: conn.fill("#late", "v"),
        "upload_file": lambda: conn.upload_file("#late", upload),
        "submit_form": lambda: conn.submit_form("#late"),
    }
    with pytest.raises(BrowserHostDenied):
        await actions[name]()
    assert page.url == "about:blank"


def test_every_page_method_is_a_fenced_read_or_a_checked_action() -> None:
    """Derived from the source: every connector method that runs `_guard` is
    classified — a read goes through `_fenced`, an action through `_acted`, and
    a new method fails here until it is one or the other. This pins ROUTING
    only; what happens on each exit is pinned by the behavioural tests around
    it, because a shape test cannot show every exit reaches a check (the
    round-3 review showed the previous version of this test could not)."""
    import ast
    import inspect
    import textwrap

    guarded = {}
    for name, fn in inspect.getmembers(PlaywrightConnector, inspect.iscoroutinefunction):
        source = ast.unparse(ast.parse(textwrap.dedent(inspect.getsource(fn))))
        if "self._guard()" in source:
            guarded[name] = source
    assert set(guarded) == set(_READS) | set(_ACTIONS)
    for name in _READS:
        assert "self._fenced(" in guarded[name], f"{name} is not fenced"
    for name in _ACTIONS:
        assert "self._acted(" in guarded[name], f"{name} is not checked on every exit"


# --- the same fence on the ERROR path (round-3 review, R3-F1) ---

_ACTIONS = ["click", "fill", "upload_file", "submit_form", "download_via_click"]
MARKER = "OFF_LIST_DIAGNOSTIC_FIXTURE"
#: The shape of Playwright's strict-mode error: it previews every match, text
#: included. Seen for real in the live suite, beside the test that pins it.
DIAGNOSTIC = (
    "Locator.inner_text: Error: strict mode violation: locator('#late') resolved to 2 elements:\n"
    f'    1) <p id="late">{MARKER}</p>\n    2) <p id="late">second fixture</p>\n'
)


def _failing(name: str, page: FakePlaywrightPage, during: Callable[[], None]) -> None:
    """Make `name`'s page call run `during`, then raise the diagnostic."""
    from playwright.async_api import Error as PlaywrightError

    page_call, errors = {
        "read_text": ("inner_text", page.raise_on_text),
        "read_table": ("inner_html", page.raise_on_html),
        "wait_for": ("wait_for", page.raise_on_wait),
        "click": ("click", page.raise_on_click),
        "download_via_click": ("click", page.raise_on_click),
        "fill": ("fill", page.raise_on_fill),
        "upload_file": ("set_input_files", page.raise_on_set_files),
        "submit_form": ("evaluate", page.raise_on_evaluate),
    }[name]
    page.during[page_call] = during
    errors["#late"] = PlaywrightError(DIAGNOSTIC)
    page.expect_download_value = FakeDownload()


def _call(conn: PlaywrightConnector, name: str) -> Awaitable[object]:
    upload = BrowserUpload(name="a.csv", mime_type="text/csv", content=b"a\n")
    calls: dict[str, Callable[[], Awaitable[object]]] = {
        "read_text": lambda: conn.read_text("#late"),
        "read_table": lambda: conn.read_table("#late"),
        "wait_for": lambda: conn.wait_for("#late"),
        "click": lambda: conn.click("#late"),
        "download_via_click": lambda: conn.download_via_click("#late"),
        "fill": lambda: conn.fill("#late", "v"),
        "upload_file": lambda: conn.upload_file("#late", upload),
        "submit_form": lambda: conn.submit_form("#late"),
    }
    return calls[name]()


_ERRORING = ["read_text", "read_table", "wait_for", *_ACTIONS]


@pytest.mark.parametrize("name", _ERRORING)
async def test_an_error_after_the_page_left_for_an_off_list_host_is_withheld(
    tmp_path: Path, name: str
) -> None:
    """The reviewer's sequence: the page navigates off-list mid-call and the
    call FAILS with an error quoting the page. Before the fix the error passed
    straight to the agent and the page stayed off-list. Now nothing of it
    reaches the caller — not the quote, not the URL the page reached."""
    page = FakePlaywrightPage(url=ALLOWED)
    _failing(name, page, lambda: page.commit(OTHER))
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied, match="withheld") as caught:
        await _call(conn, name)
    assert MARKER not in str(caught.value) and OTHER not in str(caught.value)
    assert caught.value.__context__ is None, "the original error rides along as context"
    assert page.url == "about:blank"


@pytest.mark.parametrize("name", _ERRORING)
async def test_an_error_after_off_list_and_back_is_withheld(tmp_path: Path, name: str) -> None:
    """Back at the checked URL by the time the error arrives: only the
    navigation count shows the error may quote the page in between."""
    page = FakePlaywrightPage(url=ALLOWED)

    def away_and_back() -> None:
        page.commit(OTHER)
        page.commit(ALLOWED)

    _failing(name, page, away_and_back)
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied, match="withheld") as caught:
        await _call(conn, name)
    assert MARKER not in str(caught.value)
    assert page.url == ALLOWED


@pytest.mark.parametrize("name", _ERRORING)
async def test_an_error_about_the_checked_page_is_kept(tmp_path: Path, name: str) -> None:
    """The positive control: with no navigation, the error is about the page
    that was checked, and the agent needs it to correct its selector."""
    from playwright.async_api import Error as PlaywrightError

    page = FakePlaywrightPage(url=ALLOWED)
    _failing(name, page, lambda: None)
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(PlaywrightError, match=MARKER):
        await _call(conn, name)
    assert page.url == ALLOWED


# --- when CLEARING the page fails (round-4 review, R4-F1) ---

#: The interrupting URL a failed clear's error can name — chosen by a server.
CLEANUP = "CLEANUP_URL_FIXTURE"
CLEANUP_URL = f"https://evil.example/{CLEANUP}"


def _clearing_fails(page: FakePlaywrightPage) -> None:
    from playwright.async_api import Error as PlaywrightError

    page.clear_interrupted_to = CLEANUP_URL
    page.clear_fails = PlaywrightError(
        f'Page.goto: Navigation to "about:blank" is interrupted by another navigation to '
        f'"{CLEANUP_URL}"'
    )


def _assert_withheld_and_uncleared(exc: BaseException, page: FakePlaywrightPage) -> None:
    message = str(exc)
    assert "withheld" in message and "cleanup=failed" in message
    assert "could NOT be cleared" in message, "a failed clear reported as a cleared page"
    assert CLEANUP not in message and MARKER not in message and "evil.example" not in message
    assert exc.__context__ is None and exc.__cause__ is None
    assert page.url == CLEANUP_URL, "the fixture did not leave the page uncleared"


@pytest.mark.parametrize("name", _READS)
async def test_a_withheld_result_stays_withheld_when_clearing_fails(
    tmp_path: Path, name: str
) -> None:
    """The reviewer's sequence: the read succeeds on a page that navigated
    off-list, the fence withholds it, and the clearing navigation is itself
    interrupted — Playwright's error names the interrupting URL. Before the fix
    that error replaced the withheld one and reached the agent."""
    page = _page_with_late_content()
    page.during[_PAGE_CALL[name]] = lambda: page.commit(OTHER)
    _clearing_fails(page)
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied) as caught:
        await _read(conn, name, tmp_path)
    _assert_withheld_and_uncleared(caught.value, page)


@pytest.mark.parametrize("name", _ERRORING)
async def test_a_withheld_error_stays_withheld_when_clearing_fails(
    tmp_path: Path, name: str
) -> None:
    page = FakePlaywrightPage(url=ALLOWED)
    _failing(name, page, lambda: page.commit(OTHER))
    _clearing_fails(page)
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied) as caught:
        await _call(conn, name)
    _assert_withheld_and_uncleared(caught.value, page)


@pytest.mark.parametrize("clearing_fails", [False, True])
async def test_a_rejected_screenshot_never_reaches_its_path(
    tmp_path: Path, clearing_fails: bool
) -> None:
    """Before the fix the capture was written to its path first and deleted
    only on the expected error, so a failed clear left it there."""
    page = _page_with_late_content()
    page.during["screenshot"] = lambda: page.commit(OTHER)
    if clearing_fails:
        _clearing_fails(page)
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied, match="withheld"):
        await conn.screenshot(path="shot.png")
    assert list((tmp_path / "dl").iterdir()) == [], "a rejected capture was left on disk"


async def test_a_page_found_off_list_that_cannot_be_cleared_says_so(tmp_path: Path) -> None:
    """The same at the start of a call, where every call checks the page: the
    error says clearing failed, carries none of Playwright's text, and the
    next call checks again — and clears it once clearing works."""
    page = FakePlaywrightPage(url=OTHER)
    _clearing_fails(page)
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserPageNotCleared) as caught:
        await conn.read_text("#x")
    assert CLEANUP not in str(caught.value) and caught.value.__context__ is None
    assert not [c for c in page.calls if c[0].startswith("locator")], "it read the page"
    page.clear_fails = None
    with pytest.raises(BrowserHostDenied, match="it was cleared"):
        await conn.read_text("#x")
    assert page.url == "about:blank"


@pytest.mark.parametrize(
    ("during", "cleanup"),
    [("off_list", "cleared"), ("off_list_and_back", "not_needed")],
)
async def test_the_withheld_status_reports_what_clearing_did(
    tmp_path: Path, during: str, cleanup: str
) -> None:
    page = _page_with_late_content()

    def navigate() -> None:
        page.commit(OTHER)
        if during == "off_list_and_back":
            page.commit(ALLOWED)

    page.during["inner_text"] = navigate
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied) as caught:
        await conn.read_text("#late")
    assert f"[document_changed; call=read; error=none; cleanup={cleanup}]" in str(caught.value)


async def test_the_model_never_sees_a_failed_clear(tmp_path: Path) -> None:
    """Through the real engine: the reviewer's probe, inverted. Before the fix
    the interrupting URL reached the model's next turn and the step output."""
    from playwright.async_api import Error as PlaywrightError

    from tests._bedrock_fakes import tool_use_response
    from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
    from workflow_platform.persistence import in_memory_repositories
    from workflow_platform.tools.browser import BrowserReadTextTool
    from workflow_platform.workflow import load_definition
    from workflow_platform.world import mock_world

    page = FakePlaywrightPage(url=ALLOWED)
    page.during["inner_text"] = lambda: page.commit(OTHER)
    page.raise_on_text["#late"] = PlaywrightError(DIAGNOSTIC)
    _clearing_fails(page)
    conn = PlaywrightConnector(page=page, downloads_dir=tmp_path)
    model = FakeBedrock(
        [
            tool_use_response(tool_uses=[("r1", "browser_read_text", {"selector": "#late"})]),
            text_response("done"),
        ]
    )
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog([BrowserReadTextTool()]),
        bedrock=model,
        world=mock_world(),
        browser_connector_factory=lambda: conn,
        system_capabilities=CapabilityPolicy(
            tools=["browser_read_text"], allowed_hosts=["allowed.example"]
        ),
    )
    definition = load_definition(
        {
            "id": "r4-cleanup",
            "name": "r4 cleanup",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "read",
                    "type": "agentic",
                    "goal": "read",
                    "model": "fixture-model",
                    "tools": ["browser_read_text"],
                }
            ],
            "edges": [],
        }
    )
    instance = await engine.run(definition)
    seen = str(model.calls[1]["messages"])
    assert "cleanup=failed" in seen, "the tool result did not reach the model at all"
    assert CLEANUP not in seen and MARKER not in seen
    (step,) = await repos.steps.list_by_instance(instance.id)
    assert CLEANUP not in str(step.output) and MARKER not in str(step.output)


# --- no refusal names a URL a page or server chose (round-5 review, R5-F1) ---

#: Where the page went, or where a server pointed — never the agent's choice.
CHOSEN = "SERVER_CHOSEN_URL_FIXTURE"
CHOSEN_URL = f"https://evil.example/{CHOSEN}"


@pytest.mark.parametrize("clearing_fails", [False, True])
async def test_a_call_starting_on_an_off_list_page_does_not_name_it(
    tmp_path: Path, clearing_fails: bool
) -> None:
    """Before the fix the start-of-call check named the page's URL, cleared or
    not — which is how a retry delivered what the withheld call kept back."""
    page = FakePlaywrightPage(url=CHOSEN_URL)
    if clearing_fails:
        page.clear_fails = RuntimeError("closed")
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied) as caught:
        await conn.read_text("#x")
    message = str(caught.value)
    assert CHOSEN not in message and "evil.example" not in message
    assert f"[page_not_allowed; {'error=RuntimeError; ' if clearing_fails else ''}" in message
    assert ("cleanup=failed" if clearing_fails else "cleanup=cleared") in message
    assert not [c for c in page.calls if c[0].startswith("locator")], "it read the page"


@pytest.mark.parametrize("second_clear_fails", [True, False])
async def test_a_retry_after_a_failed_clear_does_not_deliver_the_withheld_url(
    tmp_path: Path, second_clear_fails: bool
) -> None:
    """The reviewer's two-call sequence through the real engine and agent loop:
    the first read is withheld and clearing fails, leaving the page on the
    interrupting URL; the model retries. Before the fix the retry's refusal
    carried that URL to the model and the step output."""
    from playwright.async_api import Error as PlaywrightError

    from tests._bedrock_fakes import tool_use_response
    from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
    from workflow_platform.persistence import in_memory_repositories
    from workflow_platform.tools.browser import BrowserReadTextTool
    from workflow_platform.workflow import load_definition
    from workflow_platform.world import mock_world

    page = FakePlaywrightPage(url=ALLOWED)
    page.during["inner_text"] = lambda: page.commit(OTHER)
    page.raise_on_text["#late"] = PlaywrightError(DIAGNOSTIC)
    page.clear_interrupted_to = CHOSEN_URL
    page.clear_fails = PlaywrightError(
        f"Page.goto: interrupted by another navigation to {CHOSEN_URL}"
    )
    conn = PlaywrightConnector(page=page, downloads_dir=tmp_path)

    class SecondClearDecides(FakeBedrock):
        """Before the model's retry is executed, decide whether that clear works."""

        async def converse(
            self,
            model_id: str,
            messages: list[dict[str, Any]],
            system: list[dict[str, Any]] | None = None,
            tool_config: dict[str, Any] | None = None,
            inference_config: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if len(self.calls) == 1 and not second_clear_fails:
                page.clear_fails = None
            return await super().converse(model_id, messages, system, tool_config, inference_config)

    model = SecondClearDecides(
        [
            tool_use_response(tool_uses=[("r1", "browser_read_text", {"selector": "#late"})]),
            tool_use_response(tool_uses=[("r2", "browser_read_text", {"selector": "#late"})]),
            text_response("done"),
        ]
    )
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog([BrowserReadTextTool()]),
        bedrock=model,
        world=mock_world(),
        browser_connector_factory=lambda: conn,
        system_capabilities=CapabilityPolicy(
            tools=["browser_read_text"], allowed_hosts=["allowed.example"]
        ),
    )
    definition = load_definition(
        {
            "id": "r5-retry",
            "name": "r5 retry",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "read",
                    "type": "agentic",
                    "goal": "read",
                    "model": "fixture-model",
                    "tools": ["browser_read_text"],
                }
            ],
            "edges": [],
        }
    )
    instance = await engine.run(definition)
    assert len(model.calls) == 3
    retry_seen = str(model.calls[2]["messages"])
    assert retry_seen.count("page_not_allowed") == 1, "the retry's refusal never reached the model"
    assert ("cleanup=failed" if second_clear_fails else "cleanup=cleared") in retry_seen
    assert CHOSEN not in retry_seen and MARKER not in retry_seen
    (step,) = await repos.steps.list_by_instance(instance.id)
    assert CHOSEN not in str(step.output) and MARKER not in str(step.output)
    assert len([c for c in page.calls if c[0] == "locator.inner_text"]) == 1, "the retry read"


async def test_a_redirect_off_list_is_refused_without_naming_it(tmp_path: Path) -> None:
    conn, page = await _connector(tmp_path, "allowed.example")
    page.context.request.redirect_at[ALLOWED] = CHOSEN_URL
    with pytest.raises(BrowserHostDenied, match=r"source=redirect") as caught:
        await conn.fetch_url(ALLOWED)
    assert CHOSEN not in str(caught.value)


async def test_an_off_list_download_is_refused_without_naming_it(tmp_path: Path) -> None:
    page = FakePlaywrightPage(url=ALLOWED)
    page.expect_download_value = FakeDownload(url=CHOSEN_URL, suggested_filename="x.pdf")
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied, match=r"source=download") as caught:
        await conn.download_via_click("a#dl")
    assert CHOSEN not in str(caught.value)


async def test_a_url_the_agent_asked_for_is_still_named(tmp_path: Path) -> None:
    """The positive control: the agent chose this URL, and the refusal is more
    useful naming it."""
    conn, _ = await _connector(tmp_path, "allowed.example")
    with pytest.raises(BrowserHostDenied, match=r"source=request") as caught:
        await conn.navigate(OTHER)
    assert OTHER in str(caught.value)


_REFUSALS = {"BrowserHostDenied", "BrowserPageNotCleared", "BrowserCallFailed"}
_FORMATTABLE = {"failure", "withheld", "outcome", "status", "url", "_MAX_REDIRECTS"}


def _refusal_shape_problems(tree: Any) -> list[str]:
    """What the refusal tripwire objects to in `tree`: a refusal argument
    that is not literal text, a formatted name outside the fixed set, or the
    agent's `url` rebound."""
    import ast

    problems: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _REFUSALS
        ):
            for arg in node.args:
                if not isinstance(arg, ast.Constant | ast.JoinedStr):
                    problems.append(f"line {node.lineno}: built from {ast.unparse(arg)!r}")
                    continue
                for v in ast.walk(arg):
                    if isinstance(v, ast.FormattedValue):
                        name = ast.unparse(v.value)
                        if name not in _FORMATTABLE:
                            problems.append(f"line {node.lineno}: formats {name!r}")
        targets: list[Any] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.For):
            targets = [node.target]
        if "url" in {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}:
            problems.append(f"line {getattr(node, 'lineno', '?')}: `url` rebound")
    return problems


def test_refusals_are_built_from_literal_text_and_fixed_names() -> None:
    """A source-shape TRIPWIRE over the connector, not dataflow analysis.
    Every host refusal is built from literal text, formatting only names from
    a fixed set, and `url` — the agent's own argument, the one URL a refusal
    may name — is never rebound. What a tripwire cannot see, the tool
    boundary withholds anyway
    (`test_an_unanticipated_error_reaches_the_agent_as_its_class_only`)."""
    import ast

    from workflow_platform.connectors.browser import base, playwright_connector

    found = 0
    for module in (base, playwright_connector):
        tree = ast.parse(Path(module.__file__ or "").read_text())
        found += sum(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _REFUSALS
            for n in ast.walk(tree)
        )
        assert _refusal_shape_problems(tree) == []
    assert found >= 8, f"the tripwire found only {found} refusals — it is broken"


@pytest.mark.parametrize(
    "snippet",
    [
        'raise BrowserHostDenied("refused " + current)',
        'url = current\nraise BrowserHostDenied(f"refused {url}")',
        "raise BrowserHostDenied(message)",
    ],
)
def test_the_tripwire_rejects_the_shapes_the_reviewer_showed_passing(snippet: str) -> None:
    """The round-6 review showed each of these passed the previous check.
    Parsed only; nothing is executed or written to source."""
    import ast

    assert _refusal_shape_problems(ast.parse(snippet)) != []


# --- the tool boundary: an error nobody vouched for is withheld (R6-F1) ---


async def _tool_error(tmp_path: Path, raise_from: str, exc: Exception, *, policy: bool) -> str:
    """Run a browser tool whose page raises `exc`, and return the error the
    agent would see."""
    from workflow_platform.tools import Tool
    from workflow_platform.tools.browser import BrowserClickTool, BrowserNavigateTool

    page = FakePlaywrightPage(url=ALLOWED)
    tool: Tool
    if raise_from == "navigate":

        async def failing_goto(url: str, *, wait_until: str = "load") -> None:
            raise exc

        page.goto = failing_goto  # type: ignore[method-assign]
        tool, params = BrowserNavigateTool(), {"url": ALLOWED}
    else:
        page.raise_on_click["#x"] = exc
        tool, params = BrowserClickTool(), {"selector": "#x"}
    conn = PlaywrightConnector(downloads_dir=tmp_path / "dl", page=page)
    await conn.__aenter__()
    ctx = ToolContext(
        connectors={"browser": conn},
        capabilities=resolve_capabilities(
            CapabilityPolicy(allowed_hosts=["allowed.example"] if policy else None)
        ),
    )
    result = await tool.execute(params, ctx)
    assert not result.ok
    return result.error or ""


async def test_an_unanticipated_error_reaches_the_agent_as_its_class_only(tmp_path: Path) -> None:
    """The default the round-6 review asked for — not recognising one error
    string: under a host policy, an error the connector neither built nor
    vouched for (here, a navigation failing with text a server chose) reaches
    the agent as its class only."""
    error = await _tool_error(
        tmp_path, "navigate", RuntimeError(f"net::ERR_X at {CHOSEN_URL}"), policy=True
    )
    assert "[call_failed; error=RuntimeError]" in error
    assert CHOSEN not in error and "net::" not in error


async def test_without_a_policy_a_library_error_is_unchanged(tmp_path: Path) -> None:
    error = await _tool_error(tmp_path, "navigate", RuntimeError(f"at {CHOSEN_URL}"), policy=False)
    assert CHOSEN in error


async def test_an_error_about_the_checked_page_still_reaches_the_agent(tmp_path: Path) -> None:
    """The fence vouches for an error on the checked, unchanged page; the tool
    boundary passes it whole — the agent needs it to fix its selector."""
    from playwright.async_api import Error as PlaywrightError

    error = await _tool_error(tmp_path, "click", PlaywrightError(DIAGNOSTIC), policy=True)
    assert MARKER in error and "call_failed" not in error


def test_every_tool_error_goes_through_the_one_boundary() -> None:
    """A source-shape tripwire over `tools/browser.py`: every `except
    Exception` returns `_error(...)`, and an exception is formatted into text
    only inside `_error` or the upload tool's own refusal."""
    import ast

    import workflow_platform.tools.browser as browser_tools

    tree = ast.parse(Path(browser_tools.__file__ or "").read_text())
    handlers = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ExceptHandler)
        and isinstance(n.type, ast.Name)
        and n.type.id == "Exception"
        and n.name == "exc"
    ]
    assert len(handlers) >= 11, f"found only {len(handlers)} handlers — the tripwire is broken"
    for h in handlers:
        (stmt,) = h.body
        assert (
            isinstance(stmt, ast.Return)
            and isinstance(stmt.value, ast.Call)
            and ast.unparse(stmt.value.func) == "_error"
        ), f"line {h.lineno}: an error reaches the agent without the boundary"
    error_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_error")
    upload_handlers = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ExceptHandler)
        and isinstance(n.type, ast.Name)
        and n.type.id == "_UploadSourceError"
    ]
    exempt = {id(v) for root in [error_fn, *upload_handlers] for v in ast.walk(root)}
    stray = [
        v.lineno
        for v in ast.walk(tree)
        if isinstance(v, ast.FormattedValue)
        and "exc" in {n.id for n in ast.walk(v.value) if isinstance(n, ast.Name)}
        and id(v) not in exempt
    ]
    assert not stray, f"an exception formatted outside the boundary at lines {stray}"


async def test_an_action_that_leaves_and_returns_is_not_detected(tmp_path: Path) -> None:
    """R3-F2, the contract as stated rather than a defect: an action is judged
    by where the page ENDS. Leaving and returning to the checked URL during a
    successful action is not detected — failing every action that navigates
    would fail every link click. Pinned so a change to it is a decision."""
    page = FakePlaywrightPage(url=ALLOWED)

    def away_and_back() -> None:
        page.commit(OTHER)
        page.commit(ALLOWED)

    page.during["fill"] = away_and_back
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    await conn.fill("#late", "v")
    assert page.url == ALLOWED


async def test_the_model_never_sees_an_error_from_an_unchecked_page(tmp_path: Path) -> None:
    """Through the real engine and agent loop, only the page and the model
    faked: the reviewer's probe, inverted. Before the fix the quote reached
    the model's next turn and the stored step output."""
    from playwright.async_api import Error as PlaywrightError

    from tests._bedrock_fakes import tool_use_response
    from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
    from workflow_platform.persistence import in_memory_repositories
    from workflow_platform.tools.browser import BrowserReadTextTool
    from workflow_platform.workflow import load_definition
    from workflow_platform.world import mock_world

    page = FakePlaywrightPage(url=ALLOWED)
    page.during["inner_text"] = lambda: page.commit(OTHER)
    page.raise_on_text["#late"] = PlaywrightError(DIAGNOSTIC)
    conn = PlaywrightConnector(page=page, downloads_dir=tmp_path)
    model = FakeBedrock(
        [
            tool_use_response(tool_uses=[("r1", "browser_read_text", {"selector": "#late"})]),
            text_response("done"),
        ]
    )
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog([BrowserReadTextTool()]),
        bedrock=model,
        world=mock_world(),
        browser_connector_factory=lambda: conn,
        system_capabilities=CapabilityPolicy(
            tools=["browser_read_text"], allowed_hosts=["allowed.example"]
        ),
    )
    definition = load_definition(
        {
            "id": "r3-error",
            "name": "r3 error",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "read",
                    "type": "agentic",
                    "goal": "read",
                    "model": "fixture-model",
                    "tools": ["browser_read_text"],
                }
            ],
            "edges": [],
        }
    )
    instance = await engine.run(definition)
    assert len(model.calls) == 2
    seen = str(model.calls[1]["messages"])
    assert "withheld" in seen, "the tool result did not reach the model at all"
    assert MARKER not in seen
    (step,) = await repos.steps.list_by_instance(instance.id)
    assert MARKER not in str(step.output)
    assert page.url == "about:blank"


async def test_fetch_url_to_a_disallowed_host_makes_no_request(tmp_path: Path) -> None:
    conn, page = await _connector(tmp_path, "allowed.example")
    with pytest.raises(BrowserHostDenied):
        await conn.fetch_url(OTHER)
    assert page.context.request.gets == []


async def test_fetch_url_follows_redirects_one_checked_hop_at_a_time(tmp_path: Path) -> None:
    """The API context follows redirects by itself and is not intercepted by
    routing, so the connector disables that and checks every Location."""
    conn, page = await _connector(tmp_path, "allowed.example")
    req = page.context.request
    req.redirect_at[ALLOWED] = "/next"
    req.redirect_at["https://allowed.example/next"] = OTHER
    with pytest.raises(BrowserHostDenied):
        await conn.fetch_url(ALLOWED)
    assert req.gets == [ALLOWED, "https://allowed.example/next"], "the off-list hop was fetched"
    assert all(kw.get("max_redirects") == 0 for kw in req.get_kwargs)


async def test_fetch_url_on_list_redirects_still_work(tmp_path: Path) -> None:
    conn, page = await _connector(tmp_path, "allowed.example")
    req = page.context.request
    req.redirect_at[ALLOWED] = "https://allowed.example/final.pdf"
    req.body_at["https://allowed.example/final.pdf"] = b"%PDF"
    got = await conn.fetch_url(ALLOWED, dest_filename="f.pdf")
    assert Path(got.local_path).read_bytes() == b"%PDF"


async def test_a_download_from_off_list_is_refused_before_it_is_saved(tmp_path: Path) -> None:
    page = FakePlaywrightPage(url=ALLOWED)
    page.expect_download_value = FakeDownload(url=OTHER, suggested_filename="x.pdf")
    conn, page = await _connector(tmp_path, "allowed.example", page=page)
    with pytest.raises(BrowserHostDenied):
        await conn.download_via_click("a#dl")
    assert not (tmp_path / "dl" / "x.pdf").exists()


async def test_no_policy_keeps_unrestricted_behaviour(tmp_path: Path) -> None:
    page = FakePlaywrightPage()
    conn = PlaywrightConnector(downloads_dir=tmp_path / "dl", page=page)
    await conn.__aenter__()
    await conn.navigate("file:///tmp/local.html")
    page.context.request.redirect_at[ALLOWED] = OTHER
    await conn.fetch_url(ALLOWED, dest_filename="f")
    assert page.context.request.get_kwargs == [{}, {}], "an unrestricted fetch changed shape"


# --- the tool layer: one helper carries the step's policy -------------------


async def test_the_step_capabilities_reach_the_connector_through_the_tool(
    tmp_path: Path,
) -> None:
    page = FakePlaywrightPage()
    conn = PlaywrightConnector(downloads_dir=tmp_path / "dl", page=page)
    await conn.__aenter__()
    restricted = ToolContext(
        connectors={"browser": conn},
        capabilities=resolve_capabilities(CapabilityPolicy(allowed_hosts=["allowed.example"])),
    )
    result = await BrowserNavigateTool().execute({"url": OTHER}, context=restricted)
    assert not result.ok and "allowed_hosts" in (result.error or "")
    result = await BrowserFetchUrlTool().execute({"url": OTHER}, context=restricted)
    assert not result.ok and "allowed_hosts" in (result.error or "")

    # The connector lives for the run; the NEXT step's context resets it.
    open_ctx = ToolContext(connectors={"browser": conn}, capabilities=None)
    assert (await BrowserNavigateTool().execute({"url": OTHER}, context=open_ctx)).ok


# --- the route handler (the live browser drives it; its decision is here) ---


class _Req:
    def __init__(self, url: str, navigation: bool) -> None:
        self.url = url
        self._nav = navigation

    def is_navigation_request(self) -> bool:
        return self._nav


class _Route:
    def __init__(self, url: str, navigation: bool) -> None:
        self.request = _Req(url, navigation)
        self.outcome = ""

    async def abort(self, reason: str) -> None:
        self.outcome = f"abort:{reason}"

    async def continue_(self) -> None:
        self.outcome = "continue"


@pytest.mark.parametrize(
    ("url", "navigation", "outcome"),
    [
        (OTHER, True, "abort:blockedbyclient"),  # a click / submit / goto off-list
        (ALLOWED, True, "continue"),
        (OTHER, False, "continue"),  # a page's own subresource — not the agent's choice
    ],
)
async def test_route_aborts_only_off_list_navigations(
    tmp_path: Path, url: str, navigation: bool, outcome: str
) -> None:
    conn, _ = await _connector(tmp_path, "allowed.example")
    route = _Route(url, navigation)
    await conn._route_navigation(route)
    assert route.outcome == outcome


# --- browser outputs stay in the run's downloads directory ---------------------


@pytest.mark.parametrize("name", ["../../escape.bin", "/etc/escape.bin", "sub/dir/escape.bin"])
async def test_fetch_url_cannot_write_outside_the_downloads_directory(
    tmp_path: Path, name: str
) -> None:
    """`downloads_dir / "/etc/x"` IS `/etc/x` — an absolute name replaced the
    directory outright, and `../` climbed out of it."""
    page = FakePlaywrightPage()
    page.context.request.body_at["https://allowed.example/f"] = b"PAYLOAD"
    conn = PlaywrightConnector(downloads_dir=tmp_path / "dl", page=page)
    await conn.__aenter__()
    got = await conn.fetch_url("https://allowed.example/f", dest_filename=name)
    assert Path(got.local_path) == tmp_path / "dl" / "escape.bin"
    assert not (tmp_path / "escape.bin").exists()


async def test_a_screenshot_path_is_confined_to_the_downloads_directory(tmp_path: Path) -> None:
    page = FakePlaywrightPage()
    conn = PlaywrightConnector(downloads_dir=tmp_path / "dl", page=page)
    await conn.__aenter__()
    shot = await conn.screenshot(path=str(tmp_path / "outside" / "shot.png"))
    assert shot.local_path == str(tmp_path / "dl" / "shot.png")


async def test_a_server_suggested_download_name_is_confined(tmp_path: Path) -> None:
    page = FakePlaywrightPage(url=ALLOWED)
    page.expect_download_value = FakeDownload(url=ALLOWED, suggested_filename="../../evil.pdf")
    conn = PlaywrightConnector(downloads_dir=tmp_path / "dl", page=page)
    await conn.__aenter__()
    got = await conn.download_via_click("a#dl")
    assert Path(got.local_path) == tmp_path / "dl" / "evil.pdf"
    assert not (tmp_path / "evil.pdf").exists()


async def test_a_bare_dot_dot_name_is_refused(tmp_path: Path) -> None:
    conn = PlaywrightConnector(downloads_dir=tmp_path / "dl", page=FakePlaywrightPage())
    await conn.__aenter__()
    with pytest.raises(ValueError, match="not a usable file name"):
        await conn.fetch_url("https://allowed.example/f", dest_filename="..")


# --- the host check and the browser must read the same host -------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.example\\@allowed.example/x",  # the red-team's: WHATWG ends the host at `\`
        "http://allowed.example\t.evil/x",  # WHATWG strips tabs
        "http://user@allowed.example/x",  # userinfo
        "http://allowed%2eexample/x",  # percent-escape in the authority
        "http://allowed。example/x",  # U+3002, which IDNA maps to a dot
        "https://allowed.example/a b",  # whitespace
    ],
)
def test_urls_the_two_parsers_could_read_differently_are_refused(url: str) -> None:
    from workflow_platform.connectors.browser import url_host_allowed

    assert not url_host_allowed(url, lambda host: host in {"allowed.example", "evil.example"})


def test_the_host_check_refuses_a_url_it_cannot_parse() -> None:
    """Total, not raising (round-6 review): Python cannot parse a bracketed
    host that is not an IP, and raised with the server's text; the check now
    refuses such a URL like any other it cannot vouch for."""
    allowed = resolve_capabilities(CapabilityPolicy(allowed_hosts=["*"])).host_allowed
    assert url_host_allowed(f"https://[{CHOSEN}]/x", allowed) is False


def test_an_ordinary_url_still_passes() -> None:
    from workflow_platform.connectors.browser import url_host_allowed

    assert url_host_allowed(
        "https://allowed.example:8443/path/file.pdf?q=1#top", lambda h: h == "allowed.example"
    )


# --- the platform's own model calls are bounded too ---------------------------


async def test_a_drafts_question_suggestion_cannot_exceed_the_system_cap() -> None:
    """Independent red-team (2026-10-29): `questions.suggest.max_output_tokens`
    is set by the definition and bounded only below, so a scaffolded draft sent
    `maxTokens: 100000` on a call that never passed through the clamp."""
    from workflow_platform.elicitation.store import InMemoryShadowStore
    from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
    from workflow_platform.persistence import in_memory_repositories
    from workflow_platform.workflow import load_definition_from_yaml
    from workflow_platform.world import mock_world

    definition = load_definition_from_yaml(
        """
id: q
name: q
trigger: {type: manual}
steps:
  - {id: a, type: agentic, goal: "say hi", model: some-model, tools: [],
     policy: {inference_config: {maxTokens: 100000}}}
questions:
  suggest: {model: a-draft-chosen-model, inputs: [trigger.subject], max_output_tokens: 100000}
  subject: s
  recipient: r
  catalog: {topics: []}
"""
    )
    fake = FakeBedrock([text_response("hi"), text_response("{}")])
    engine = WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=default_function_registry(),
        tools=ToolCatalog([]),
        bedrock=fake,
        world=mock_world(),
        system_capabilities=CapabilityPolicy(max_tokens_per_call=4096),
        question_store=InMemoryShadowStore(),
    )
    await engine.run(definition, trigger_payload={"subject": "x"})
    sent = {c["model_id"]: c["inference_config"] for c in fake.calls}
    assert sent["a-draft-chosen-model"] == {"maxTokens": 4096}, (
        "the suggestion call escaped the cap"
    )
    assert sent["some-model"] == {"maxTokens": 4096}


async def test_memory_distillation_carries_the_cap_and_is_unchanged_without_one() -> None:
    """It sent no output cap at all, over third-party mail content."""
    import asyncio

    from workflow_platform.memory.learned import _BedrockComplete

    fake = FakeBedrock([text_response("a"), text_response("b")])
    complete = _BedrockComplete(fake, MODEL)
    complete.loop = asyncio.get_running_loop()
    for cap in (4096, None):
        complete.max_output_tokens = cap
        await asyncio.to_thread(complete, "prompt")
    assert fake.calls[0]["inference_config"] == {"maxTokens": 4096}
    assert fake.calls[1]["inference_config"] is None, "an uncapped request changed shape"


# --- a parallel sibling cannot swap a step's host policy mid-call (review R2) -----

_FIRST = "https://narrow.example/start"
_WIDE = "https://wide.example/data"


async def _parallel_host_run(tmp_path: Path, *, with_sibling: bool) -> tuple[str, list[str]]:
    """The reviewer's real-engine reproduction. `narrow` fetches a URL whose
    response is a redirect to `wide.example`, which its own policy forbids; the
    first response is HELD so a parallel sibling with the wider policy can act
    in the middle. Before the fix the sibling's call installed its policy on
    the shared connector, the held redirect was judged by it, and the narrow
    step saved the wide host's bytes.

    The hold has a timeout: with the fix the sibling cannot start until the
    narrow call ends, so waiting for it would deadlock. Either way the
    redirect is judged after the sibling had its chance to act."""
    import asyncio
    import contextlib

    from tests._bedrock_fakes import tool_use_response
    from tests._browser_fakes import FakeAPIResponse, FakeRequest
    from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
    from workflow_platform.persistence import in_memory_repositories
    from workflow_platform.tools.browser import BrowserReadTextTool
    from workflow_platform.workflow import load_definition
    from workflow_platform.world import mock_world

    entered, release = asyncio.Event(), asyncio.Event()
    gets: list[str] = []

    class HeldRequest(FakeRequest):
        async def get(self, url: str, **kwargs: Any) -> FakeAPIResponse:
            gets.append(url)
            if url == _FIRST:
                entered.set()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(release.wait(), 0.5)
                return FakeAPIResponse(b"", 302, {"location": _WIDE})
            return FakeAPIResponse(b"WIDE-HOST-BYTES")

    page = FakePlaywrightPage()
    page.context.request = HeldRequest()
    connector = PlaywrightConnector(page=page, downloads_dir=tmp_path)

    class ByGoal(FakeBedrock):
        def __init__(self) -> None:
            super().__init__([])
            self.turns: dict[str, int] = {}

        async def converse(
            self,
            model_id: str,
            messages: list[dict[str, Any]],
            system: list[dict[str, Any]] | None = None,
            tool_config: dict[str, Any] | None = None,
            inference_config: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            assert system is not None
            goal = str(system[0]["text"])
            self.turns[goal] = self.turns.get(goal, 0) + 1
            if self.turns[goal] > 1:
                if goal.startswith("SIBLING"):
                    release.set()
                return text_response("done")
            if goal.startswith("SIBLING"):
                await entered.wait()
                return tool_use_response(
                    tool_uses=[("s", "browser_read_text", {"selector": "body"})]
                )
            return tool_use_response(
                tool_uses=[("n", "browser_fetch_url", {"url": _FIRST, "dest_filename": "got.txt"})]
            )

    steps: list[dict[str, object]] = [
        {
            "id": "narrow",
            "type": "agentic",
            "goal": "NARROW",
            "model": MODEL,
            "tools": ["browser_fetch_url"],
            "capabilities": {"allowed_hosts": ["narrow.example"]},
        }
    ]
    if with_sibling:
        steps.append(
            {
                "id": "sibling",
                "type": "agentic",
                "goal": "SIBLING",
                "model": MODEL,
                "tools": ["browser_read_text"],
            }
        )
    engine = WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=default_function_registry(),
        tools=ToolCatalog([BrowserFetchUrlTool(), BrowserReadTextTool()]),
        bedrock=ByGoal(),
        world=mock_world(),
        browser_connector_factory=lambda: connector,
        system_capabilities=CapabilityPolicy(
            tools=["browser_fetch_url", "browser_read_text"],
            allowed_hosts=["narrow.example", "wide.example"],
        ),
    )
    definition = load_definition(
        {"id": "r2", "name": "r2", "trigger": {"type": "manual"}, "steps": steps, "edges": []}
    )
    instance = await asyncio.wait_for(engine.run(definition), 10)
    narrow = next(
        s
        for s in await engine.repositories.steps.list_by_instance(instance.id)
        if s.step_id == "narrow"
    )
    assert narrow.output is not None
    return str(narrow.output["tool_calls"]), gets


async def test_a_parallel_sibling_cannot_widen_a_step_host_policy_mid_call(tmp_path: Path) -> None:
    record, gets = await _parallel_host_run(tmp_path, with_sibling=True)
    assert gets == [_FIRST], "the narrow step requested a host its policy forbids"
    assert "allowed_hosts" in record
    assert not (tmp_path / "got.txt").exists()


async def test_the_serial_twin_refuses_the_same_redirect(tmp_path: Path) -> None:
    """Same step, no sibling: the refusal above is the step's own policy."""
    record, gets = await _parallel_host_run(tmp_path, with_sibling=False)
    assert gets == [_FIRST]
    assert "allowed_hosts" in record
    assert not (tmp_path / "got.txt").exists()


def test_every_browser_tool_acts_only_inside_one_policy_operation() -> None:
    """Derived from the source: every connector call a browser tool makes sits
    inside `async with connector.operation(_host_policy(context))`. A new tool
    that calls the connector outside one fails here; the engine tests above
    show what the scope buys."""
    import ast

    import workflow_platform.tools.browser as browser_tools

    tree = ast.parse(Path(browser_tools.__file__).read_text())
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def scoped(node: ast.AST) -> bool:
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.AsyncWith):
                for item in node.items:
                    call = item.context_expr
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "operation"
                        and ast.unparse(call.args[0]) == "_host_policy(context)"
                    ):
                        return True
        return False

    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Await)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute)
        and isinstance(n.value.func.value, ast.Name)
        and n.value.func.value.id == "connector"
    ]
    assert len(calls) >= 11, f"derivation found only {len(calls)} connector calls — it is broken"
    bare = [n.lineno for n in calls if not scoped(n)]
    assert not bare, f"browser connector called outside a policy operation at lines {bare}"
