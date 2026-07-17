#!/usr/bin/env python3
"""Interactive ground-truth review of email-triage verdicts.

Walks every triaged message (latest verdict per message_id — forks
supersede), shows how the agent categorized it, and asks you to mark it
correct or incorrect (picking the true category). Labels append to a JSONL
file as you go, so you can quit anytime and resume later — already-labeled
messages are skipped.

If a judge report (tools/judge_email_triage.py) is found, agent-vs-judge
disagreements are shown FIRST — they're the highest-value adjudications —
and the judge's blind second opinion is displayed alongside.

The labels file contains personal-mail metadata: it lives under .memory/
(gitignored) by default and must never be committed.

Keys:
  Enter / c  mark the agent's category CORRECT
  i          mark INCORRECT → then pick the true category (1-5)
  b          show the full body, then re-prompt
  s          skip (decide later; stays unlabeled)
  q          quit (progress is already saved)

Usage:
    DATABASE_URL=postgresql+asyncpg://... uv run python tools/review_triage.py
    uv run python tools/review_triage.py --summary   # stats from labels so far
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.engine.functions import TRIAGE_CATEGORIES
from workflow_platform.persistence.db import make_engine, make_session_factory
from workflow_platform.persistence.postgres import postgres_repositories

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = BACKEND_DIR / ".memory" / "triage-ground-truth.jsonl"
DEFAULT_JUDGE_REPORT = Path("/tmp/email-triage-judge-report.json")
CATEGORIES = TRIAGE_CATEGORIES

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _load_labels(path: Path) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                entry = json.loads(line)
                labels[entry["message_id"]] = entry
    return labels


def _append_label(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _judge_verdicts(path: Path) -> dict[str, dict[str, Any]]:
    """message_id -> judge fields from a judge_email_triage.py report."""
    if not path.exists():
        return {}
    try:
        items = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {i["message_id"]: i for i in items if isinstance(i, dict) and "judge_category" in i}


async def _load_items(workflow: str) -> list[dict[str, Any]]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set — labels review reads from Postgres.")
    db_engine = make_engine(url)
    repos = postgres_repositories(make_session_factory(db_engine))
    try:
        instances = await repos.instances.list_by_workflow(workflow)
        instances.sort(key=lambda i: i.started_at or i.created_at)
        by_msg: dict[str, dict[str, Any]] = {}
        for inst in instances:
            steps = await repos.steps.list_by_instance(inst.id)
            record = next(
                (s for s in steps if s.step_id == "record" and (s.output or {}).get("parse_ok")),
                None,
            )
            if record is None or record.output is None:
                continue
            trig = inst.trigger_payload
            mid = str(trig.get("message_id") or inst.id)
            by_msg[mid] = {
                "message_id": mid,
                "received_at": str(trig.get("received_at") or "")[:16],
                "sender_name": (trig.get("from_address") or {}).get("name") or "",
                "sender": (trig.get("from_address") or {}).get("address") or "?",
                "subject": str(trig.get("subject") or ""),
                "body": str(trig.get("body_text") or ""),
                "agent_category": record.output.get("category"),
                "agent_confidence": record.output.get("confidence"),
                "agent_summary": record.output.get("summary"),
            }
        return list(by_msg.values())
    finally:
        if hasattr(db_engine, "dispose"):
            await db_engine.dispose()


def _show_card(item: dict[str, Any], pos: int, total: int) -> None:
    print(f"\n{DIM}{'─' * 72}{RESET}")
    print(f"{DIM}[{pos}/{total}]{RESET}  {item['received_at']}")
    name = f"{item['sender_name']} " if item["sender_name"] else ""
    print(f"{BOLD}From   :{RESET} {name}<{item['sender']}>")
    print(f"{BOLD}Subject:{RESET} {item['subject']}")
    preview = " ".join(item["body"].split())
    if len(preview) > 300:
        preview = preview[:300] + " …"
    print(f"{DIM}{preview}{RESET}")
    print(
        f"\n{BOLD}Agent  :{RESET} {CYAN}{item['agent_category']}{RESET}"
        f" (confidence {item['agent_confidence']})  {DIM}{item['agent_summary']}{RESET}"
    )
    judge = item.get("judge")
    if judge:
        jc = judge["judge_category"]
        color = GREEN if jc == item["agent_category"] else YELLOW
        print(
            f"{BOLD}Judge  :{RESET} {color}{jc}{RESET}"
            f"  {DIM}{str(judge.get('judge_reasoning'))[:100]}{RESET}"
        )


def _pick_category(current: str) -> str | None:
    print("True category:")
    for n, cat in enumerate(CATEGORIES, 1):
        marker = f" {DIM}(agent's choice){RESET}" if cat == current else ""
        print(f"  {n}. {cat}{marker}")
    while True:
        choice = input(f"1-{len(CATEGORIES)} (or x to cancel): ").strip().lower()
        if choice == "x":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(CATEGORIES):
            return CATEGORIES[int(choice) - 1]


def _print_summary(labels: dict[str, dict[str, Any]]) -> None:
    if not labels:
        print("No labels yet.")
        return
    entries = list(labels.values())
    correct = [e for e in entries if e["verdict"] == "correct"]
    incorrect = [e for e in entries if e["verdict"] == "incorrect"]
    print(f"\n{BOLD}=== ground-truth summary ==={RESET}")
    print(f"labeled  : {len(entries)}")
    print(f"correct  : {len(correct)}  ({100 * len(correct) / len(entries):.1f}% accuracy)")
    print(f"incorrect: {len(incorrect)}")
    if incorrect:
        print(f"\n{BOLD}corrections (agent -> truth):{RESET}")
        pairs = Counter((e["agent_category"], e["true_category"]) for e in incorrect)
        for (a, t), n in pairs.most_common():
            print(f"  {a:>15} -> {t:<15} {n}")
        print(f"\n{BOLD}corrected messages:{RESET}")
        for e in incorrect:
            print(f"  {e['agent_category']} -> {e['true_category']}  {e['subject'][:55]!r}")


async def run(args: argparse.Namespace) -> int:
    labels_path = Path(args.labels)
    labels = _load_labels(labels_path)

    if args.summary:
        _print_summary(labels)
        return 0

    items = await _load_items(args.workflow)
    judges = _judge_verdicts(Path(args.judge_report))
    for item in items:
        item["judge"] = judges.get(item["message_id"])

    revisit_cats = {c.strip() for c in args.revisit.split(",") if c.strip()}
    pending = [
        i
        for i in items
        if i["message_id"] not in labels
        or (revisit_cats and labels[i["message_id"]].get("true_category") in revisit_cats)
    ]
    # Highest-value first: judge disagreements, then the rest chronologically.
    pending.sort(
        key=lambda i: (
            not (i["judge"] and i["judge"]["judge_category"] != i["agent_category"]),
            i["received_at"],
        )
    )
    disagreements = sum(
        1 for i in pending if i["judge"] and i["judge"]["judge_category"] != i["agent_category"]
    )
    print(f"{BOLD}Email triage — ground-truth review{RESET}")
    print(f"labeled so far: {len(labels)}   remaining: {len(pending)}")
    if disagreements:
        print(f"{YELLOW}{disagreements} judge disagreement(s) queued first.{RESET}")
    print(f"labels file: {labels_path}")

    total = len(items)
    for item in pending:
        _show_card(item, len(labels) + 1, total)
        while True:
            answer = (
                input(f"[{GREEN}Enter=correct{RESET} / i=incorrect / b=body / s=skip / q=quit] ")
                .strip()
                .lower()
            )
            if answer in ("", "c"):
                verdict, true_cat = "correct", item["agent_category"]
            elif answer == "i":
                picked = _pick_category(item["agent_category"])
                if picked is None:
                    continue
                verdict, true_cat = "incorrect", picked
            elif answer == "b":
                print(f"\n{item['body']}\n")
                continue
            elif answer == "s":
                verdict = ""
            elif answer == "q":
                _print_summary(labels)
                return 0
            else:
                continue
            break
        if not verdict:
            continue
        entry = {
            "message_id": item["message_id"],
            "sender": item["sender"],
            "subject": item["subject"],
            "agent_category": item["agent_category"],
            "verdict": verdict,
            "true_category": true_cat,
            "labeled_at": datetime.now(UTC).isoformat(),
        }
        _append_label(labels_path, entry)
        labels[item["message_id"]] = entry
        mark = f"{GREEN}✓{RESET}" if verdict == "correct" else f"{RED}✗ -> {true_cat}{RESET}"
        print(f"  {mark}")

    print(f"\n{GREEN}All messages labeled.{RESET}")
    _print_summary(labels)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", default="email-triage-live")
    parser.add_argument("--labels", default=str(DEFAULT_LABELS))
    parser.add_argument(
        "--judge-report",
        default=str(DEFAULT_JUDGE_REPORT),
        help="judge_email_triage.py report; when present, disagreements are queued first",
    )
    parser.add_argument(
        "--revisit",
        default="",
        help="comma-separated true_category values to re-queue for re-labeling "
        "(e.g. 'fyi,spam' after the 2026-07-19 taxonomy split); their prior "
        "labels are superseded by the new answer (append-only file, last wins)",
    )
    parser.add_argument("--summary", action="store_true", help="print stats and exit")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
