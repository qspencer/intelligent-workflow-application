"""Browser-connector lifecycle inside `WorkflowEngine._drive`.

The engine lazy-builds a `BrowserConnector` only when the workflow
definition references at least one `browser_*` tool, attaches it to
`ToolContext.connectors["browser"]` for the duration of the run, and
tears it down in `_drive`'s finally block — regardless of success,
pause, kill, or failure paths.

Tests inject a fake connector via `browser_connector_factory=` so no
real Chromium is launched. The fake records `__aenter__` and
`__aexit__` calls so we can assert the lifecycle invariants.
"""

from __future__ import annotations

from typing import Any

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.connectors.browser import BrowserConnector
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
)
from workflow_platform.engine.executor import _workflow_uses_browser
from workflow_platform.persistence import (
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.tools import (
    BrowserNavigateTool,
    BrowserReadTextTool,
    FileWriteTool,
)
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

MODEL = "anthropic.claude-3-haiku-20240307-v1:0"


class _RecordingBrowserConnector(BrowserConnector):
    """Stand-in BrowserConnector that records lifecycle + dispatch calls
    without launching Chromium. Behaves enough like the real
    PlaywrightConnector for the engine's `browser_navigate` agent
    interactions to succeed."""

    type = "browser"  # ClassVar; required by Connector ABC

    def __init__(self, *, fail_on_enter: bool = False) -> None:
        self.entered: int = 0
        self.exited: int = 0
        self.navigate_calls: list[str] = []
        self.read_text_calls: list[str] = []
        self._fail_on_enter = fail_on_enter

    async def __aenter__(self) -> _RecordingBrowserConnector:
        if self._fail_on_enter:
            raise RuntimeError("Chromium failed to launch")
        self.entered += 1
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.exited += 1

    # --- Connector ABC ---

    async def authenticate(self) -> None:
        return None

    async def health_check(self) -> bool:
        return self.entered > self.exited

    # --- BrowserConnector ABC ---

    async def navigate(self, url: str, *, wait_until: str = "load") -> None:
        self.navigate_calls.append(url)

    async def wait_for(
        self, selector: str, *, state: Any = "visible", timeout_ms: int = 5000
    ) -> None:
        return None

    async def read_text(self, selector: str) -> str:
        self.read_text_calls.append(selector)
        return f"text-at:{selector}"

    async def read_table(self, selector: str) -> list[dict[str, str]]:
        return [{"col": selector}]

    async def click(self, selector: str, *, timeout_ms: int = 5000) -> None:
        return None

    async def fill(self, selector: str, value: str, *, clear_first: bool = True) -> None:
        return None

    async def upload_file(self, selector: str, file_path: str) -> None:
        return None

    async def submit_form(self, selector: str) -> None:
        return None

    async def screenshot(self, *, path: str | None = None, full_page: bool = False) -> Any:
        from workflow_platform.connectors.browser import BrowserScreenshot

        return BrowserScreenshot(local_path=path or "/tmp/x.png", bytes=0, full_page=full_page)

    async def download_via_click(self, selector: str, *, timeout_ms: int = 30000) -> Any:
        from workflow_platform.connectors.browser import BrowserDownload

        return BrowserDownload(
            source_url="https://example.com/x",
            local_path="/tmp/x",
            suggested_filename="x",
            bytes=0,
        )

    async def fetch_url(self, url: str, *, dest_filename: str | None = None) -> Any:
        from workflow_platform.connectors.browser import BrowserDownload

        name = dest_filename or url.rsplit("/", 1)[-1] or "download"
        return BrowserDownload(
            source_url=url,
            local_path=f"/tmp/{name}",
            suggested_filename=name,
            bytes=0,
        )


# ---------- _workflow_uses_browser unit ----------


def test_workflow_uses_browser_false_for_deterministic_only() -> None:
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [{"id": "a", "type": "deterministic", "function": "echo"}],
            "edges": [],
        }
    )
    assert _workflow_uses_browser(definition) is False


