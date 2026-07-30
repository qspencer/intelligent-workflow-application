#!/usr/bin/env python3
"""Generate the codified-sender rule artifact (EMAIL_TRIAGE_CODIFY_PLAN v2 §3).

Evidence sources:
- Postgres step_executions x workflow_instances: the distinct-adjudicated-
  message verdicts (category, attention, schema version, decision source).
- The veracium store: human corrections (disqualifiers; also feed the
  domain-level disqualification with its freemail exemption) and legacy
  confirmed counters (supporting context only — never satisfy the floor).

Dry-run by default: prints promoted / retained / demoted / rejected with
reasons. `--apply` writes the artifact atomically (temp file + rename,
0600) to `.memory/codified/<org>/<account>/<workflow>.json`.
`--explain sender@x` prints the full eligibility trace for one sender.

Usage:
    uv run python tools/codify_senders.py [--apply] [--explain S]
        [--org default] [--account qspencer@gmail.com]
        [--workflow email-triage-apply]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.codify import (
    Eligibility,
    SenderFacts,
    VerdictRow,
    corrected_domain_set,
    evaluate_sender,
)
from workflow_platform.engine.functions import TRIAGE_SCHEMA_VERSION

CODIFIER_VERSION = 1


async def load_verdicts(database_url: str, workflow_id: str) -> dict[str, list[VerdictRow]]:
    """Verdict rows grouped by normalized sender, from record-step outputs."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url)
    rows: dict[str, list[VerdictRow]] = defaultdict(list)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT i.trigger_payload->'from_address'->>'address' AS sender,
                           i.trigger_payload->>'message_id' AS message_id,
                           s.output->>'category' AS category,
                           s.output->'attention' AS attention,
                           s.output->>'triage_schema_version' AS schema_version,
                           s.output->>'decision_source' AS decision_source,
                           s.completed_at AS at
                    FROM step_executions s
                    JOIN workflow_instances i ON i.id = s.instance_id
                    WHERE i.workflow_id = :wf AND s.step_id = 'record'
                      AND s.state = 'completed'
                      AND s.output->>'category_valid' = 'true'
                    """
                ),
                {"wf": workflow_id},
            )
            for r in result.mappings():
                if not r["sender"] or not r["message_id"]:
                    continue
                attention = r["attention"]
                if isinstance(attention, str):
                    try:
                        attention = json.loads(attention)
                    except ValueError:
                        attention = []
                at = r["at"]
                if at is not None and at.tzinfo is None:
                    at = at.replace(tzinfo=UTC)
                rows[str(r["sender"]).strip().lower()].append(
                    VerdictRow(
                        sender=str(r["sender"]).strip().lower(),
                        message_id=str(r["message_id"]),
                        category=str(r["category"] or ""),
                        attention=attention if isinstance(attention, list) else [],
                        schema_version=int(r["schema_version"] or 1),
                        decision_source=str(r["decision_source"] or "classifier"),
                        at=at or datetime.now(UTC),
                    )
                )
    finally:
        await engine.dispose()
    return rows


def load_store_facts(store_path: str, namespace: str) -> tuple[dict[str, SenderFacts], list[str]]:
    """Per-sender disqualifier facts + the corrected-sender list, from the
    veracium store. Corrections are outcome events with corrected counts on
    edges whose subject/note carries the sender address."""
    facts: dict[str, SenderFacts] = defaultdict(SenderFacts)
    corrected_senders: set[str] = set()
    con = sqlite3.connect(store_path)
    try:
        for (j,) in con.execute("SELECT json FROM edges WHERE user_id = ?", (namespace,)):
            d = json.loads(j)
            counts = d.get("outcome_counts") or {}
            text_blob = " ".join(str(d.get(k) or "") for k in ("subject", "object", "note")).lower()
            confirmed = int(counts.get("confirmed", 0))
            corrected = int(counts.get("corrected", 0))
            if not (confirmed or corrected):
                continue
            for token in text_blob.replace("<", " ").replace(">", " ").split():
                if "@" in token and "." in token:
                    sender = token.strip("().,;:'\"").lower()
                    if corrected:
                        facts[sender].corrected += corrected
                        corrected_senders.add(sender)
                    if confirmed:
                        facts[sender].legacy_confirmed += confirmed
    finally:
        con.close()
    return facts, sorted(corrected_senders)


def build_artifact(
    eligible: list[Eligibility], rubric_hash: str, sample_one_in: int
) -> dict[str, object]:
    return {
        "format_version": 1,
        "triage_schema_version": TRIAGE_SCHEMA_VERSION,
        "rubric_hash": rubric_hash,
        "codifier_version": CODIFIER_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_one_in": sample_one_in,
        "senders": {
            e.sender: {
                "category": e.category,
                "status": "active",
                "distinct_messages": e.distinct_messages,
                "verdict_events_seen": e.verdict_events_seen,
                "current_schema_messages": e.current_schema_messages,
                "legacy_confirmed": e.legacy_confirmed,
                "corrections": 0,
                "first_evidence_at": e.first_evidence_at.isoformat()
                if e.first_evidence_at
                else None,
                "last_evidence_at": e.last_evidence_at.isoformat() if e.last_evidence_at else None,
            }
            for e in eligible
        },
    }


def write_atomically(path: Path, artifact: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(artifact, f, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def rubric_hash_of(workflow_dir: Path) -> str:
    memory = (workflow_dir / "agent_memory.md").read_bytes()
    return "sha256:" + hashlib.sha256(memory).hexdigest()[:16]


async def run(args: argparse.Namespace) -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("DATABASE_URL not set — the verdict evidence lives in Postgres")
        return 2
    store_path = os.environ.get("WORKFLOW_PLATFORM_LEARNED_MEMORY_DB", ".memory/learned.db")
    namespace = f"org:{args.org}:user:{args.account}"

    verdicts = await load_verdicts(database_url, args.workflow)
    facts, corrected_senders = load_store_facts(store_path, namespace)
    corrected_domains = corrected_domain_set(corrected_senders)
    now = datetime.now(UTC)

    results = [
        evaluate_sender(
            sender,
            rows,
            facts.get(sender, SenderFacts()),
            mailbox_owner=args.account,
            corrected_domains=corrected_domains,
            now=now,
        )
        for sender, rows in sorted(verdicts.items())
    ]

    if args.explain:
        target = args.explain.strip().lower()
        matches = [e for e in results if e.sender == target]
        if not matches:
            print(f"{target}: no verdict evidence at all")
            return 1
        e = matches[0]
        print(
            f"{e.sender}: {'ELIGIBLE (' + str(e.category) + ')' if e.eligible else 'not eligible'}"
        )
        print(
            f"  distinct={e.distinct_messages} events={e.verdict_events_seen} "
            f"schema2={e.current_schema_messages} legacy_confirmed={e.legacy_confirmed}"
        )
        for reason in e.reasons:
            print(f"  - {reason}")
        return 0

    eligible = [e for e in results if e.eligible]
    rejected = [e for e in results if not e.eligible and e.current_schema_messages > 0]

    artifact_path = Path(".memory/codified") / args.org / args.account / f"{args.workflow}.json"
    previous: set[str] = set()
    if artifact_path.exists():
        with contextlib.suppress(ValueError):
            previous = set(json.loads(artifact_path.read_text()).get("senders", {}))

    for e in eligible:
        tag = "retained" if e.sender in previous else "PROMOTED"
        print(
            f"  + {tag}: {e.sender} -> wf/{e.category} ({e.current_schema_messages} schema-2 msgs)"
        )
    for sender in sorted(previous - {e.sender for e in eligible}):
        print(f"  - DEMOTED: {sender}")
    if args.verbose:
        for e in rejected:
            print(f"  · rejected: {e.sender} — {'; '.join(e.reasons)}")
    print(
        f"senders with evidence: {len(results)}; eligible: {len(eligible)}; "
        f"corrected-domain fence: {sorted(corrected_domains) or 'none'}"
    )

    if not args.apply:
        print("[DRY-RUN] no artifact written (use --apply)")
        return 0

    rubric_hash = rubric_hash_of(Path("../examples") / args.workflow.replace("-", "_"))
    artifact = build_artifact(eligible, rubric_hash, args.sample_one_in)
    write_atomically(artifact_path, artifact)
    print(f"[APPLIED] wrote {artifact_path} ({len(eligible)} senders)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--explain", metavar="SENDER")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--org", default="default")
    parser.add_argument("--account", default="qspencer@gmail.com")
    parser.add_argument("--workflow", default="email-triage-apply")
    parser.add_argument("--sample-one-in", type=int, default=5)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
