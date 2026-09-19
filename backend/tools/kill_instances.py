"""Bulk-kill non-terminal workflow instances, with an audit trail.

Written for the 2026-09-19 orphan recovery: the boot sweep turned 171
instances stranded RUNNING into PAUSED, and PAUSED means "resumable", which
for `email-triage-apply` means re-running a step that applies labels to a
live mailbox. Months-old mail should not be re-triaged, so the right end
state for those runs is KILLED.

WHY A TOOL RATHER THAN A LOOP OVER THE API. `POST /workflow-instances/{id}/kill`
writes the state change and **no audit entry** — the only `workflow_killed`
entry the system produces comes from the engine's own `_KillRequested` path.
Flipping 171 rows to a terminal state with nothing in the log recording who
did it, when, or why is indistinguishable from tampering later. This writes
the same `workflow_killed` entry the engine writes, through the shared
`AuditWriter`, so the bulk action is as legible as a single one.

SAFE BY DEFAULT: selects nothing without `--workflow-id`, previews without
`--yes`, refuses terminal states, and never touches a RUNNING instance
unless asked (`--state running`) — a RUNNING row may be a live run.

Usage:
    cd backend
    DATABASE_URL=... uv run python tools/kill_instances.py \\
        --workflow-id email-triage-apply --state paused \\
        --error-prefix "interrupted:" --reason "orphan recovery" --yes
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from workflow_platform.audit_writer import AuditWriter
from workflow_platform.persistence import WorkflowInstanceState
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories
from workflow_platform.trace_flip import trace_safe_only_from_env

TERMINAL = {
    WorkflowInstanceState.COMPLETED,
    WorkflowInstanceState.FAILED,
    WorkflowInstanceState.KILLED,
}


async def run(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set.")
        return 2
    try:
        state = WorkflowInstanceState(args.state)
    except ValueError:
        print(f"Unknown state {args.state!r}.")
        return 2
    if state in TERMINAL:
        print(f"{state.value} is already terminal; nothing to kill.")
        return 2

    db_engine = make_engine(url)
    repos = postgres_repositories(make_session_factory(db_engine))
    try:
        candidates = [
            i
            for i in await repos.instances.list_by_state([state.value], limit=args.limit)
            if i.workflow_id == args.workflow_id
            and (not args.error_prefix or (i.error or "").startswith(args.error_prefix))
        ]
        if not candidates:
            print("No instances matched.")
            return 0

        oldest = min(i.created_at for i in candidates)
        newest = max(i.created_at for i in candidates)
        print(
            f"{len(candidates)} instance(s) of {args.workflow_id!r} in state "
            f"{state.value}, created {oldest:%Y-%m-%d} .. {newest:%Y-%m-%d}"
        )
        if not args.yes:
            print("Preview only. Re-run with --yes to kill them.")
            return 0

        writer = AuditWriter(repos, trace_safe_only=trace_safe_only_from_env())
        killed = 0
        for instance in candidates:
            instance.state = WorkflowInstanceState.KILLED
            await repos.instances.update(instance)
            # The engine's own action name and shape — no detail, so nothing
            # is lost to projection and nothing needs vaulting. The REASON
            # lives in `actor_id`, which is a column, not detail.
            await writer.append(
                "workflow_killed",
                actor_type="operator",
                actor_id=f"kill_instances_cli:{args.reason}",
                instance_id=instance.id,
            )
            killed += 1
        print(f"Killed {killed} instance(s).")
        return 0
    finally:
        await db_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--state", default="paused", help="non-terminal state to select")
    parser.add_argument(
        "--error-prefix",
        default="",
        help="only instances whose error starts with this (e.g. 'interrupted:')",
    )
    parser.add_argument("--reason", default="bulk kill", help="recorded in the audit actor_id")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--yes", action="store_true", help="actually kill; otherwise preview")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