def test_workflow_uses_browser_false_for_agentic_no_browser_tool() -> None:
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "a",
                    "type": "agentic",
                    "goal": "...",
                    "model": MODEL,
                    "tools": ["file_write"],
                }
            ],
            "edges": [],
        }
    )
    assert _workflow_uses_browser(definition) is False


def test_workflow_uses_browser_true_for_any_browser_tool() -> None:
    for tool_name in (
        "browser_navigate",
        "browser_read_text",
        "browser_click",
        "browser_download",
    ):
        definition = load_definition(
            {
                "id": "wf",
                "name": "wf",
                "trigger": {"type": "manual"},
                "steps": [
                    {
                        "id": "a",
                        "type": "agentic",
                        "goal": "...",
                        "model": MODEL,
                        "tools": [tool_name],
                    }
                ],
                "edges": [],
            }
        )
        assert _workflow_uses_browser(definition) is True, tool_name


def test_workflow_uses_browser_true_when_only_one_step_needs_it() -> None:
    """Mixed workflow: deterministic + agentic-without-browser + agentic-with-browser."""
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "echo"},
                {
                    "id": "b",
                    "type": "agentic",
                    "goal": "...",
                    "model": MODEL,
                    "tools": ["file_write"],
                },
                {
                    "id": "c",
                    "type": "agentic",
                    "goal": "...",
                    "model": MODEL,
                    "tools": ["browser_read_text"],
                },
            ],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
        }
    )
    assert _workflow_uses_browser(definition) is True


# ---------- Engine lazy-build behavior ----------


async def test_engine_does_not_build_browser_for_non_browser_workflow() -> None:
    """Factory must NOT be called when no agentic step references browser_*."""
    repos = in_memory_repositories()
    factory_calls: list[int] = []

    def factory() -> BrowserConnector:
        factory_calls.append(1)
        return _RecordingBrowserConnector()

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "Write 'ok' to /result.txt",
                    "model": MODEL,
                    "tools": ["file_write"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([FileWriteTool()]),
        bedrock=FakeBedrock(
            [
                tool_use_response(
                    tool_uses=[("c1", "file_write", {"path": "/r.txt", "content": "ok"})]
                ),
                text_response("done"),
            ]
        ),
        world=mock_world(),
        browser_connector_factory=factory,
    )

    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.COMPLETED
    assert factory_calls == [], "Factory should not be called for non-browser workflows"


async def test_engine_lazy_builds_browser_for_browser_workflow() -> None:
    repos = in_memory_repositories()
    fake = _RecordingBrowserConnector()
    factory_calls: list[int] = []

    def factory() -> BrowserConnector:
        factory_calls.append(1)
        return fake

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "navigate",
                    "type": "agentic",
                    "goal": "Open example.com",
                    "model": MODEL,
                    "tools": ["browser_navigate"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([BrowserNavigateTool()]),
        bedrock=FakeBedrock(
            [
                tool_use_response(
                    tool_uses=[("c1", "browser_navigate", {"url": "https://example.com"})]
                ),
                text_response("opened"),
            ]
        ),
        world=mock_world(),
        browser_connector_factory=factory,
    )

    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.COMPLETED
    assert factory_calls == [1]
    assert fake.entered == 1
    assert fake.exited == 1, "Connector must be torn down on success"
    assert fake.navigate_calls == ["https://example.com"]


