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
from tests._browser_fakes import FakeDownload, FakePlaywrightPage
from workflow_platform.agent import Agent
from workflow_platform.connectors.browser import BrowserHostDenied, PlaywrightConnector
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
