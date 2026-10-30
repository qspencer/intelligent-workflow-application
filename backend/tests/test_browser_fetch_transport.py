"""`fetch_url` against Playwright's REAL HTTP client and a loopback server.

Round-6 external review, R6-F1: a redirect whose response is cut short makes
Playwright raise before the connector holds a response, and its error carries
a call log with the response headers — the off-list `Location` included. A
malformed `Location` made Python's URL parser raise with the server's text.
Both reached the model. These run the actual `APIRequestContext` (no Chromium
needed — the request API runs in Playwright's driver) against a server that
misbehaves on purpose; only the page object is an adapter around it.

Each case beside its twin: a complete off-list redirect (refused, bounded), and
a complete redirect to an allowed host (fetched).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from playwright.async_api import async_playwright

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.connectors.browser import PlaywrightConnector
from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.security.capabilities import CapabilityPolicy, resolve_capabilities
from workflow_platform.tools import ToolContext
from workflow_platform.tools.browser import BrowserFetchUrlTool
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

MARKER = "OFFLIST_LOCATION_FIXTURE"
MALFORMED = "SERVER_CHOSEN_HOST_FIXTURE"


@dataclass
class _Site:
    kind: str
    hits: list[str] = field(default_factory=list)
    allowed: str = ""
    connector: Any = None
    context: Any = None

    @property
    def requested(self) -> str:
        return f"{self.allowed}/start"


@asynccontextmanager
async def _site(tmp_path: Path, kind: str) -> AsyncIterator[_Site]:
    site = _Site(kind)
    tasks: set[asyncio.Task[Any]] = set()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        try:
            line = (await reader.readuntil(b"\r\n\r\n")).splitlines()[0].decode()
            site.hits.append(line)
            if line.split()[1] == "/start":
                location = {
                    "malformed": f"https://[{MALFORMED}]/download",
                    "allowed": f"{site.allowed}/file",
                }.get(kind, f"http://localhost:{port}/{MARKER}")
                # "truncated" declares a body it never finishes sending.
                length, body = (100, b"x") if kind == "truncated" else (0, b"")
                writer.write(
                    (
                        "HTTP/1.1 302 Found\r\n"
                        f"Location: {location}\r\n"
                        f"Content-Length: {length}\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode()
                    + body
                )
            else:
                body = b"ALLOWED_DOWNLOAD_FIXTURE"
                writer.write(
                    f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n".encode()
                    + body
                )
            await writer.drain()
            if kind == "truncated":
                await asyncio.sleep(0.05)  # headers arrive before EOF cuts the body
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    site.allowed = f"http://127.0.0.1:{port}"
    async with server, async_playwright() as playwright:
        request = await playwright.request.new_context(timeout=2000)
        try:
            page = SimpleNamespace(context=SimpleNamespace(request=request))
            site.connector = PlaywrightConnector(page=page, downloads_dir=tmp_path)
            site.context = ToolContext(
                world=mock_world(),
                connectors={"browser": site.connector},
                capabilities=resolve_capabilities(CapabilityPolicy(allowed_hosts=["127.0.0.1"])),
            )
            yield site
        finally:
            await request.dispose()
            if tasks:
                await asyncio.gather(*tasks)


async def _fetch(site: _Site) -> Any:
    return await BrowserFetchUrlTool().execute(
        {"url": site.requested, "dest_filename": "download.bin"}, site.context
    )


async def test_a_truncated_redirect_does_not_deliver_its_location(tmp_path: Path) -> None:
    """The reviewer's sequence. Before the fix the tool error was Playwright's
    `aborted` call log, headers and off-list `Location` included."""
    async with _site(tmp_path, "truncated") as site:
        result = await _fetch(site)
    assert not result.ok
    assert "[call_failed; call=fetch; error=Error]" in (result.error or "")
    assert MARKER not in (result.error or "") and "localhost" not in (result.error or "")
    assert site.hits == ["GET /start HTTP/1.1"], "the off-list hop was requested"
    assert list(tmp_path.iterdir()) == []


async def test_the_connector_itself_withholds_a_transport_error(tmp_path: Path) -> None:
    """The fetch boundary, below the tool: the tool boundary would also reduce
    this error, but the connector does not rely on it — another caller of
    `fetch_url` gets a bounded error too."""
    from workflow_platform.connectors.browser import BrowserCallFailed

    async with _site(tmp_path, "truncated") as site:
        policy = site.context.capabilities.host_allowed
        async with site.connector.operation(policy):
            with pytest.raises(BrowserCallFailed) as caught:
                await site.connector.fetch_url(site.requested)
    assert MARKER not in str(caught.value) and caught.value.__context__ is None


async def test_a_malformed_location_is_refused_without_its_text(tmp_path: Path) -> None:
    """Before the fix Python's parser raised, quoting the server's host text."""
    async with _site(tmp_path, "malformed") as site:
        result = await _fetch(site)
    assert not result.ok and "source=redirect" in (result.error or "")
    assert MALFORMED not in (result.error or "")
    assert site.hits == ["GET /start HTTP/1.1"]
    assert list(tmp_path.iterdir()) == []


async def test_a_complete_off_list_redirect_is_refused(tmp_path: Path) -> None:
    async with _site(tmp_path, "complete") as site:
        result = await _fetch(site)
    assert not result.ok and "source=redirect" in (result.error or "")
    assert MARKER not in (result.error or "")
    assert site.hits == ["GET /start HTTP/1.1"]


async def test_a_redirect_to_an_allowed_host_is_fetched(tmp_path: Path) -> None:
    """The positive control: the boundary refuses nothing it should fetch."""
    async with _site(tmp_path, "allowed") as site:
        result = await _fetch(site)
    assert result.ok, result.error
    assert (tmp_path / "download.bin").read_bytes() == b"ALLOWED_DOWNLOAD_FIXTURE"
    assert site.hits == ["GET /start HTTP/1.1", "GET /file HTTP/1.1"]


@pytest.mark.parametrize("kind", ["truncated", "malformed"])
async def test_the_model_never_sees_a_transport_or_parse_error(tmp_path: Path, kind: str) -> None:
    """Through the real engine and agent loop, only the model faked: neither
    the next model request nor the stored step output carries the marker."""
    async with _site(tmp_path, kind) as site:
        model = FakeBedrock(
            [
                tool_use_response(tool_uses=[("f1", "browser_fetch_url", {"url": site.requested})]),
                text_response("done"),
            ]
        )
        repos = in_memory_repositories()
        engine = WorkflowEngine(
            repositories=repos,
            functions=default_function_registry(),
            tools=ToolCatalog([BrowserFetchUrlTool()]),
            bedrock=model,
            world=mock_world(),
            browser_connector_factory=lambda: site.connector,
            system_capabilities=CapabilityPolicy(
                tools=["browser_fetch_url"], allowed_hosts=["127.0.0.1"]
            ),
        )
        definition = load_definition(
            {
                "id": "r6-transport",
                "name": "r6 transport",
                "trigger": {"type": "manual"},
                "steps": [
                    {
                        "id": "fetch",
                        "type": "agentic",
                        "goal": "fetch",
                        "model": "fixture-model",
                        "tools": ["browser_fetch_url"],
                    }
                ],
                "edges": [],
            }
        )
        instance = await engine.run(definition)
    seen = str(model.calls[1]["messages"])
    assert "browser_fetch_url failed" in seen, "the tool result never reached the model"
    for marker in (MARKER, MALFORMED):
        assert marker not in seen
    (step,) = await repos.steps.list_by_instance(instance.id)
    assert MARKER not in str(step.output) and MALFORMED not in str(step.output)
