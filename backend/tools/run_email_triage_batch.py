"""Run a batch of `data/email_triage/<account>/*.json` files through the
email_triage workflow and print a per-message triage summary.

Designed for the validation-against-real-mail loop:
  1. `tools/fetch_gmail_inbox.py` pulls N messages to data/email_triage/.
  2. This script fires each through the workflow and prints what the
     agent classified them as.
  3. You compare the agent's `category` / `summary` to your own
     judgment and decide where the rubric drifts.

In-memory repos, live Bedrock. Sets `WORKFLOW_PLATFORM_GMAIL_ACCOUNT`
internally so the EmailSend + EmailLabelApply tools wire up — meaning
the agent *can* actually send mail or apply labels. Both are
controlled by the rubric, which biases hard toward "no reply".

Usage (from repo root):
    BEDROCK_LIVE=1 AWS_REGION=us-east-1 \\
        uv run --project backend python backend/tools/run_email_triage_batch.py \\
        --account intelligent.workflow.engine@quentinspencer.com

Or from `backend/`:
    BEDROCK_LIVE=1 uv run python tools/run_email_triage_batch.py --account <addr>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIR = REPO_ROOT / "examples" / "email_triage"
DATA_DIR = REPO_ROOT / "data" / "email_triage"


async def run_batch(account: str, limit: int | None) -> int:
    # Wire the email-tool catalog by setting the env var before main builds.
    os.environ["WORKFLOW_PLATFORM_GMAIL_ACCOUNT"] = account

    from workflow_platform.bedrock import BedrockClient, BedrockMode
    from workflow_platform.connectors.email.bootstrap import maybe_build_gmail_connector
    from workflow_platform.engine import ToolCatalog, WorkflowEngine
    from workflow_platform.engine.functions import default_function_registry
    from workflow_platform.memory import MemoryManager
    from workflow_platform.persistence import in_memory_repositories
    from workflow_platform.secrets import EnvSecretStore
    from workflow_platform.tools import EmailLabelApplyTool, EmailSendTool
    from workflow_platform.workflow import load_definition_from_yaml
    from workflow_platform.world import real_world

    inbox_dir = DATA_DIR / account
    fixtures = sorted(inbox_dir.glob("*.json"))
    if limit:
        fixtures = fixtures[:limit]
    if not fixtures:
        print(f"No fixtures in {inbox_dir}. Run fetch_gmail_inbox.py first.")
        return 1
    print(f"Found {len(fixtures)} messages to triage.\n")

    # Seed agent memory from the workflow's agent_memory.md (G6 auto-load).
    memory = MemoryManager(REPO_ROOT / ".memory")
    memory_text = (EXAMPLE_DIR / "agent_memory.md").read_text()

    definition = load_definition_from_yaml((EXAMPLE_DIR / "workflow.yaml").read_text())
    for step in definition.steps:
        if step.type == "agentic":
            await memory.write_raw(f"steps/{definition.id}/{step.id}", memory_text)

    # Build the connector + tools.
    connector = maybe_build_gmail_connector(account=account, secret_store=EnvSecretStore())
    if connector is None:
        print(f"Failed to build Gmail connector for {account}.")
        return 1
    tool_catalog = ToolCatalog([EmailSendTool(connector), EmailLabelApplyTool(connector)])

    bedrock = BedrockClient(mode=BedrockMode.LIVE, region=os.environ.get("AWS_REGION", "us-east-1"))

    results: list[dict[str, Any]] = []
    total_cost = 0.0
    total_tokens = 0

    for fixture_path in fixtures:
        repos = in_memory_repositories()
        engine = WorkflowEngine(
            repositories=repos,
            functions=default_function_registry(),
            tools=tool_catalog,
            bedrock=bedrock,
            world=real_world(),
            memory=memory,
        )
        trigger = json.loads(fixture_path.read_text())
        try:
            instance = await engine.run(definition, trigger_payload=trigger)
        except Exception as exc:
            print(f"  [{fixture_path.name}] EXCEPTION: {exc}")
            results.append({"file": fixture_path.name, "error": str(exc)})
            continue

        ctx = instance.context
        record_out = ctx.get("steps", {}).get("record", {})
        triage_out = ctx.get("steps", {}).get("triage", {})
        cost = ctx.get("total_cost_usd", 0.0)
        tokens = ctx.get("total_tokens", 0)
        total_cost += cost
        total_tokens += tokens

        results.append(
            {
                "file": fixture_path.name,
                "subject": trigger.get("subject", "")[:60],
                "from": trigger.get("from_address", {}).get("address", ""),
                "state": instance.state.value,
                "parse_ok": record_out.get("parse_ok"),
                "category": record_out.get("category"),
                "confidence": record_out.get("confidence"),
                "reply_drafted": record_out.get("reply_drafted"),
                "labels_applied": record_out.get("labels_applied"),
                "summary": record_out.get("summary"),
                "raw": record_out.get("raw") if not record_out.get("parse_ok") else None,
                "agent_text": triage_out.get("output_text", "")[:200],
                "tokens": tokens,
                "cost_usd": cost,
            }
        )

    # ----- print per-message table -----
    print()
    print(f"{'#':>3}  {'cat':<16} {'conf':>5} {'reply':>5} {'state':<10} {'subject':<60}")
    print("-" * 110)
    for i, r in enumerate(results, 1):
        if "error" in r:
            print(f"{i:>3}  ERROR: {r['error'][:80]}")
            continue
        cat = r.get("category") or ("<unparsed>" if r["parse_ok"] is False else "?")
        conf = f"{r['confidence']:.2f}" if isinstance(r.get("confidence"), int | float) else "-"
        reply = "YES" if r.get("reply_drafted") else "no"
        print(
            f"{i:>3}  {cat:<16} {conf:>5} {reply:>5} {r['state']:<10} {r['subject']:<60}"
        )

    # ----- category histogram -----
    print()
    print("Category distribution:")
    histogram: dict[str, int] = {}
    for r in results:
        cat = r.get("category") or "<unparsed>"
        histogram[cat] = histogram.get(cat, 0) + 1
    for cat, count in sorted(histogram.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:<20} {count}")

    # ----- replies drafted + label discipline -----
    replies = [r for r in results if r.get("reply_drafted")]
    print()
    print(f"Replies drafted: {len(replies)}")
    for r in replies:
        print(f"  - [{r['category']}] {r['subject']}")
        print(f"    summary: {r['summary']}")

    # ----- summary table -----
    print()
    print(f"Total: {len(results)} runs, {total_tokens} tokens, ${total_cost:.4f}")

    # ----- write detailed json beside the inbox dir -----
    detail = REPO_ROOT / "data" / "email_triage" / f"{account}_triage_results.json"
    detail.write_text(json.dumps(results, indent=2, default=str))
    print(f"Full results: {detail}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--limit", type=int, help="Process at most N files (default all)")
    args = parser.parse_args()
    return asyncio.run(run_batch(args.account, args.limit))


if __name__ == "__main__":
    sys.exit(main())
