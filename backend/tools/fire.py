"""Fire a workflow once against the configured persistence + Bedrock mode.

Companion to `tools/replay.py` for *real* runs that you want to inspect in
the dashboard. `replay.py` is in-memory + replay-mode only — designed for
deterministic regression testing. `fire.py` reads the environment:

- `DATABASE_URL` set       → Postgres-backed repos (run visible in dashboard).
- `DATABASE_URL` unset     → in-memory repos (one-shot, not visible).
- `BEDROCK_MODE` env var   → live / record / replay. `BedrockClient()` reads
                              this directly; default is live.
- `AWS_REGION` env var     → region for Bedrock. Default is `BedrockClient`'s.

Usage:
    cd backend
    DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \\
        uv run python tools/fire.py \\
        --definition ../examples/pdf_classifier/workflow.yaml \\
        --trigger '{"file_path": "/abs/path/to/some.pdf"}'

Exits non-zero on FAILED / KILLED; zero on COMPLETED or PAUSED.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.bedrock import BedrockClient
from workflow_platform.connectors.email import maybe_build_gmail_connector
from workflow_platform.engine import (
    ToolCatalog,
    WorkflowEngine,
    default_function_registry,
)
from workflow_platform.memory import LearnedMemoryService, MemoryManager
from workflow_platform.orchestrator import seed_memory_from_workflow_dir
from workflow_platform.persistence import (
    Repositories,
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories
from workflow_platform.secrets import EnvSecretStore
from workflow_platform.tools import (
    EmailLabelApplyTool,
    EmailSendTool,
    FileReadTool,
    FileWriteTool,
    PdfExtractTool,
    Tool,
)
from workflow_platform.workflow import load_definition_from_file
from workflow_platform.world import real_world

EXIT_OK = 0
EXIT_FAIL = 1


def _build_repos() -> tuple[Repositories, Any | None]:
    """Return (repos, db_engine_or_None). Caller disposes the engine."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return in_memory_repositories(), None
    db_engine = make_engine(url)
    session_factory = make_session_factory(db_engine)
    return postgres_repositories(session_factory), db_engine


def _build_tools() -> list[Tool]:
    """Always-on tools + optional Gmail tools when
    `WORKFLOW_PLATFORM_GMAIL_ACCOUNT` is set and credentials are reachable."""
    tools: list[Tool] = [PdfExtractTool(), FileReadTool(), FileWriteTool()]
    account = os.environ.get("WORKFLOW_PLATFORM_GMAIL_ACCOUNT")
    gmail_connector = maybe_build_gmail_connector(account=account, secret_store=EnvSecretStore())
    if gmail_connector is not None:
        tools.extend([EmailSendTool(gmail_connector), EmailLabelApplyTool(gmail_connector)])
    return tools


async def fire(args: argparse.Namespace) -> int:
    definition_path = Path(args.definition).resolve()
    definition = load_definition_from_file(definition_path)
    trigger_payload: dict[str, Any] = json.loads(args.trigger) if args.trigger else {}

    repos, db_engine = _build_repos()
    backend = "postgres" if db_engine is not None else "in-memory"
    memory_dir = os.environ.get("WORKFLOW_PLATFORM_MEMORY_DIR", ".memory")
    memory = MemoryManager(memory_dir)

    try:
        await repos.definitions.save(definition)
        await seed_memory_from_workflow_dir(definition, definition_path, memory)

        bedrock = BedrockClient()
        learned_db = os.environ.get(
            "WORKFLOW_PLATFORM_LEARNED_MEMORY_DB", str(Path(memory_dir) / "learned.db")
        )
        engine = WorkflowEngine(
            repositories=repos,
            functions=default_function_registry(),
            tools=ToolCatalog(_build_tools()),
            bedrock=bedrock,
            world=real_world(),
            memory=memory,
            learned_memory=LearnedMemoryService(bedrock, learned_db),
        )
        instance = await engine.run(definition, trigger_payload=trigger_payload)
    finally:
        if db_engine is not None and hasattr(db_engine, "dispose"):
            await db_engine.dispose()

    print(f"instance: {instance.id}")
    print(f"state:    {instance.state.value}")
    if instance.error:
        print(f"error:    {instance.error}")

    context = instance.context or {}
    if "total_tokens" in context:
        print(f"tokens:   {context['total_tokens']}")
    if "total_cost_usd" in context:
        print(f"cost:     ${context['total_cost_usd']:.6f}")

    print(f"backend:  {backend}")
    if backend == "postgres":
        # Assumes the standard dev frontend proxy at localhost:4200.
        print(f"view:     http://localhost:4200/instances/{instance.id}")

    if instance.state in (WorkflowInstanceState.FAILED, WorkflowInstanceState.KILLED):
        return EXIT_FAIL
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fire a workflow once against the configured persistence + Bedrock mode.",
    )
    parser.add_argument(
        "--definition",
        required=True,
        type=Path,
        help="Path to a workflow definition YAML or JSON file.",
    )
    parser.add_argument(
        "--trigger",
        default="{}",
        help='JSON string for the trigger payload (default "{}").',
    )
    args = parser.parse_args()
    return asyncio.run(fire(args))


if __name__ == "__main__":
    raise SystemExit(main())
