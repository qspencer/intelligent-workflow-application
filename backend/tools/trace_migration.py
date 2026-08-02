"""Raw-trace backfill + zero-raw verifier CLI (docs/TRACE_GOVERNANCE_PLAN.md
§8.6/§8.14, TG3c).

    # verify only (the release gate — exit 1 if any raw remains)
    DATABASE_URL=postgresql+asyncpg://... uv run python tools/trace_migration.py verify

    # backfill existing inline raw into the vault + project the rows, then verify
    DATABASE_URL=postgresql+asyncpg://... uv run python tools/trace_migration.py backfill

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
from workflow_platform.trace_migration import backfill_all, verify_zero_raw


def _build_repos() -> tuple[Repositories, Any | None]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return in_memory_repositories(), None
    db_engine = make_engine(url)
    return postgres_repositories(make_session_factory(db_engine)), db_engine


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


async def _main(command: str) -> int:
    repos, db_engine = _build_repos()
    try:
        if command == "backfill":
            written = await backfill_all(repos)
            print(f"backfill: {written} vault object(s) written")
        return await _verify(repos)
    finally:
        if db_engine is not None:
            await db_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "backfill"), default="verify", nargs="?")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.command)))


if __name__ == "__main__":
    main()