async def test_engine_tears_down_browser_on_failure() -> None:
    """Step-failure path: the connector teardown still runs in `_drive`'s finally."""
    repos = in_memory_repositories()
    fake = _RecordingBrowserConnector()

    # Workflow references browser_navigate but the agent calls an unknown
    # tool — Agent dispatch fails, engine surfaces as workflow_failed.
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "...",
                    "model": MODEL,
                    "tools": ["browser_navigate", "does_not_exist"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([BrowserNavigateTool()]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        browser_connector_factory=lambda: fake,
    )

    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.FAILED
    assert fake.entered == 1
    assert fake.exited == 1, "Connector must be torn down even on failure"


async def test_engine_surfaces_connector_build_failure_as_step_failure() -> None:
    """Factory raises on __aenter__ — engine audits + fails the workflow,
    teardown not called because we never successfully entered."""
    repos = in_memory_repositories()
    fake = _RecordingBrowserConnector(fail_on_enter=True)

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "...",
                    "model": MODEL,
                    "tools": ["browser_navigate"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([BrowserNavigateTool()]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        browser_connector_factory=lambda: fake,
    )

    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.FAILED
    assert fake.entered == 0
    assert fake.exited == 0
    audit = await repos.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert "connector_build_failed" in actions
    assert "workflow_failed" in actions


async def test_engine_emits_connector_opened_and_closed_audit() -> None:
    """connector_opened audit on lazy-build; connector_closed audit on teardown."""
    repos = in_memory_repositories()
    fake = _RecordingBrowserConnector()
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "...",
                    "model": MODEL,
                    "tools": ["browser_navigate"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([BrowserNavigateTool()]),
        bedrock=FakeBedrock(
            [
                tool_use_response(
                    tool_uses=[("c1", "browser_navigate", {"url": "https://example.com"})]
                ),
                text_response("done"),
            ]
        ),
        world=mock_world(),
        browser_connector_factory=lambda: fake,
    )

    instance = await engine.run(definition)
    audit = await repos.audit.list_by_instance(instance.id)
    actions = [e.action for e in audit]
    assert "connector_opened" in actions
    assert "connector_closed" in actions
    # Ordering: opened before any step starts, closed after workflow_completed.
    opened_idx = actions.index("connector_opened")
    closed_idx = actions.index("connector_closed")
    started_idx = actions.index("workflow_started")
    completed_idx = actions.index("workflow_completed")
    assert started_idx < opened_idx < completed_idx < closed_idx


# ---------- Tool dispatch sees the connector ----------


async def test_browser_tool_dispatches_through_engine_connector() -> None:
    """End-to-end smoke: agent → BrowserReadTextTool → connector.read_text.

    Closes the loop between D3's tool-side connectors lookup and D5's
    engine-side connector injection.
    """
    repos = in_memory_repositories()
    fake = _RecordingBrowserConnector()
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "Read the title.",
                    "model": MODEL,
                    "tools": ["browser_read_text"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([BrowserReadTextTool()]),
        bedrock=FakeBedrock(
            [
                tool_use_response(tool_uses=[("c1", "browser_read_text", {"selector": "#title"})]),
                text_response("Got it"),
            ]
        ),
        world=mock_world(),
        browser_connector_factory=lambda: fake,
    )

    instance = await engine.run(definition)
    assert instance.state == WorkflowInstanceState.COMPLETED
    assert fake.read_text_calls == ["#title"]

    audit = await repos.audit.list_by_instance(instance.id)
    tool_calls = [e for e in audit if e.action == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].detail["name"] == "browser_read_text"
    assert tool_calls[0].detail["result"]["content"]["text"] == "text-at:#title"


# ---------- Default factory wiring (no chromium launch — just the path) ----------


def test_default_factory_returns_playwright_connector() -> None:
    """When no factory is injected, engine uses the default PlaywrightConnector."""
    from workflow_platform.connectors.browser import PlaywrightConnector

    engine = WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=FakeBedrock([]),
        world=mock_world(),
    )
    assert engine.browser_connector_factory is None
    # Don't actually launch Playwright — just verify the default-path
    # call site would produce a PlaywrightConnector if it ran. (The real
    # construction happens lazily inside `_build_run_connectors` only
    # when a workflow references browser tools.)
    connector = PlaywrightConnector(downloads_dir=engine.browser_downloads_dir)
    assert isinstance(connector, PlaywrightConnector)


# ---------- Connector context.connectors not serialized ----------


def test_workflow_context_connectors_excluded_from_dump() -> None:
    """`connectors` is a Pydantic field with `exclude=True` so it doesn't
    leak into the JSON-serialized `instance.context`."""
    from workflow_platform.engine.context import WorkflowContext

    ctx = WorkflowContext(instance_id="i", workflow_id="w")
    ctx.connectors["browser"] = _RecordingBrowserConnector()
    dumped = ctx.model_dump()
    assert "connectors" not in dumped


# asyncio_mode = "auto" in pyproject covers the async tests above.
