"""Batch driver for invoice_extraction — fires every PDF in a directory
through `workflow-batch.yaml` using a single shared engine instance.

Distinct from `run_batch.sh`, which forks `tools/fire.py` per PDF (each
fork pays ~3-4s of Python+SDK startup). This script builds the engine
once and reuses it across all invoices, which cuts wall-clock from ~120
minutes to ~15-25 minutes for 1000+ PDFs depending on concurrency.

Concurrency: defaults to 4. Each engine.run() is independent; the
engine itself has no per-call mutable state. Bedrock 200 req/min default
quota is plenty of headroom. Postgres connection pool (5 + 10 overflow)
handles this trivially.

The batch workflow drops the three notify_* email steps that the
operational `workflow.yaml` uses. At 1007 invoices × 3 emails = 3021
emails, the existing workflow would slam into Workspace's daily send
quota mid-run. For batches this size, the Postgres dataset *is* the
analytical surface — emails would just be inbox noise.

Usage (from repo root):
    DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \\
      uv run --project backend python examples/invoice_extraction/scripts/run_full_batch.py \\
        --pdf-dir /home/ubuntu/Documents/intelligent-workflow-engine/sample-invoices/1000-pdf-invoice-samples \\
        [--concurrency 4] [--max-invoices N] [--progress-every 25]

Exits non-zero if any per-invoice run failed. Final summary prints
totals + rate + ETA. Per-invoice failures are logged with `state` +
error message and the batch continues.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

# Add backend/src so `workflow_platform.*` imports work when invoked from
# anywhere (e.g. cwd is the repo root, not backend/).
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from workflow_platform.bedrock import BedrockClient  # noqa: E402
from workflow_platform.engine import (  # noqa: E402
    ToolCatalog,
    WorkflowEngine,
    default_function_registry,
)
from workflow_platform.memory import MemoryManager  # noqa: E402
from workflow_platform.orchestrator import seed_memory_from_workflow_dir  # noqa: E402
from workflow_platform.persistence.db import make_engine, make_session_factory  # noqa: E402
from workflow_platform.persistence.postgres import postgres_repositories  # noqa: E402
from workflow_platform.tools import FileReadTool, FileWriteTool, PdfExtractTool  # noqa: E402
from workflow_platform.workflow import load_definition_from_file  # noqa: E402
from workflow_platform.world import real_world  # noqa: E402

WORKFLOW_PATH = REPO_ROOT / "examples" / "invoice_extraction" / "workflow-batch.yaml"


async def fire_one(
    engine: WorkflowEngine, definition: Any, pdf_path: Path
) -> tuple[str, str, str | None]:
    """Run one invoice. Returns (name, state, error_or_none). Never raises —
    per-invoice failures land as state='exception' with the message."""
    try:
        instance = await engine.run(definition, trigger_payload={"file_path": str(pdf_path)})
        return (pdf_path.name, instance.state.value, instance.error)
    except Exception as exc:
        return (pdf_path.name, "exception", f"{type(exc).__name__}: {exc}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", required=True, type=Path)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-invoices", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    pdf_files = sorted(args.pdf_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDFs found in {args.pdf_dir}", file=sys.stderr)
        return 2
    if args.max_invoices:
        pdf_files = pdf_files[: args.max_invoices]

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set; refusing to run batch without Postgres.", file=sys.stderr)
        return 2

    print(f"Found {len(pdf_files)} PDFs in {args.pdf_dir}")
    print(f"Concurrency: {args.concurrency}")
    print(f"Workflow:    {WORKFLOW_PATH.name}")
    print(f"Persistence: {db_url}")
    print()

    # Build repos + engine once.
    db_engine = make_engine(db_url)
    session_factory = make_session_factory(db_engine)
    repos = postgres_repositories(session_factory)

    memory_dir = os.environ.get("WORKFLOW_PLATFORM_MEMORY_DIR", ".memory")
    memory = MemoryManager(memory_dir)
    definition = load_definition_from_file(WORKFLOW_PATH)
    await repos.definitions.save(definition)
    await seed_memory_from_workflow_dir(definition, WORKFLOW_PATH, memory)

    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog([PdfExtractTool(), FileReadTool(), FileWriteTool()]),
        bedrock=BedrockClient(),
        world=real_world(),
        memory=memory,
    )

    sem = asyncio.Semaphore(args.concurrency)

    async def _fire(pdf: Path) -> tuple[str, str, str | None]:
        async with sem:
            return await fire_one(engine, definition, pdf)

    tasks = [_fire(pdf) for pdf in pdf_files]

    start = time.perf_counter()
    completed = 0
    failed = 0

    try:
        for idx, coro in enumerate(asyncio.as_completed(tasks), 1):
            name, state, error = await coro
            if state == "completed":
                completed += 1
            else:
                failed += 1
                print(
                    f"  [{idx}/{len(pdf_files)}] FAIL: {name} "
                    f"state={state} {error or ''}",
                    file=sys.stderr,
                )
            if idx % args.progress_every == 0 or idx == len(pdf_files):
                elapsed = time.perf_counter() - start
                rate = idx / elapsed if elapsed > 0 else 0
                eta = (len(pdf_files) - idx) / rate if rate > 0 else 0
                print(
                    f"  [{idx}/{len(pdf_files)}] "
                    f"completed={completed} failed={failed} "
                    f"rate={rate:.2f}/s "
                    f"eta={eta:.0f}s"
                )
    finally:
        if hasattr(db_engine, "dispose"):
            await db_engine.dispose()

    elapsed = time.perf_counter() - start
    print()
    print("=" * 64)
    print(f"Batch complete in {elapsed:.0f}s ({elapsed / 60:.1f} min)")
    print(f"  Total:     {len(pdf_files)}")
    print(f"  Completed: {completed}")
    print(f"  Failed:    {failed}")
    if elapsed > 0:
        print(f"  Rate:      {len(pdf_files) / elapsed:.2f} invoices/s")
    print("=" * 64)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
