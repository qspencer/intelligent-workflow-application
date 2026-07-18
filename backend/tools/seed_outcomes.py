#!/usr/bin/env python3
"""Seed veracium outcome events from the labeled corpus + judge report.

The two offline emitters (veracium V4, >=0.3.0b1):

- **Review labels** (`triage-ground-truth.jsonl`) → `confirmed` /
  `corrected` (+ true value), actor=user — the strongest signal.
- **Judge report** (judge_email_triage.py JSON) → `concurred` /
  `challenged`, actor=system — a weak signal, recorded as such.

For each judged message, outcomes attach to the sender's currently-recalled
edges (the facts a run consulting this sender would use). Historical runs
predate act-time use recording, so these create fresh outcome events keyed
by evidence_ref = the run's instance id; the engine's act-time events and
future judgments upgrade by the same key. Idempotent: replaying an
evidence_ref upgrades in place — safe to re-run.

Zero LLM calls (outcome recording is pure store writes). Order matters:
judge events are recorded FIRST so the stronger human labels upgrade over
them where both exist for the same run.

Usage:
    DATABASE_URL=postgresql+asyncpg://... uv run python tools/seed_outcomes.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.bedrock import BedrockClient
from workflow_platform.memory import LearnedMemoryService, memory_namespace, normalize_entity
from workflow_platform.persistence import Repositories, WorkflowInstanceState
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories
from workflow_platform.workflow import load_definition_from_file

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DEFINITION = BACKEND_DIR.parent / "examples" / "email_triage_live" / "workflow.yaml"
DEFAULT_LEARNED_DB = BACKEND_DIR / ".memory" / "learned.db"
DEFAULT_LABELS = BACKEND_DIR / ".memory" / "triage-ground-truth.jsonl"
DEFAULT_JUDGE_REPORT = Path("/tmp/email-triage-judge-report.json")


async def run(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set.")
        return 2
    definition = load_definition_from_file(Path(args.definition).resolve())
    spec = definition.learned_memory
    if spec is None or spec.recall is None:
        print("Workflow has no learned_memory.recall block; nothing to attach outcomes to.")
        return 2

    db_engine = make_engine(url)
    repos: Repositories = postgres_repositories(make_session_factory(db_engine))
    service = LearnedMemoryService(
        BedrockClient(),
        os.environ.get("WORKFLOW_PLATFORM_LEARNED_MEMORY_DB", str(DEFAULT_LEARNED_DB)),
    )

    # message_id -> (latest completed instance id, sender, memory_hash)
    runs: dict[str, tuple[str, str, str | None]] = {}
    try:
        instances = await repos.instances.list_by_workflow(definition.id)
        instances.sort(key=lambda i: i.started_at or i.created_at)
        for inst in instances:
            if inst.state != WorkflowInstanceState.COMPLETED:
                continue
            sender = str((inst.trigger_payload.get("from_address") or {}).get("address") or "")
            mid = str(inst.trigger_payload.get("message_id") or inst.id)
            if not sender:
                continue
            steps = await repos.steps.list_by_instance(inst.id)
            triage = next((s for s in steps if s.step_id == "triage"), None)
            mem_hash = (triage.output or {}).get("memory_hash") if triage else None
            runs[mid] = (inst.id, sender, mem_hash)
    finally:
        if hasattr(db_engine, "dispose"):
            await db_engine.dispose()
    print(f"runs indexed: {len(runs)} (latest completed per message)")

    events: list[dict[str, object]] = []
    # Judge first (weak) so human labels (strong) upgrade over them.
    judge_path = Path(args.judge_report)
    if judge_path.is_file():
        for row in json.loads(judge_path.read_text()):
            if not isinstance(row, dict) or "judge_category" not in row:
                continue
            outcome = (
                "concurred" if row["judge_category"] == row.get("agent_category") else "challenged"
            )
            events.append({"message_id": row["message_id"], "outcome": outcome, "actor": "system"})
    labels_path = Path(args.labels)
    if labels_path.is_file():
        seen: dict[str, dict[str, object]] = {}
        for line in labels_path.read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                seen[e["message_id"]] = e  # last answer wins
        for e in seen.values():
            correct = e["verdict"] == "correct"
            events.append(
                {
                    "message_id": e["message_id"],
                    "outcome": "confirmed" if correct else "corrected",
                    "actor": "user",
                    "corrected_value": None if correct else e["true_category"],
                }
            )
    print(f"events to record: {len(events)} (judge first, then human labels)")
    # Offline seeder for the (single-org) email-triage store: every source run
    # lives in the default org. Resolve per-instance if that ever changes.
    namespace = memory_namespace("default", spec.user_id)

    stats: Counter[str] = Counter()
    recall_cache: dict[str, list[str]] = {}
    for ev in events:
        run_info = runs.get(str(ev["message_id"]))
        if run_info is None:
            stats["no_run"] += 1
            continue
        instance_id, sender, mem_hash = run_info
        entity = normalize_entity(sender)
        if entity not in recall_cache:
            recalled = await service.recall_context(namespace, entity, token_budget=600)
            recall_cache[entity] = recalled.edge_ids
        edge_ids = recall_cache[entity]
        if not edge_ids:
            stats["no_edges"] += 1
            continue
        if args.dry_run:
            stats[f"would_{ev['outcome']}"] += 1
            continue
        result = await service.record_outcomes(
            namespace,
            edge_ids,
            outcome=str(ev["outcome"]),
            evidence_ref=instance_id,
            actor=str(ev["actor"]),
            corrected_value=(str(ev["corrected_value"]) if ev.get("corrected_value") else None),
            context_ref=mem_hash,
        )
        stats[str(ev["outcome"])] += 1
        stats["edges_recorded"] += result["recorded"]
        stats["edges_upgraded"] += result["upgraded"]
        stats["edges_failed"] += result["failed"]
    service.close()

    print("\nDONE:")
    for key, count in sorted(stats.items()):
        print(f"  {key:16s} {count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", default=str(DEFAULT_DEFINITION))
    parser.add_argument("--labels", default=str(DEFAULT_LABELS))
    parser.add_argument("--judge-report", default=str(DEFAULT_JUDGE_REPORT))
    parser.add_argument("--dry-run", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
