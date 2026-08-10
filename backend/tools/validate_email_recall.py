"""Persistent/sequential validation of veracium per-entity recall on email triage.

Implements docs/EMAIL_RECALL_VALIDATION_PLAN.md: does per-entity recall reduce the
same-sender classification split rate WITHOUT sacrificing correctness?

Two within-subjects arms over the SAME messages in ascending received_at order,
each on its OWN persistent SCRATCH learned-memory store (never the production
store), with the SAME observe path so the only delta is recall INJECTION:

  OFF  recall block stripped  -> observes, but nothing injected (baseline)
  ON   recall block intact    -> observes AND injects prior sender context

Reports: split_rate per arm (+ delta), the anchoring/change-direction analysis
(does ON pull toward the sender's FIRST label?), recall hit rate, and an optional
model-noise floor (re-run K messages statelessly, measure category flips) that ON
must beat to be meaningful. NO raw mail content in the output.

Usage (from backend/):
    BEDROCK_MODE=live uv run python tools/validate_email_recall.py \
        --account qspencer@gmail.com --repeat-only --noise-floor-k 5
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "email_triage"
LIVE_WF = REPO_ROOT / "examples" / "email_triage_live"


def _sender(trigger: dict[str, Any]) -> str:
    fa = trigger.get("from_address") or {}
    return str((fa.get("address") if isinstance(fa, dict) else fa) or "?").lower()


def _received_at(trigger: dict[str, Any]) -> str:
    return str(trigger.get("received_at") or "")


async def _run_arm(
    arm: str,
    fixtures: list[Path],
    *,
    deps: dict[str, Any],
    inject_recall: bool,
    scratch_db: Path,
) -> list[dict[str, Any]]:
    """Run every message SEQUENTIALLY through one arm against a fresh persistent
    scratch store. Returns one record per message."""
    from workflow_platform.engine import WorkflowEngine
    from workflow_platform.memory.learned import LearnedMemoryService
    from workflow_platform.persistence import in_memory_repositories
    from workflow_platform.world import real_world

    definition = deps["definition"]
    if not inject_recall:
        # Strip ONLY the recall block — observations stay, so both arms populate
        # the store identically and the isolated variable is injection.
        lm = definition.learned_memory
        definition = definition.model_copy(
            update={"learned_memory": lm.model_copy(update={"recall": None})}
        )

    learned = LearnedMemoryService(deps["bedrock"], db_path=scratch_db)
    records: list[dict[str, Any]] = []
    first_label: dict[str, str] = {}

    for i, path in enumerate(fixtures, 1):
        trigger = deps["trim"](json.loads(path.read_text()))
        sender = _sender(trigger)
        # fresh engine per run (engines aren't reused), SHARED persistent store
        engine = WorkflowEngine(
            repositories=in_memory_repositories(),
            functions=deps["functions"](),
            tools=deps["tools"],
            bedrock=deps["bedrock"],
            world=real_world(),
            memory=deps["memory"],
            learned_memory=learned,
        )
        try:
            inst = await engine.run(definition, trigger_payload=trigger)
            ctx = inst.context or {}
            steps = ctx.get("steps", {}) if isinstance(ctx, dict) else {}
            record_out = steps.get("record", {}) or {}
            triage_out = steps.get("triage", {}) or {}
            cat = record_out.get("category")
            if cat is None:
                cat = "<unparsed>" if record_out.get("parse_ok") is False else "<failed>"
            recalled = triage_out.get("recall")
            injected = bool(recalled) and recalled not in ("", None)
        except Exception as exc:
            cat, injected = f"<error:{type(exc).__name__}>", False
        first_label.setdefault(sender, cat)
        records.append(
            {
                "arm": arm,
                "seq": i,
                "sender": sender,
                "received_at": _received_at(trigger),
                "subject": (trigger.get("subject") or "")[:60],
                "category": cat,
                "recall_injected": injected,
                "sender_first_label": first_label[sender],
            }
        )
        print(
            f"  [{arm}] {i}/{len(fixtures)} {sender[:28]:30} -> {cat}"
            f"{'  (recall)' if injected else ''}"
        )
    return records


def _split_rate(records: list[dict[str, Any]]) -> tuple[float, int, int, list[str]]:
    by_sender: dict[str, list[str]] = collections.defaultdict(list)
    for r in records:
        by_sender[r["sender"]].append(r["category"])
    repeats = {s: c for s, c in by_sender.items() if len(c) >= 2}
    split = [s for s, c in repeats.items() if len(set(c)) > 1]
    rate = len(split) / len(repeats) if repeats else 0.0
    return rate, len(split), len(repeats), split


async def _noise_floor(fixtures: list[Path], *, deps: dict[str, Any], k: int) -> dict[str, Any]:
    """Re-run a sample of messages K times STATELESSLY (fresh store, no recall) to
    measure model non-determinism — the floor ON must beat."""
    from workflow_platform.engine import WorkflowEngine
    from workflow_platform.memory.learned import LearnedMemoryService
    from workflow_platform.persistence import in_memory_repositories
    from workflow_platform.world import real_world

    definition = deps["definition"]
    lm = definition.learned_memory
    stateless_def = definition.model_copy(
        update={"learned_memory": lm.model_copy(update={"recall": None})}
    )
    sample = fixtures[: min(len(fixtures), 8)]
    flips = 0
    with tempfile.TemporaryDirectory() as td:
        for path in sample:
            trigger = deps["trim"](json.loads(path.read_text()))
            cats: list[str] = []
            for j in range(k):
                learned = LearnedMemoryService(deps["bedrock"], db_path=Path(td) / f"nf_{j}.db")
                engine = WorkflowEngine(
                    repositories=in_memory_repositories(),
                    functions=deps["functions"](),
                    tools=deps["tools"],
                    bedrock=deps["bedrock"],
                    world=real_world(),
                    memory=deps["memory"],
                    learned_memory=learned,
                )
                try:
                    inst = await engine.run(stateless_def, trigger_payload=trigger)
                    ctx = inst.context or {}
                    c = (ctx.get("steps", {}).get("record", {}) or {}).get("category") or "<x>"
                except Exception:
                    c = "<error>"
                cats.append(c)
            if len(set(cats)) > 1:
                flips += 1
            print(f"  [noise] {_sender(trigger)[:28]:30} {cats}")
    return {
        "sampled": len(sample),
        "k": k,
        "messages_that_flipped": flips,
        "flip_rate": flips / len(sample) if sample else 0.0,
    }


async def _main(account: str, limit: int | None, repeat_only: bool, noise_k: int) -> int:
    os.environ["WORKFLOW_PLATFORM_GMAIL_ACCOUNT"] = account
    from workflow_platform.bedrock import BedrockClient, BedrockMode
    from workflow_platform.engine import ToolCatalog
    from workflow_platform.engine.functions import default_function_registry
    from workflow_platform.memory import MemoryManager
    from workflow_platform.workflow import load_definition_from_yaml

    fixtures = sorted((DATA_DIR / account).glob("*.json"))
    if not fixtures:
        print(f"No fixtures in {DATA_DIR / account}. Run fetch_gmail_inbox.py first.")
        return 1
    # sort by received_at (online protocol); optionally drop singletons for speed
    parsed = [(p, json.loads(p.read_text())) for p in fixtures]
    parsed.sort(key=lambda pt: _received_at(pt[1]))
    if repeat_only:
        counts = collections.Counter(_sender(t) for _, t in parsed)
        parsed = [(p, t) for p, t in parsed if counts[_sender(t)] >= 2]
    if limit:
        parsed = parsed[:limit]
    ordered = [p for p, _ in parsed]
    print(
        f"{len(ordered)} messages ({'repeat-senders only' if repeat_only else 'all'}), "
        f"received_at order.\n"
    )

    definition = load_definition_from_yaml((LIVE_WF / "workflow.yaml").read_text())
    if definition.learned_memory is None or definition.learned_memory.recall is None:
        print("ERROR: the workflow has no learned_memory.recall block to validate.")
        return 1
    # R1 #2: pin temperature 0 to collapse model non-determinism (the cleanest
    # attack on the noise problem). maxTokens bounds the classification output.
    for step in definition.steps:
        if step.type == "agentic":
            ic = dict(step.policy.inference_config or {})
            ic.setdefault("temperature", 0)
            ic.setdefault("maxTokens", 1024)
            step.policy.inference_config = ic
    memory = MemoryManager(REPO_ROOT / ".memory")
    memory_text = (LIVE_WF / "agent_memory.md").read_text()
    for step in definition.steps:
        if step.type == "agentic":
            await memory.write_raw(f"steps/{definition.id}/{step.id}", memory_text)

    def _trim(trigger: dict[str, Any]) -> dict[str, Any]:
        t = dict(trigger)
        t["body_html"] = None
        if isinstance(t.get("body_text"), str) and len(t["body_text"]) > 4000:
            t["body_text"] = t["body_text"][:4000] + "\n[truncated]"
        return t

    deps = {
        "definition": definition,
        "bedrock": BedrockClient(mode=BedrockMode(os.environ.get("BEDROCK_MODE", "live"))),
        "tools": ToolCatalog([]),  # read-only live workflow: tools:[]
        "functions": default_function_registry,
        "memory": memory,
        "trim": _trim,
    }

    results: dict[str, Any] = {"account": account, "n_messages": len(ordered)}
    with tempfile.TemporaryDirectory() as td:
        for arm, inject in (("off", False), ("on", True)):
            print(f"=== arm: {arm} (recall {'ON' if inject else 'OFF'}) ===")
            recs = await _run_arm(
                arm, ordered, deps=deps, inject_recall=inject, scratch_db=Path(td) / f"{arm}.db"
            )
            rate, nsplit, nrep, split = _split_rate(recs)
            results[arm] = {
                "records": recs,
                "split_rate": rate,
                "split_senders": split,
                "n_repeat_senders": nrep,
                "recall_hit_rate": sum(r["recall_injected"] for r in recs) / len(recs),
            }
            print(f"  -> split_rate={rate:.3f} ({nsplit}/{nrep} repeat senders)\n")

    # change-direction / anchoring: per-message ON vs OFF, relative to ON's first-seen label
    off_by = {(r["sender"], r["seq"]): r for r in results["off"]["records"]}
    changed = toward_first = away_first = 0
    for r in results["on"]["records"]:
        o = off_by.get((r["sender"], r["seq"]))
        if o and r["category"] != o["category"]:
            changed += 1
            if r["category"] == r["sender_first_label"]:
                toward_first += 1
            else:
                away_first += 1
    results["anchoring"] = {
        "on_changed_vs_off": changed,
        "toward_sender_first_label": toward_first,
        "away_from_first_label": away_first,
        "note": "high toward_first with no away = recall acting as "
        "first-label anchoring, NOT correction",
    }
    if noise_k:
        print("=== noise floor (stateless re-runs) ===")
        results["noise_floor"] = await _noise_floor(ordered, deps=deps, k=noise_k)

    out = DATA_DIR / f"{account}_recall_validation.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print("\n" + "=" * 60)
    print(
        f"split_rate  OFF={results['off']['split_rate']:.3f}  "
        f"ON={results['on']['split_rate']:.3f}  "
        f"delta={results['off']['split_rate'] - results['on']['split_rate']:+.3f}"
    )
    print(f"recall hit rate (ON): {results['on']['recall_hit_rate']:.2f}")
    print(f"ON changed vs OFF: {changed}  (toward first-label {toward_first} / away {away_first})")
    if noise_k:
        print(f"noise floor (stateless flip rate): {results['noise_floor']['flip_rate']:.3f}")
    print("\n⚠️  EXPLORATORY RESULT — NOT a verdict on recall (EMAIL_RECALL_VALIDATION_PLAN R1).")
    print(
        "    A verdict requires: operator labels on the anchor + changed sets (correctness"
        " guard), a simulated split-RATE noise floor, and a power/MDE check at this N."
    )
    print("    Consistency alone cannot distinguish good anchoring from propagated error.")
    print(f"Full JSON: {out}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--account", required=True)
    p.add_argument("--limit", type=int)
    p.add_argument(
        "--repeat-only",
        action="store_true",
        help="skip single-message senders (the only informative rows)",
    )
    p.add_argument(
        "--noise-floor-k",
        type=int,
        default=0,
        help="re-run a sample K times statelessly to measure model non-determinism",
    )
    args = p.parse_args()
    raise SystemExit(
        asyncio.run(_main(args.account, args.limit, args.repeat_only, args.noise_floor_k))
    )


if __name__ == "__main__":
    main()
