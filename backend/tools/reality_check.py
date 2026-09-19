"""Falsify the normative docs against the live tables.

WHY THIS EXISTS. On 2026-09-19 two real defects were found by querying the
production database: `MonitoringService` writing unprojected mailbox
addresses at rest, and 171 workflow instances stranded RUNNING for two
months. Neither was found by the 18-round external review of the trace
primitive, by 1,286 tests, or by reading the code.
`EXECUTION_SEMANTICS.md` §7 claimed automatic boot re-drive that no code
implemented, and the table had disagreed with the document for months.

A normative document is a claim about the running system. A claim nobody
queries is a belief. Each check below is the QUERY THAT WOULD FALSIFY one
stated claim; the claim passes only when the query returns nothing.

This is a diagnostic, not a test: it reads a live database, reports, and
changes nothing. Read-only by construction — every statement is a SELECT.

    cd backend
    DATABASE_URL=... uv run python tools/reality_check.py
    DATABASE_URL=... uv run python tools/reality_check.py --since 2026-09-18

Exits 1 if any claim is falsified, so it can gate a release.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from workflow_platform.persistence.db import make_engine
from workflow_platform.trace_projection import PROJECTOR_VERSION, project_audit_detail_at_rest

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

#: Claims whose query needs the `--since` bound, because the behaviour they
#: describe was WRONG in the past and is right now. Bounding them is not
#: hiding the history — it is asking the question the claim actually makes
#: ("the engine does this"), and leaving the historical rows to the backfill
#: item that owns them. Run with `--since 1970-01-01` for the full sweep.
#: (source, claim, falsifying SQL with a :since bind).
CHECKS: list[tuple[str, str, str]] = [
    (
        "ES §1",
        "instance states stay within the documented enum",
        "select distinct state from workflow_instances where state not in "
        "('pending','running','completed','failed','killed','paused')",
    ),
    (
        "ES §1",
        "step states stay within the documented enum",
        "select distinct state from step_executions where state not in "
        "('pending','running','completed','failed','cancelled','skipped')",
    ),
    (
        "ES §1",
        "every terminal instance has its matching terminal audit entry",
        """select i.state, count(*) from workflow_instances i
           where i.state in ('completed','failed','killed')
             and coalesce(i.completed_at, i.started_at, i.created_at) > :since
             and not exists (
               select 1 from audit_log a where a.workflow_instance_id = i.id
                 and a.action = case i.state
                   when 'completed' then 'workflow_completed'
                   when 'failed' then 'workflow_failed'
                   else 'workflow_killed' end)
           group by 1""",
    ),
    (
        "ES §3a",
        "(instance_id, step_id, attempt) is unique",
        "select instance_id, step_id, attempt, count(*) from step_executions "
        "group by 1,2,3 having count(*) > 1 limit 20",
    ),
    (
        "ES §3a",
        "attempt numbers are 1-based",
        "select id, attempt from step_executions where attempt < 1 limit 20",
    ),
    (
        "ES §3a",
        "attempts per (instance, step) are contiguous from 1",
        "select instance_id, step_id, max(attempt), count(*) from step_executions "
        "group by 1,2 having max(attempt) <> count(*) limit 20",
    ),
    (
        "ES §3a",
        "a CANCELLED attempt holds no output",
        "select id, step_id from step_executions where state='cancelled' "
        "and output is not null and output::text not in ('null','{}') limit 20",
    ),
    (
        "ES §3a",
        "a step is not re-run once it has a COMPLETED attempt",
        "select instance_id, step_id, count(*) from step_executions where state='completed' "
        "group by 1,2 having count(*) > 1 limit 20",
    ),
    (
        "ES §3a",
        "step_* audit entries carry `attempt` in their detail",
        """select action, count(*) from audit_log
           where action in ('step_started','step_completed','step_failed','step_retry')
             and not (detail ? 'attempt') group by 1""",
    ),
    (
        "ES §7",
        "no instance is stranded RUNNING (nothing runs for hours at this scale)",
        "select id, workflow_id, started_at from workflow_instances "
        "where state='running' and started_at < now() - interval '2 hours' limit 20",
    ),
    (
        "ES §1",
        "no instance sits PAUSED past the abandoned-pause threshold unalerted",
        """select i.id, i.workflow_id, i.started_at from workflow_instances i
           where i.state = 'paused'
             and coalesce(i.started_at, i.created_at) < now() - interval '3 hours'
             and coalesce(i.started_at, i.created_at) > :since
             and not exists (
               select 1 from audit_log a where a.workflow_instance_id = i.id
                 and a.action = 'alert_abandoned_pause')
           limit 20""",
    ),
    (
        "SUBJ §5",
        "no audit row carries an email in `actor_id`",
        """select distinct actor_id from audit_log
           where actor_id ~ '^[^@[:space:]]+@[^@[:space:]]+\\.[A-Za-z]{2,}$'
           limit 20""",
    ),
    (
        "TM §5",
        "every definition carries an org",
        "select id from workflow_definitions where org_id is null or org_id='' limit 20",
    ),
    (
        "TM §5",
        "every instance carries an org",
        "select id from workflow_instances where org_id is null or org_id='' limit 20",
    ),
    (
        "TM §5",
        "an instance's org matches its definition's (inherited at birth)",
        """select i.id, i.org_id, d.org_id from workflow_instances i
           join workflow_definitions d on d.id = i.workflow_id
           where i.org_id is distinct from d.org_id limit 20""",
    ),
    (
        "TM §5",
        "every vault row carries an org",
        "select id from raw_traces where org_id is null or org_id='' limit 20",
    ),
    (
        "TM §6",
        "no session column stores a raw token (hashes only)",
        """select column_name from information_schema.columns
           where table_name='auth_sessions' and column_name not like '%hash%'
             and column_name in ('token','session_token','secret')""",
    ),
    (
        "ES §3a",
        "every vaulted audit detail is bound to an audit entry",
        "select id from raw_traces where kind='audit_detail' and audit_entry_id is null "
        "and created_at > :since limit 20",
    ),
    (
        "ES §3a",
        "every vault row's instance still exists",
        "select r.id from raw_traces r left join workflow_instances i on i.id = r.instance_id "
        "where i.id is null limit 20",
    ),
]


async def _projection_checks(conn: Any, since: datetime) -> list[tuple[str, str, str]]:
    """The two claims SQL cannot express, because they need the projector.

    Both are scoped to rows stamped with the CURRENT projector version. A
    row written by an older version is expected to differ under a newer
    one — that is what the version stamp is for, and re-projecting it under
    today's rules and calling the difference a leak is the mistake that
    made round-6 records read as tampering (see `PROJECTOR_VERSION`).
    """
    rows = (
        await conn.execute(
            text(
                "select action, detail from audit_log "
                "where timestamp > :since and projector_version = :v"
            ),
            {"since": since, "v": PROJECTOR_VERSION},
        )
    ).fetchall()
    findings: list[tuple[str, str, str]] = []

    leaked: dict[str, int] = {}
    drifted: dict[str, int] = {}
    for action, detail in rows:
        if not isinstance(detail, dict):
            continue
        if EMAIL_RE.search(json.dumps(detail)):
            leaked[action] = leaked.get(action, 0) + 1
        if project_audit_detail_at_rest(action, detail) != detail:
            drifted[action] = drifted.get(action, 0) + 1

    findings.append(
        (
            "TM §7",
            f"no audit detail written at projector v{PROJECTOR_VERSION} holds an email address",
            "; ".join(f"{a}={n}" for a, n in leaked.items()),
        )
    )
    findings.append(
        (
            "TM §7",
            f"every audit detail at projector v{PROJECTOR_VERSION} is a fixed point "
            "of the projection",
            "; ".join(f"{a}={n}" for a, n in drifted.items()),
        )
    )
    if not rows:
        findings.append(
            (
                "meta",
                f"rows exist at the current projector version (v{PROJECTOR_VERSION})",
                "none — the two checks above proved nothing",
            )
        )
    return findings


async def run(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set.")
        return 2
    since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
    engine = make_engine(url)
    failed = 0
    try:
        async with engine.connect() as conn:
            results: list[tuple[str, str, str]] = []
            for source, claim, sql in CHECKS:
                try:
                    params = {"since": since} if ":since" in sql else {}
                    rows = (await conn.execute(text(sql), params)).fetchall()
                except Exception as exc:  # a broken query is a finding too
                    results.append((source, claim, f"QUERY ERROR: {exc}"[:160]))
                    continue
                results.append(
                    (source, claim, "; ".join(str(tuple(r)) for r in rows[:5]) if rows else "")
                )
            results.extend(await _projection_checks(conn, since))

        print(
            f"Claims are asked of behaviour since {since:%Y-%m-%d %H:%M} "
            f"(projector v{PROJECTOR_VERSION}). Use --since 1970-01-01 for all history.\n"
        )
        for source, claim, evidence in results:
            if evidence:
                failed += 1
                print(f"[FALSIFIED] {source:<8} {claim}")
                print(f"            -> {evidence}")
            else:
                print(f"[   ok    ] {source:<8} {claim}")
        print(f"\n{len(results) - failed}/{len(results)} claims hold.")
        if failed:
            print(
                "A falsified claim is either a defect or a document that has drifted. "
                "Both need an edit; decide which."
            )
        return 1 if failed else 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default="2026-09-18 23:00:00",
        help="lower bound for the history-sensitive checks (default: when "
        "at-rest projection began stamping rows)",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
