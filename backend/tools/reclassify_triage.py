#!/usr/bin/env python3
"""Re-classify every triaged email under the CURRENT rubric via fork-from-step.

For each distinct message's LATEST completed run, POSTs the fork endpoint on
the running backend — so each re-run picks up the freshly seeded rubric AND
the sender's recalled learned-memory history, exactly like an organic run
(observations are written back too). Built for taxonomy/rubric migrations
(first use: the 2026-07-19 fyi → notification/newsletter/promotion split).

Idempotent: messages whose latest run already carries the current rubric's
`memory_hash` are skipped, so re-running the tool only processes what's left.

Requires the backend to be RUNNING (and restarted since the rubric change —
the rubric seeds at boot). Costs real Bedrock spend (~$0.005-0.01/message:
one triage call + two memory observations).

Usage:
    DATABASE_URL=postgresql+asyncpg://... uv run python tools/reclassify_triage.py \\
        [--workflow email-triage-live] [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.persistence import Repositories, WorkflowInstanceState
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "http://127.0.0.1:8001"
_DEV_HEADERS = {"X-Dev-User": "reclassify-tool", "X-Dev-Groups": "admins"}


def _current_rubric_hash(workflow_id: str, step_id: str) -> str | None:
    """Hash of the rubric the running engine would inject — same computation
    as the engine's memory_hash (sha256 of the seeded memory file, 16 hex)."""
    memory_dir = Path(os.environ.get("WORKFLOW_PLATFORM_MEMORY_DIR", BACKEND_DIR / ".memory"))
    path = memory_dir / "steps" / workflow_id / f"{step_id}.md"
    if not path.is_file():
        return None
    return "sha256:" + hashlib.sha256(path.read_text().encode()).hexdigest()[:16]


async def run(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set — run history comes from Postgres.")
        return 2

    current_hash = _current_rubric_hash(args.workflow, args.step)
    print(f"workflow      : {args.workflow}")
    print(f"current rubric: {current_hash or '(no seeded memory file found)'}")

    db_engine = make_engine(url)
    repos: Repositories = postgres_repositories(make_session_factory(db_engine))
    moved: Counter[tuple[str, str]] = Counter()
    forked = skipped = failed = 0
    try:
        instances = await repos.instances.list_by_workflow(args.workflow)
        instances = [i for i in instances if i.state == WorkflowInstanceState.COMPLETED]
        instances.sort(key=lambda i: i.started_at)
        latest: dict[str, object] = {}
        for inst in instances:
            mid = str(inst.trigger_payload.get("message_id") or inst.id)
            latest[mid] = inst  # later wins (sorted ascending)
        targets = list(latest.values())
        if args.limit:
            targets = targets[: args.limit]
        print(f"messages      : {len(targets)} (latest run per message)\n")

        async with httpx.AsyncClient(base_url=args.base_url, timeout=180.0) as client:
            for n, inst in enumerate(targets, 1):
                steps = await repos.steps.list_by_instance(inst.id)  # type: ignore[attr-defined]
                triage = next((s for s in steps if s.step_id == args.step), None)
                record = next((s for s in steps if s.step_id == "record"), None)
                old_cat = (record.output or {}).get("category") if record else None
                old_hash = (triage.output or {}).get("memory_hash") if triage else None
                subject = str(inst.trigger_payload.get("subject", ""))[:50]  # type: ignore[attr-defined]

                if current_hash is not None and old_hash == current_hash:
                    skipped += 1
                    continue
                if args.dry_run:
                    forked += 1
                    print(f"[{n}] would fork: ({old_cat}) {subject!r}")
                    continue

                resp = await client.post(
                    f"/api/workflow-instances/{inst.id}/fork",  # type: ignore[attr-defined]
                    json={"from_step_id": args.step},
                    headers=_DEV_HEADERS,
                )
                if resp.status_code != 200:
                    failed += 1
                    print(f"[{n}] FORK FAILED {resp.status_code}: {subject!r} — {resp.text[:120]}")
                    continue
                new_id = resp.json()["instance_id"]
                new_steps = await repos.steps.list_by_instance(new_id)
                new_record = next((s for s in new_steps if s.step_id == "record"), None)
                new_cat = (new_record.output or {}).get("category") if new_record else None
                forked += 1
                moved[(str(old_cat), str(new_cat))] += 1
                marker = "  " if old_cat == new_cat else "->"
                print(f"[{n}] {old_cat!s:>14} {marker} {new_cat!s:<14} {subject!r}")
    finally:
        if hasattr(db_engine, "dispose"):
            await db_engine.dispose()

    print(f"\nDONE: {forked} re-classified, {skipped} already current, {failed} failed.")
    if moved:
        print("\nmovement (old -> new):")
        for (old, new), count in moved.most_common():
            print(f"  {old:>14} -> {new:<14} {count}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", default="email-triage-live")
    parser.add_argument("--step", default="triage")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="list targets without forking")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
