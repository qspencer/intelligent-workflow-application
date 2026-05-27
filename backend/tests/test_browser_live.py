"""Live browser test, opt-in via `BROWSER_LIVE=1`.

Launches a real headless Chromium via `PlaywrightConnector` and drives
it against the public RPA Challenge OCR site
(https://rpachallengeocr.azurewebsites.net/). The site is free, has no
auth, and explicitly invites automation — passing run produces a
"challenge completed" result page.

Two layers, gated together:

1. **Connector smoke** — navigate / click / wait / read_table against
   the real site. Answers: "is the browser stack actually reachable,
   does the page still match our assumptions?" Cheap, free, runs in
   ~10 seconds.

2. **End-to-end workflow** — drives `examples/rpa_challenge_ocr/`
   through real Bedrock + real browser + real tesseract. Adds the
   `BEDROCK_LIVE=1` gate on top (so the cost of the Haiku calls is
   opt-in independently). Skipped if `BEDROCK_LIVE` is not set even
   when `BROWSER_LIVE` is.

CI runs this weekly via `.github/workflows/live-tests.yml` once D7b
lands. Local invocation:

    BROWSER_LIVE=1 uv run pytest tests/test_browser_live.py -q
    BROWSER_LIVE=1 BEDROCK_LIVE=1 uv run pytest -m browser_live -q
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from workflow_platform.connectors.browser import PlaywrightConnector

RPA_URL = "https://rpachallengeocr.azurewebsites.net/"

pytestmark = [
    pytest.mark.browser_live,
    pytest.mark.skipif(
        os.environ.get("BROWSER_LIVE") != "1",
        reason="BROWSER_LIVE not set; skipping live browser tests",
    ),
]


@pytest.fixture()
async def live_browser(tmp_path: Path) -> AsyncIterator[PlaywrightConnector]:
    """Real PlaywrightConnector launched against headless Chromium.

    Each test gets its own connector (and therefore its own browser /
    context / page), matching how the engine builds one per workflow
    run. Teardown closes the browser even on test failure.
    """
    connector = PlaywrightConnector(downloads_dir=tmp_path / "downloads")
    await connector.__aenter__()
    try:
        yield connector
    finally:
        await connector.__aexit__(None, None, None)


async def test_navigate_to_rpa_challenge_loads_page(
    live_browser: PlaywrightConnector,
) -> None:
    """Sanity: the page is reachable + Playwright lifecycle works."""
    await live_browser.navigate(RPA_URL, wait_until="domcontentloaded")
    assert await live_browser.health_check() is True


async def test_start_button_renders_table(live_browser: PlaywrightConnector) -> None:
    """Click Start, then wait for the JS-rendered table to populate.

    The site's `<tbody>` is empty on initial load; rows only appear
    after the async fetch fires off `#buttonStart`. If this assertion
    fails, our `read_table` step assumption is broken — either the
    challenge page changed or the network is flaky.
    """
    await live_browser.navigate(RPA_URL, wait_until="domcontentloaded")
    await live_browser.click("#start")
    # The first row appearing is the gating condition for read_table.
    await live_browser.wait_for("#tableSandbox tr", state="visible", timeout_ms=15000)
    rows = await live_browser.read_table("#tableSandbox")
    assert len(rows) >= 1, f"Expected ≥1 invoice row after Start; got {rows!r}"
    # Sanity on column shape — the challenge has 3 columns (ID, due
    # date, link). read_table keys come from the first row's <th>
    # cells; if the page reformats, this catches it.
    first = rows[0]
    assert len(first) >= 1, f"Expected ≥1 column in {first!r}"


async def test_screenshot_writes_to_downloads_dir(
    live_browser: PlaywrightConnector,
) -> None:
    """Verify the downloads-dir contract: screenshot lands on disk."""
    await live_browser.navigate(RPA_URL, wait_until="domcontentloaded")
    shot = await live_browser.screenshot(full_page=False)
    assert Path(shot.local_path).is_file()
    assert shot.bytes > 0


# ---------- End-to-end workflow (needs BEDROCK_LIVE too) ----------


@pytest.mark.skipif(
    os.environ.get("BEDROCK_LIVE") != "1",
    reason="BEDROCK_LIVE not set; skipping end-to-end live workflow test",
)
async def test_rpa_challenge_workflow_end_to_end_live(tmp_path: Path) -> None:
    """Full workflow against the real site + real Bedrock + real tesseract.

    Loads the committed `examples/rpa_challenge_ocr/workflow.yaml`, runs
    it once, asserts the workflow reaches COMPLETED and the output CSV
    contains at least one row. Does NOT assert on specific invoice
    values — the test data on the live site changes every load.

    This is the real validation gate: if the rubric in
    `examples/rpa_challenge_ocr/agent_memory.md` regresses against the
    actual OCR output, this test catches it.
    """
    from workflow_platform.bedrock import BedrockClient, BedrockMode
    from workflow_platform.engine import ToolCatalog, WorkflowEngine
    from workflow_platform.engine.functions import default_function_registry
    from workflow_platform.memory import MemoryManager
    from workflow_platform.persistence import (
        WorkflowInstanceState,
        in_memory_repositories,
    )
    from workflow_platform.tools import (
        BrowserClickTool,
        BrowserDownloadTool,
        BrowserFillTool,
        BrowserNavigateTool,
        BrowserReadTableTool,
        BrowserReadTextTool,
        BrowserScreenshotTool,
        BrowserUploadFileTool,
        BrowserWaitForTool,
        ImageOcrTool,
    )
    from workflow_platform.world import real_world

    example_dir = Path(__file__).parent.parent.parent / "examples" / "rpa_challenge_ocr"

    # Seed memory from the workflow's agent_memory.md (G6 auto-load
    # pattern — same as fire.py and the trigger orchestrator).
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    memory = MemoryManager(memory_dir)
    memory_text = (example_dir / "agent_memory.md").read_text()
    # Every agentic step seeds the same memory under its own agent_id.
    from workflow_platform.workflow import load_definition_from_yaml

    definition = load_definition_from_yaml((example_dir / "workflow.yaml").read_text())
    for step in definition.steps:
        if step.type == "agentic":
            await memory.write_raw(f"steps/{definition.id}/{step.id}", memory_text)

    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog(
            [
                BrowserNavigateTool(),
                BrowserClickTool(),
                BrowserFillTool(),
                BrowserWaitForTool(),
                BrowserReadTextTool(),
                BrowserReadTableTool(),
                BrowserDownloadTool(),
                BrowserUploadFileTool(),
                BrowserScreenshotTool(),
                ImageOcrTool(),
            ]
        ),
        bedrock=BedrockClient(mode=BedrockMode.LIVE, region="us-east-1"),
        world=real_world(),
        memory=memory,
        browser_downloads_dir=tmp_path / "downloads",
    )

    instance = await engine.run(definition)

    assert instance.state == WorkflowInstanceState.COMPLETED, instance.error

    # The CSV step must have produced a non-empty file.
    csv_path = Path("/tmp/rpa-challenge-output.csv")
    assert csv_path.is_file(), f"Expected CSV at {csv_path}"
    body = csv_path.read_text().strip().splitlines()
    assert len(body) >= 2, f"Expected header + ≥1 data row in CSV; got {body!r}"
    assert body[0] == "id,due_date,invoice_number,invoice_date,company_name,total_due"
