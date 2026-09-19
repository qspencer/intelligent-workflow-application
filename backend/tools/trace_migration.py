"""Raw-trace backfill + zero-raw verifier CLI (docs/TRACE_GOVERNANCE_PLAN.md
§8.6/§8.14, TG3c).

    # verify only (the release gate — exit 1 if any raw remains)
    DATABASE_URL=postgresql+asyncpg://... uv run python tools/trace_migration.py verify

    # backfill existing inline raw into the vault + project the rows, then verify
    DATABASE_URL=postgresql+asyncpg://... uv run python tools/trace_migration.py backfill

    # G-Trace-Audit-Rest: the pre-flip audit_log rows. REPORT ONLY by
    # default — it is the one path that rewrites existing audit rows, so
    # it takes an explicit --apply.
    DATABASE_URL=... uv run python tools/trace_migration.py migrate-audit
    DATABASE_URL=... uv run python tools/trace_migration.py migrate-audit --apply

Uses Postgres repos when `DATABASE_URL` is set, else in-memory (a no-op there).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from workflow_platform.persistence import Repositories, in_memory_repositories
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories
from workflow_platform.persistence.schema_version import assert_schema_current
from workflow_platform.trace_migration import (
    backfill_all,
    migrate_audit_details,
    verify_zero_raw,
)


def _build_repos() -> tuple[Repositories, Any | None, Any | None]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return in_memory_repositories(), None, None
    db_engine = make_engine(url)
    session_factory = make_session_factory(db_engine)
    return postgres_repositories(session_factory), db_engine, session_factory


async def _verify(repos: Repositories) -> int:
    report = await verify_zero_raw(repos)
    if report.clean:
        print(f"zero-raw: OK — no raw found (scanned {report.scanned} instances, exhaustive)")
        return 0
    if report.capped:
        # A truncated scan can't certify what it didn't read (F10).
        print(f"zero-raw: FAIL — scan CAPPED at {report.scanned}; store exceeds the scan ceiling")
    if report.audit_findings:
        print(
            f"zero-raw: {len(report.audit_findings)} of the findings are append-only pre-flip "
            "audit_log raw — backfill does not rewrite it; encrypt/migrate before certifying"
        )
    if report.findings:
        print(f"zero-raw: FAIL — {len(report.findings)} raw finding(s):")
        for f in report.findings[:50]:
            print(f"  {f.table}.{f.column}  row={f.row_id}")
        if len(report.findings) > 50:
            print(f"  … and {len(report.findings) - 50} more")
    return 1


async def _migrate_audit(repos: Repositories, *, apply: bool) -> int:
    report = await migrate_audit_details(repos, dry_run=not apply)
    head = "WOULD MIGRATE" if report.dry_run else "MIGRATED"
    print(
        f"audit-rest: {head} {report.candidates} row(s) in ~{report.batches} batch(es)"
        + ("" if report.dry_run else f" — vaulted {report.vaulted}, projected {report.projected}")
    )
    if report.skipped_no_instance:
        print(
            f"audit-rest: SKIPPED {report.skipped_no_instance} instance-less row(s) — the vault "
            "is instance-scoped, so projecting them would destroy raw with nowhere to put it"
        )
    if report.skipped_no_org:
        print(f"audit-rest: SKIPPED {report.skipped_no_org} row(s) whose instance has no org")
    if report.dry_run:
        print("audit-rest: report only. Re-run with --apply to rewrite these rows.")
    return 0


async def _main(command: str, *, apply: bool) -> int:
    repos, db_engine, session_factory = _build_repos()
    await assert_schema_current(session_factory)  # G26.1 pre-flight
    try:
        if command == "backfill":
            written = await backfill_all(repos)
            print(f"backfill: {written} vault object(s) written")
        if command == "migrate-audit":
            rc = await _migrate_audit(repos, apply=apply)
            if rc or not apply:
                return rc
        return await _verify(repos)
    finally:
        if db_engine is not None:
            await db_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("verify", "backfill", "migrate-audit"), default="verify", nargs="?"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="migrate-audit only: actually rewrite the rows. Without it, report only.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.command, apply=args.apply)))


if __name__ == "__main__":
    main()
