"""Replay-mode CLI — re-run a workflow against recorded Bedrock responses.

Usage:
    uv run python tools/replay.py \
        --definition path/to/workflow.json \
        --trigger '{"file_path": "/inbox/inv.pdf"}' \
        --recordings-dir tests/recordings

What it does:
    1. Builds a fresh in-memory engine + MockWorld + REPLAY-mode BedrockClient
       pointing at the given recordings dir.
    2. Loads the definition and runs it with the supplied trigger payload.
    3. Prints the resulting state, per-step outputs, and audit trail.

Useful for: debugging a real-world workflow run against a saved fixture, and
regression-testing changes to the engine or step functions without burning
Bedrock credits.

Limitations: requires the recording set to cover every Bedrock call the
workflow makes (any miss raises `RecordingNotFoundError`). Re-record by setting
`BEDROCK_MODE=record` and running the live workflow once.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Allow `python tools/replay.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.bedrock import BedrockClient, BedrockMode
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
    default_function_registry,
)
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.tools import (
    FileReadTool,
    FileWriteTool,
    PdfExtractTool,
)
from workflow_platform.workflow import load_definition_from_file
from workflow_platform.world import mock_world


def _default_tools() -> ToolCatalog:
    return ToolCatalog([PdfExtractTool(), FileReadTool(), FileWriteTool()])


def _default_functions() -> FunctionRegistry:
    return default_function_registry()


async def replay(args: argparse.Namespace) -> int:
    definition = load_definition_from_file(args.definition)
    trigger_payload: dict[str, Any] = json.loads(args.trigger) if args.trigger else {}

    bedrock = BedrockClient(mode=BedrockMode.REPLAY, recordings_dir=args.recordings_dir)
    repos = in_memory_repositories()
    await repos.definitions.save(definition)

    engine = WorkflowEngine(
        repositories=repos,
        functions=_default_functions(),
        tools=_default_tools(),
        bedrock=bedrock,
        world=mock_world(),
    )

    instance = await engine.run(definition, trigger_payload=trigger_payload)

    print(f"\nInstance: {instance.id}")
    print(f"State:    {instance.state.value}")
    if instance.error:
        print(f"Error:    {instance.error}")

    steps = await repos.steps.list_by_instance(instance.id)
    print("\nSteps:")
    for s in steps:
        line = f"  {s.step_id:<20} {s.state.value}"
        if s.error:
            line += f"  -- {s.error}"
        print(line)
        if s.output:
            for k, v in s.output.items():
                print(f"      {k}: {json.dumps(v, default=str)[:120]}")

    audit = await repos.audit.list_by_instance(instance.id)
    print(f"\nAudit ({len(audit)} entries):")
    for e in audit:
        target = f" ({e.step_id})" if e.step_id else ""
        print(f"  {e.timestamp.isoformat()}  {e.action}{target}")

    return 0 if instance.state.value == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a workflow against recorded fixtures.")
    parser.add_argument(
        "--definition",
        required=True,
        type=Path,
        help="Path to a workflow definition JSON file.",
    )
    parser.add_argument(
        "--trigger",
        default="{}",
        help='JSON string for the trigger payload (default "{}").',
    )
    parser.add_argument(
        "--recordings-dir",
        type=Path,
        default=Path("tests/recordings"),
        help="Directory containing Bedrock recordings.",
    )
    args = parser.parse_args()
    return asyncio.run(replay(args))


if __name__ == "__main__":
    raise SystemExit(main())
