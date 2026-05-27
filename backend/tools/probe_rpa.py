"""One-shot diagnostic for the RPA Challenge OCR workflow.

Runs the workflow against real Bedrock + real Chromium + real tesseract,
then dumps each step's output and the audit log so we can see *exactly*
where things went wrong without re-reading 5+ minutes of pytest output.

Usage:
    cd backend && BROWSER_LIVE=1 BEDROCK_LIVE=1 uv run python tools/probe_rpa.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
EXAMPLE_DIR = REPO_ROOT / "examples" / "rpa_challenge_ocr"
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))


async def main() -> int:
    if os.environ.get("BROWSER_LIVE") != "1":
        print("Set BROWSER_LIVE=1 to run.")
        return 1

    from workflow_platform.bedrock import BedrockClient, BedrockMode
    from workflow_platform.engine import ToolCatalog, WorkflowEngine
    from workflow_platform.engine.functions import default_function_registry
    from workflow_platform.memory import MemoryManager
    from workflow_platform.persistence import in_memory_repositories
    from workflow_platform.tools import (
        BrowserClickTool,
        BrowserDownloadTool,
        BrowserFetchUrlTool,
        BrowserFillTool,
        BrowserNavigateTool,
        BrowserReadTableTool,
        BrowserReadTextTool,
        BrowserScreenshotTool,
        BrowserSubmitFormTool,
        BrowserUploadFileTool,
        BrowserWaitForTool,
        ImageOcrTool,
    )
    from workflow_platform.workflow import load_definition_from_yaml
    from workflow_platform.world import real_world

    workdir = Path("/tmp/rpa-probe")
    workdir.mkdir(exist_ok=True)

    memory_dir = workdir / "memory"
    memory_dir.mkdir(exist_ok=True)
    memory = MemoryManager(memory_dir)
    memory_text = (EXAMPLE_DIR / "agent_memory.md").read_text()
    definition = load_definition_from_yaml((EXAMPLE_DIR / "workflow.yaml").read_text())
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
                BrowserFetchUrlTool(),
                BrowserUploadFileTool(),
                BrowserSubmitFormTool(),
                BrowserScreenshotTool(),
                ImageOcrTool(),
            ]
        ),
        bedrock=BedrockClient(mode=BedrockMode.LIVE, region="us-east-1"),
        world=real_world(),
        memory=memory,
        browser_downloads_dir=workdir / "downloads",
    )

    print(f"=== Running workflow {definition.id!r} ===\n")
    instance = await engine.run(definition)

    print(f"\n=== Terminal state: {instance.state.value} ===")
    if instance.error:
        print(f"Error: {instance.error}")
    print(f"Total tokens: {instance.context.get('total_tokens')}")
    print(f"Total cost USD: {instance.context.get('total_cost_usd')}\n")

    print("=== Per-step outputs ===")
    steps = await repos.steps.list_by_instance(instance.id)
    for s in steps:
        print(f"\n--- {s.step_id} ({s.state.value}) ---")
        if s.error:
            print(f"ERROR: {s.error}")
        if s.output:
            out = dict(s.output)
            for key in ("conversation",):
                if key in out:
                    out[key] = f"<elided ({len(out[key])} entries)>"
            tool_calls = out.pop("tool_calls", None)
            print(json.dumps(out, indent=2, default=str)[:3000])
            if tool_calls:
                print(f"\ntool_calls ({len(tool_calls)}):")
                for tc in tool_calls:
                    name = tc.get("name", "?")
                    inp = tc.get("input", {})
                    result = tc.get("result", {})
                    err = result.get("error") if isinstance(result, dict) else None
                    print(f"  - {name}({json.dumps(inp, default=str)[:200]})")
                    if err:
                        print(f"      ERROR: {err}")
                    elif isinstance(result, dict):
                        content = result.get("content")
                        if content is not None:
                            preview = json.dumps(content, default=str)[:300]
                            print(f"      ok: {preview}")

    print("\n=== Audit log ===")
    audit = await repos.audit.list_by_instance(instance.id)
    for entry in audit:
        detail = entry.detail or {}
        d_preview = json.dumps(detail, default=str)[:200] if detail else ""
        print(f"  [{entry.action}] step={entry.step_id} {d_preview}")

    return 0 if instance.state.value == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
