#!/usr/bin/env python3
"""Model price/quality benchmark on the platform's REAL classification task
(NEXT_STEPS G18).

Dataset: the 154-message human-labeled email-triage ground truth
(`.memory/triage-ground-truth.jsonl` — metadata only, gitignored, personal).
The 139 labels whose category is in the current 5-bucket vocabulary are
directly usable; the 15 retired urgent/awaiting-reply labels are skipped.
Message bodies are fetched from Gmail read-only at eval time and cached
under `.memory/eval-cache/` (gitignored) so repeat runs and additional
models cost zero Gmail traffic and identical prompts.

Each model gets the SAME prompt the production classifier uses (current
rubric + trigger-shaped payload), and is scored on category accuracy vs
the human label, with tokens / $ / latency from the live calls. Output: a
ranked table + a JSONL record per run for later comparison.

Usage (live Bedrock; ~$0.01-0.05 per model per 10 messages, see --limit):
    BEDROCK_MODE=live uv run python tools/eval_models.py \\
        --models us.anthropic.claude-haiku-4-5-20251001-v1:0 \\
                 us.anthropic.claude-sonnet-4-6-v1:0 \\
        --limit 25
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.bedrock import BedrockClient
from workflow_platform.connectors.email.bootstrap import (
    maybe_build_gmail_connector,
    seed_gmail_env_from_disk,
)
from workflow_platform.cost.pricing import cost_for_usage
from workflow_platform.engine.functions import TRIAGE_CATEGORIES
from workflow_platform.secrets import EnvSecretStore

GROUND_TRUTH = Path(".memory/triage-ground-truth.jsonl")
CACHE_DIR = Path(".memory/eval-cache")
RESULTS = Path(".memory/eval-results.jsonl")
RUBRIC = Path("../examples/email_triage_apply/agent_memory.md")
ACCOUNT = "qspencer@gmail.com"
BODY_CAP = 8000  # mirror the production trigger's body_max_chars


def load_labels(limit: int | None) -> list[dict[str, Any]]:
    """Last-answer-wins over the corpus, filtered to current-vocabulary
    labels (the 15 retired urgent/awaiting-reply verdicts drop out)."""
    by_message: dict[str, dict[str, Any]] = {}
    for line in GROUND_TRUTH.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            by_message[row["message_id"]] = row
    rows = [r for r in by_message.values() if r["true_category"] in TRIAGE_CATEGORIES]
    rows.sort(key=lambda r: r["message_id"])  # deterministic subset selection
    return rows[:limit] if limit else rows


async def fetch_body(connector: Any, message_id: str) -> dict[str, Any] | None:
    """Cached read-only fetch of the message content the classifier sees."""
    cache_file = CACHE_DIR / f"{message_id}.json"
    if cache_file.exists():
        cached: dict[str, Any] = json.loads(cache_file.read_text())
        return cached
    try:
        msg = await connector.get_message(message_id)
    except Exception as exc:
        print(f"  ! fetch failed for {message_id}: {exc}")
        return None
    payload = {
        "from": f"{msg.from_address.name} <{msg.from_address.address}>",
        "subject": msg.subject,
        "body_text": (msg.body_text or "")[:BODY_CAP],
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(payload))
    cache_file.chmod(0o600)
    return payload


def build_prompt(
    rubric: str, payload: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    system = [{"text": f"Prior agent memory:\n\n{rubric}"}]
    user = (
        "You are triaging an inbound email — CLASSIFICATION ONLY.\n"
        f"From: {payload['from']}\nSubject: {payload['subject']}\n\n"
        f"{payload['body_text']}\n\n"
        "Apply the rubric: choose exactly one category and a (usually empty) "
        "attention list. Respond with ONLY a JSON object on one line:\n"
        '{"category": "<one of five>", "attention": [...], '
        '"category_confidence": <0..1>, "summary": "<one sentence>"}'
    )
    return system, [{"role": "user", "content": [{"text": user}]}]


def parse_category(text: str) -> str | None:
    match = re.search(r'"category"\s*:\s*"([a-z-]+)"', text)
    return match.group(1) if match else None


async def run(args: argparse.Namespace) -> int:
    labels = load_labels(args.limit)
    if not labels:
        print("no usable labels found")
        return 2
    rubric = RUBRIC.read_text()
    seed_gmail_env_from_disk(ACCOUNT)
    connector = maybe_build_gmail_connector(account=ACCOUNT, secret_store=EnvSecretStore())
    if connector is None:
        print(f"no Gmail credentials for {ACCOUNT}")
        return 2
    bedrock = BedrockClient()

    print(f"dataset: {len(labels)} labeled messages (current-vocabulary subset)")
    results: list[dict[str, Any]] = []
    for model_id in args.models:
        correct = 0
        scored = 0
        confusion: Counter[tuple[str, str]] = Counter()
        usage_in = usage_out = 0
        cost = 0.0
        latencies: list[float] = []
        for row in labels:
            payload = await fetch_body(connector, row["message_id"])
            if payload is None:
                continue
            system, messages = build_prompt(rubric, payload)
            started = time.monotonic()
            try:
                response = await bedrock.converse(
                    model_id=model_id,
                    messages=messages,
                    system=system,
                    inference_config={"maxTokens": 300},
                )
            except Exception as exc:
                print(f"  ! {model_id}: converse failed on {row['message_id']}: {exc}")
                continue
            latencies.append(time.monotonic() - started)
            usage = response.get("usage", {})
            usage_in += usage.get("inputTokens", 0)
            usage_out += usage.get("outputTokens", 0)
            cost += cost_for_usage(
                {
                    "input_tokens": usage.get("inputTokens", 0),
                    "output_tokens": usage.get("outputTokens", 0),
                },
                model_id,
            )
            text = "".join(
                block.get("text", "")
                for block in response.get("output", {}).get("message", {}).get("content", [])
            )
            predicted = parse_category(text)
            scored += 1
            if predicted == row["true_category"]:
                correct += 1
            else:
                confusion[(row["true_category"], str(predicted))] += 1

        accuracy = correct / scored if scored else 0.0
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        per_msg = cost / scored if scored else 0.0
        record = {
            "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": model_id,
            "messages": scored,
            "accuracy": round(accuracy, 4),
            "input_tokens": usage_in,
            "output_tokens": usage_out,
            "cost_usd": round(cost, 4),
            "cost_per_msg": round(per_msg, 6),
            "avg_latency_s": round(avg_latency, 2),
            "top_confusions": [
                {"true": t, "predicted": p, "count": n} for (t, p), n in confusion.most_common(5)
            ],
        }
        results.append(record)
        with RESULTS.open("a") as f:
            f.write(json.dumps(record) + "\n")
        print(
            f"\n{model_id}\n  accuracy {accuracy:.1%} on {scored} msgs | "
            f"${cost:.4f} total (${per_msg:.5f}/msg) | {avg_latency:.1f}s avg"
        )
        for (true, predicted), n in confusion.most_common(3):
            print(f"    miss: {true} -> {predicted} x{n}")

    if len(results) > 1:
        print("\n=== price/quality ===")
        for r in sorted(results, key=lambda r: -r["accuracy"]):
            points_per_dollar = r["accuracy"] / r["cost_per_msg"] if r["cost_per_msg"] else 0
            print(
                f"  {r['model'].split('.')[-1][:40]:42s} "
                f"{r['accuracy']:.1%}  ${r['cost_per_msg']:.5f}/msg  "
                f"(accuracy-per-$/msg: {points_per_dollar:,.0f})"
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        nargs="+",
        default=["us.anthropic.claude-haiku-4-5-20251001-v1:0"],
    )
    parser.add_argument("--limit", type=int, default=None, help="messages per model")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
