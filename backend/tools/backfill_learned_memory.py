#!/usr/bin/env python3
"""Backfill the learned-memory (veracium) store from completed workflow runs.

The write-only slice only observes runs that complete *after* it shipped.
This tool seeds the store from history: it reads COMPLETED instances of a
workflow from the repositories, rebuilds each run's context (trigger payload
+ step outputs), renders the same `learned_memory.observations` templates the
engine uses, and ingests them through the same `LearnedMemoryService.observe`
path — so authorship, quarantine, metering, and audit shape all match organic
writes. Each write appends a `memory_observed` audit entry (with
`backfill: true`) to the source instance, which doubles as the idempotency
marker: instances that already have one are skipped, so re-runs and overlap
with the live engine hook are harmless.

Costs real Bedrock spend (one distill call per observation). An observation
whose template references a `steps.*` path that resolves to nothing is
skipped (e.g. the record step didn't parse) — historical data quality guard.

Usage:
    DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \\
        uv run python tools/backfill_learned_memory.py [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.bedrock import BedrockClient
from workflow_platform.engine.context import WorkflowContext
from workflow_platform.engine.executor import (
    _render_observation_template,
    _resolve_context_value,
)
from workflow_platform.memory import LearnedMemoryService
from workflow_platform.persistence import (
    AuditEntry,
    Repositories,
    StepExecutionState,
    WorkflowInstanceState,
)
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories
from workflow_platform.workflow import ObservationSpec, load_definition_from_file

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DEFINITION = BACKEND_DIR.parent / "examples" / "email_triage_live" / "workflow.yaml"
DEFAULT_LEARNED_DB = BACKEND_DIR / ".memory" / "learned.db"

_STEP_PLACEHOLDER = re.compile(r"\{(steps\.[A-Za-z0-9_.]+)\}")


def _steps_placeholders_resolve(spec: ObservationSpec, context: WorkflowContext) -> bool:
    """True when every `{steps.*}` placeholder in the template has a value.
    Trigger paths may be legitimately sparse (e.g. a null name); step paths
    missing mean the source run's output is unusable for this observation."""
    return all(
        _resolve_context_value(context, path) is not None
        for path in _STEP_PLACEHOLDER.findall(spec.text)
    )


async def backfill(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set — the backfill reads run history from Postgres.")
        return 2

    definition = load_definition_from_file(Path(args.definition).resolve())
    spec = definition.learned_memory
    if spec is None:
        print(f"{args.definition} declares no learned_memory block; nothing to backfill.")
        return 2

    db_engine = make_engine(url)
    repos: Repositories = postgres_repositories(make_session_factory(db_engine))
    bedrock = BedrockClient()
    learned_db = os.environ.get("WORKFLOW_PLATFORM_LEARNED_MEMORY_DB", str(DEFAULT_LEARNED_DB))
    service = LearnedMemoryService(bedrock, learned_db)
    print(f"workflow    : {definition.id}")
    print(f"memory user : {spec.user_id}")
    print(f"store       : {learned_db}")
    print(f"bedrock     : mode={bedrock.mode.value} model={service.model_id}")

    observed_msgs = 0
    observed_writes = 0
    skipped_done = 0
    skipped_obs = 0
    total_tokens = 0
    total_cost = 0.0
    try:
        instances = await repos.instances.list_by_workflow(definition.id)
        instances = [i for i in instances if i.state == WorkflowInstanceState.COMPLETED]
        instances.sort(key=lambda i: i.started_at)
        if args.limit:
            instances = instances[: args.limit]
        print(f"instances   : {len(instances)} completed candidates\n")

        for instance in instances:
            existing = await repos.audit.list_by_instance(instance.id)
            if any(e.action == "memory_observed" for e in existing):
                skipped_done += 1
                continue

            context = WorkflowContext(
                instance_id=instance.id,
                workflow_id=definition.id,
                trigger=dict(instance.trigger_payload),
            )
            for step in await repos.steps.list_by_instance(instance.id):
                if step.state == StepExecutionState.COMPLETED and step.output:
                    context.record_step_output(step.step_id, step.output)

            subject = str(context.trigger.get("subject", ""))[:60]
            for index, obs in enumerate(spec.observations):
                if not _steps_placeholders_resolve(obs, context):
                    skipped_obs += 1
                    print(f"  ~ obs {index} skipped (unresolved steps.*): {subject!r}")
                    continue
                text = _render_observation_template(obs.text, context)
                if not text.strip():
                    skipped_obs += 1
                    continue
                if args.dry_run:
                    observed_writes += 1
                    continue
                date_raw = _resolve_context_value(context, obs.date_from)
                ref_raw = _resolve_context_value(context, obs.ref_from)
                result = await service.observe(
                    spec.user_id,
                    text,
                    author=obs.author,
                    event_type=obs.event_type,
                    date=str(date_raw)[:10] if date_raw else None,
                    evidence_ref=str(ref_raw) if ref_raw is not None else None,
                )
                await repos.audit.append(
                    AuditEntry(
                        actor_type="engine",
                        actor_id="learned_memory",
                        action="memory_observed",
                        workflow_instance_id=instance.id,
                        detail={
                            "user_id": spec.user_id,
                            "observation": index,
                            "backfill": True,
                            **result.model_dump(),
                        },
                    )
                )
                observed_writes += 1
                total_tokens += result.input_tokens + result.output_tokens
                total_cost += result.cost_usd
            observed_msgs += 1
            print(
                f"[{observed_msgs}] {instance.started_at:%Y-%m-%d} {subject!r}"
                + (" (dry-run)" if args.dry_run else f"  (${total_cost:.4f} so far)")
            )
    finally:
        service.close()
        if hasattr(db_engine, "dispose"):
            await db_engine.dispose()

    print(
        f"\nDONE: {observed_msgs} instance(s), {observed_writes} observation(s) "
        f"{'rendered' if args.dry_run else 'written'}; "
        f"{skipped_done} already observed, {skipped_obs} observation(s) skipped."
    )
    if not args.dry_run:
        print(f"spend: {total_tokens} tokens, ${total_cost:.4f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--definition",
        default=str(DEFAULT_DEFINITION),
        help="workflow YAML with the learned_memory block (default: email_triage_live)",
    )
    parser.add_argument("--limit", type=int, default=0, help="max instances (0 = all)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="render + count observations without LLM calls or writes",
    )
    return asyncio.run(backfill(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
