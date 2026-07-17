#!/usr/bin/env python3
"""LLM-as-judge evaluation pass over email-triage verdicts.

For every completed run of a triage workflow, a stronger model *blindly*
re-classifies the stored message against the same rubric (it never sees the
agent's verdict, so there's no anchoring), then the script reports
agent-vs-judge agreement, a confusion matrix, and the disagreement list —
the human then adjudicates only the disagreements instead of every message.

Judge agreement is NOT ground truth; it's a triage of the human's review
effort. Mirrors the PDF-classifier eval-loop pattern (R1).

When one message has several runs (e.g. a fork re-ran it under a newer
rubric), only the LATEST verdict is judged — that's the tool's current
behavior.

Usage:
    DATABASE_URL=postgresql+asyncpg://... uv run python tools/judge_email_triage.py \\
        [--workflow email-triage-live] [--limit N] [--report /path/report.json]

Costs real Bedrock spend (~$0.01/message at Sonnet pricing).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.bedrock import BedrockClient
from workflow_platform.cost.pricing import cost_for_usage
from workflow_platform.engine.functions import TRIAGE_CATEGORIES
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RUBRIC = BACKEND_DIR.parent / "examples" / "email_triage_live" / "agent_memory.md"
JUDGE_MODEL = os.environ.get("WORKFLOW_PLATFORM_JUDGE_MODEL", "us.anthropic.claude-sonnet-4-6")
CATEGORIES = set(TRIAGE_CATEGORIES)
BODY_CAP = 4000
CONCURRENCY = 4

JUDGE_PROMPT = """You are independently auditing an email-triage system. Classify the
email below into exactly one category, applying the rubric verbatim. You are
the judge: careful, literal, unswayed by urgency language.

--- RUBRIC ---
{rubric}
--- END RUBRIC ---

--- EMAIL ---
From: {from_name} <{from_address}>
Subject: {subject}
Received: {received_at}

{body}
--- END EMAIL ---

Respond with ONLY a JSON object on one line:
{{"category": "<one of: urgent|awaiting-reply|personal|notification|newsletter|promotion|spam>", "confidence": <0..1>, "reasoning": "<one sentence>"}}"""


def _find_json(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _judge_one(
    bedrock: BedrockClient,
    rubric: str,
    item: dict[str, Any],
    sem: asyncio.Semaphore,
    totals: dict[str, float],
) -> None:
    trigger = item["trigger"]
    from_addr = (trigger.get("from_address") or {}) if isinstance(trigger, dict) else {}
    body = str(trigger.get("body_text") or "")
    if len(body) > BODY_CAP:
        body = body[:BODY_CAP] + f"\n[... truncated, {len(body) - BODY_CAP} more chars]"
    prompt = JUDGE_PROMPT.format(
        rubric=rubric,
        from_name=from_addr.get("name") or "",
        from_address=from_addr.get("address") or "",
        subject=trigger.get("subject") or "",
        received_at=trigger.get("received_at") or "",
        body=body,
    )
    async with sem:
        try:
            response = await bedrock.converse(
                model_id=JUDGE_MODEL,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inference_config={"maxTokens": 300},
            )
        except Exception as exc:
            item["judge_error"] = str(exc)
            return
    usage = response.get("usage", {})
    totals["input"] += int(usage.get("inputTokens", 0))
    totals["output"] += int(usage.get("outputTokens", 0))
    content = response.get("output", {}).get("message", {}).get("content", [])
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    parsed = _find_json(text)
    if parsed is None or parsed.get("category") not in CATEGORIES:
        item["judge_error"] = f"unparseable judge response: {text[:200]!r}"
        return
    item["judge_category"] = parsed["category"]
    item["judge_confidence"] = parsed.get("confidence")
    item["judge_reasoning"] = parsed.get("reasoning")


async def run(args: argparse.Namespace) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set — the judge reads run history from Postgres.")
        return 2
    rubric = Path(args.rubric).read_text()

    db_engine = make_engine(url)
    repos = postgres_repositories(make_session_factory(db_engine))
    try:
        instances = await repos.instances.list_by_workflow(args.workflow)
        instances.sort(key=lambda i: i.started_at or i.created_at)
        # Latest verdict per message_id: forks/re-runs supersede earlier runs.
        items_by_msg: dict[str, dict[str, Any]] = {}
        for inst in instances:
            steps = await repos.steps.list_by_instance(inst.id)
            record = next(
                (s for s in steps if s.step_id == "record" and (s.output or {}).get("parse_ok")),
                None,
            )
            if record is None or record.output is None:
                continue
            mid = str(inst.trigger_payload.get("message_id") or inst.id)
            items_by_msg[mid] = {
                "message_id": mid,
                "instance_id": inst.id,
                "trigger": inst.trigger_payload,
                "agent_category": record.output.get("category"),
                "agent_confidence": record.output.get("confidence"),
                "agent_summary": record.output.get("summary"),
            }
    finally:
        if hasattr(db_engine, "dispose"):
            await db_engine.dispose()

    items = list(items_by_msg.values())
    if args.limit:
        items = items[: args.limit]
    print(f"workflow : {args.workflow}")
    print(f"judge    : {JUDGE_MODEL} (blind — never sees the agent's verdict)")
    print(f"messages : {len(items)} (latest verdict per message)\n")

    bedrock = BedrockClient()
    sem = asyncio.Semaphore(CONCURRENCY)
    totals: dict[str, float] = {"input": 0, "output": 0}
    await asyncio.gather(*(_judge_one(bedrock, rubric, i, sem, totals) for i in items))

    judged = [i for i in items if "judge_category" in i]
    errors = [i for i in items if "judge_error" in i]
    agree = [i for i in judged if i["judge_category"] == i["agent_category"]]
    disagree = [i for i in judged if i["judge_category"] != i["agent_category"]]

    confusion: Counter[tuple[str, str]] = Counter(
        (str(i["agent_category"]), str(i["judge_category"])) for i in judged
    )
    cost = cost_for_usage(
        {"input_tokens": int(totals["input"]), "output_tokens": int(totals["output"])},
        JUDGE_MODEL,
    )

    print(
        f"judged   : {len(judged)}  agree: {len(agree)}  disagree: {len(disagree)}"
        f"  errors: {len(errors)}"
    )
    if judged:
        print(f"agreement: {100 * len(agree) / len(judged):.1f}%")
    print(
        f"spend    : {int(totals['input'])} in / {int(totals['output'])} out tokens, ${cost:.2f}\n"
    )

    if disagree:
        print("=== disagreements (agent -> judge) ===")
        for i in disagree:
            trig = i["trigger"]
            sender = ((trig.get("from_address") or {}).get("address")) or "?"
            print(
                f"- {i['agent_category']} -> {i['judge_category']}"
                f"  [{sender}] {str(trig.get('subject'))[:60]!r}"
            )
            print(f"    agent: {str(i['agent_summary'])[:110]}")
            print(f"    judge: {str(i['judge_reasoning'])[:110]}")
    if confusion:
        print("\n=== confusion (agent, judge) -> count ===")
        for (a, j), n in confusion.most_common():
            marker = "" if a == j else "   <-- disagreement"
            print(f"  {a:>15} , {j:<15} {n}{marker}")

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(items, indent=2, default=str))
    print(f"\nfull report: {report_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", default="email-triage-live")
    parser.add_argument("--rubric", default=str(DEFAULT_RUBRIC))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--report",
        default="/tmp/email-triage-judge-report.json",
        help="where the full per-message JSON lands (contains mail content — keep private)",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
