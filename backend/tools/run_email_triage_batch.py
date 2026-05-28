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


def _trim_trigger(
    trigger: dict[str, Any],
    *,
    max_body_chars: int,
    strip_html: bool,
) -> dict[str, Any]:
    """Reduce an EmailMessage trigger payload to its classification-relevant
    fields.

    Real Gmail messages can carry 100KB+ of `body_html` (the same content
    as `body_text` plus markup, base64 inline images, marketing template
    layouts). The agent doesn't need it — `body_text` is the
    classification signal. Stripping `body_html` typically saves
    50-90% of input tokens per turn.

    Also truncates `body_text` to `max_body_chars` — long reply chains
    and marketing fluff add bulk without category signal. The first
    ~5000 chars almost always carry the relevant content.
    """
    trimmed = dict(trigger)
    if strip_html and "body_html" in trimmed:
        trimmed["body_html"] = None
    body_text = trimmed.get("body_text") or ""
    if isinstance(body_text, str) and len(body_text) > max_body_chars:
        trimmed["body_text"] = body_text[:max_body_chars] + "\n[truncated]"
    return trimmed


async def run_batch(
    account: str,
    limit: int | None,
    workflow_file: str,
    concurrency: int,
    max_body_chars: int,
    strip_html: bool,
) -> int:
    # Wire the email-tool catalog by setting the env var before main builds.
    os.environ["WORKFLOW_PLATFORM_GMAIL_ACCOUNT"] = account

    from workflow_platform.bedrock import BedrockClient, BedrockMode
    from workflow_platform.connectors.email.bootstrap import maybe_build_gmail_connector
    from workflow_platform.engine import ToolCatalog, WorkflowEngine
    from workflow_platform.engine.functions import default_function_registry
    from workflow_platform.memory import MemoryManager
    from workflow_platform.persistence import in_memory_repositories
    from workflow_platform.secrets import EnvSecretStore
    from workflow_platform.tools import EmailLabelApplyTool, EmailSendTool, Tool
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

    wf_path = EXAMPLE_DIR / workflow_file
    if not wf_path.exists():
        print(f"Workflow file not found: {wf_path}")
        return 1
    definition = load_definition_from_yaml(wf_path.read_text())
    print(f"Workflow: {definition.id}  ({wf_path.name})")
    for step in definition.steps:
        if step.type == "agentic":
            await memory.write_raw(f"steps/{definition.id}/{step.id}", memory_text)

    # Build the connector + tools. Only wire the tools the workflow actually
    # references, so a label-only workflow doesn't carry a usable
    # `email_send` in the catalog even by accident.
    connector = maybe_build_gmail_connector(account=account, secret_store=EnvSecretStore())
    if connector is None:
        print(f"Failed to build Gmail connector for {account}.")
        return 1
    referenced_tool_names: set[str] = set()
    for step in definition.steps:
        if step.type == "agentic":
            referenced_tool_names.update(step.tools)
    available: list[Tool] = []
    if "email_send" in referenced_tool_names:
        available.append(EmailSendTool(connector))
    if "email_label_apply" in referenced_tool_names:
        available.append(EmailLabelApplyTool(connector))
    tool_catalog = ToolCatalog(available)

    bedrock = BedrockClient(mode=BedrockMode.LIVE, region=os.environ.get("AWS_REGION", "us-east-1"))

    print(f"Dispatching {len(fixtures)} runs with concurrency={concurrency}…")
    sem = asyncio.Semaphore(concurrency)
    done_count = 0
    done_lock = asyncio.Lock()

    async def _run_one(fixture_path: Path) -> dict[str, Any]:
        nonlocal done_count
        async with sem:
            # Each run gets its own fresh in-memory repo + WorkflowEngine
            # instance — engines aren't designed to be reused concurrently
            # (single audit-log channel, single context). Shared across
            # tasks: bedrock client, gmail connector, memory manager,
            # tool catalog — all read-only or call-scoped.
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
            trigger = _trim_trigger(trigger, max_body_chars=max_body_chars, strip_html=strip_html)
            try:
                instance = await engine.run(definition, trigger_payload=trigger)
            except Exception as exc:
                async with done_lock:
                    done_count += 1
                    print(f"  [{done_count}/{len(fixtures)}] EXCEPTION {fixture_path.name}: {exc}")
                return {"file": fixture_path.name, "error": str(exc)}

            ctx = instance.context
            record_out = ctx.get("steps", {}).get("record", {})
            triage_out = ctx.get("steps", {}).get("triage", {})
            cost = ctx.get("total_cost_usd", 0.0)
            tokens = ctx.get("total_tokens", 0)
            row = {
                "file": fixture_path.name,
                "subject": trigger.get("subject", "")[:60],
                "from": trigger.get("from_address", {}).get("address", ""),
                "state": instance.state.value,
                "instance_error": instance.error,
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
            async with done_lock:
                done_count += 1
                cat = row.get("category") or ("<unparsed>" if row["parse_ok"] is False else "?")
                print(
                    f"  [{done_count}/{len(fixtures)}] {cat:<16} ${cost:.4f} "
                    f"{row['subject'][:50]!r}"
                )
            return row

    # Run fixtures in chunks. Two reasons NOT to gather all at once:
    #   1. Pre-creating 1000 Task objects + 1000 pending Semaphore.acquire()
    #      waits inflates the event loop state.
    #   2. If a single task raises with return_exceptions=False, gather
    #      cancels every pending task — cancelling 1000 in-flight
    #      to_thread() boto3/google-api calls has segfaulted on this box.
    #   chunk_size 50 keeps in-flight tasks small enough to avoid both,
    #   without sacrificing the concurrency win.
    chunk_size = 50
    results: list[dict[str, Any]] = []
    for chunk_start in range(0, len(fixtures), chunk_size):
        chunk = fixtures[chunk_start : chunk_start + chunk_size]
        chunk_results = await asyncio.gather(
            *(_run_one(fp) for fp in chunk), return_exceptions=True
        )
        for fp, result in zip(chunk, chunk_results, strict=True):
            if isinstance(result, BaseException):
                results.append({"file": fp.name, "error": f"{type(result).__name__}: {result}"})
            else:
                results.append(result)
    total_cost = sum(float(r.get("cost_usd", 0.0)) for r in results)
    total_tokens = sum(int(r.get("tokens", 0)) for r in results)

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
        print(f"{i:>3}  {cat:<16} {conf:>5} {reply:>5} {r['state']:<10} {r['subject']:<60}")

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
    parser.add_argument(
        "--workflow",
        default="workflow.yaml",
        help=(
            "Workflow YAML filename within examples/email_triage/ "
            "(default workflow.yaml; use workflow_label_only.yaml to skip the "
            "email_send tool when validating against a personal inbox)."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help=(
            "Number of agent runs to dispatch concurrently (default 1 = "
            "sequential). 8-10 is a sensible upper bound; Bedrock TPS "
            "limits are well above that and Gmail label-apply quotas are "
            "negligible at this rate. Higher values won't speed up much "
            "if your per-run latency is bounded by Bedrock response time."
        ),
    )
    parser.add_argument(
        "--max-body-chars",
        type=int,
        default=5000,
        help=(
            "Truncate each message's body_text to this many chars before "
            "passing to the workflow (default 5000). Real classification "
            "signal is in the first paragraph; long bodies are reply "
            "chains and marketing fluff that bloat token use without "
            "improving accuracy."
        ),
    )
    parser.add_argument(
        "--keep-html",
        action="store_true",
        help=(
            "By default, body_html is stripped from the trigger payload "
            "before invoking the workflow (body_text is the same content "
            "minus markup, and HTML carries 5-10x more tokens). Pass "
            "--keep-html to ship the HTML version too — almost never "
            "what you want for classification."
        ),
    )
    args = parser.parse_args()
    return asyncio.run(
        run_batch(
            args.account,
            args.limit,
            args.workflow,
            args.concurrency,
            args.max_body_chars,
            strip_html=not args.keep_html,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
