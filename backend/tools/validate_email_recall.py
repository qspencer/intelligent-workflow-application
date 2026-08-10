"""Persistent/sequential validation of veracium per-entity recall on email triage.

Implements docs/EMAIL_RECALL_VALIDATION_PLAN.md (design + R1 review). Question:
does per-entity recall reduce the same-sender classification split rate WITHOUT
sacrificing correctness?

Two within-subjects arms over the SAME messages in ascending received_at order,
each on its OWN persistent SCRATCH learned-memory store (never the production
store), with the SAME observe path so the only delta is recall INJECTION:

  OFF  recall block stripped  -> observes, but nothing injected (baseline)
  ON   recall block intact    -> observes AND injects prior sender context

This tool runs the **exploratory** consistency pass only. A decision-grade VERDICT
(R1) additionally needs operator labels + an accuracy guard + K-repeat majority
vote + a paired McNemar + an MDE/power check + corpus freeze — none of which are
implemented here, so `--verdict` FAILS CLOSED rather than emitting a false verdict.
Consistency alone cannot tell good anchoring from a propagated first-error.

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

# Preregistered abort rule (R1 #7 / review finding 4): if more than this fraction
# of runs FAIL (engine error / no output), the arm is untrustworthy — abort rather
# than let failures masquerade as classifications.
MAX_FAILURE_RATE = 0.05


def _sender(trigger: dict[str, Any]) -> str:
    fa = trigger.get("from_address") or {}
    return str((fa.get("address") if isinstance(fa, dict) else fa) or "?").lower()


def _received_at(trigger: dict[str, Any]) -> str:
    return str(trigger.get("received_at") or "")


def _classify(inst: Any) -> tuple[str | None, str, bool]:
    """-> (category | None, status, recall_injected). status is 'ok' | 'failed'.
    A failed run has NO category — it is NOT a classification and must never enter
    the split-rate/change/hit-rate math (review finding 4)."""
    ctx = getattr(inst, "context", None) or {}
    steps = ctx.get("steps", {}) if isinstance(ctx, dict) else {}
    record_out = steps.get("record", {}) or {}
    triage_out = steps.get("triage", {}) or {}
    cat = record_out.get("category")
    if cat is None:
        # unparsed OR missing output — a failed run, not a category.
        return None, "failed", False
    # `recall` is {query, context_hash, edges, episodes} | None. Injected means the
    # store actually returned something — edges/episodes > 0, not merely non-None
    # (review finding 3).
    recalled = triage_out.get("recall")
    injected = bool(
        isinstance(recalled, dict) and (recalled.get("edges", 0) or recalled.get("episodes", 0))
    )
    return str(cat), "ok", injected


async def _run_one(definition: Any, trigger: dict[str, Any], learned: Any, deps: dict[str, Any]):
    from workflow_platform.engine import WorkflowEngine
    from workflow_platform.persistence import in_memory_repositories
    from workflow_platform.world import real_world

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
        return _classify(inst)
    except Exception as exc:
        return None, f"failed:{type(exc).__name__}", False


def _strip_recall(definition: Any) -> Any:
    lm = definition.learned_memory
    return definition.model_copy(update={"learned_memory": lm.model_copy(update={"recall": None})})


async def _run_arm(
    arm: str, fixtures: list[Path], *, deps: dict[str, Any], inject_recall: bool, scratch_db: Path
) -> list[dict[str, Any]]:
    """Every message SEQUENTIALLY through one arm against a fresh persistent scratch
    store. Both arms observe (populate the store) identically; only injection differs."""
    from workflow_platform.memory.learned import LearnedMemoryService

    definition = deps["definition"] if inject_recall else _strip_recall(deps["definition"])
    learned = LearnedMemoryService(deps["bedrock"], db_path=scratch_db)
    records: list[dict[str, Any]] = []
    seen: dict[str, int] = collections.Counter()
    for i, path in enumerate(fixtures, 1):
        trigger = deps["trim"](json.loads(path.read_text()))
        sender = _sender(trigger)
        cat, status, injected = await _run_one(definition, trigger, learned, deps)
        seen[sender] += 1
        records.append(
            {
                "arm": arm,
                "seq": i,
                "sender": sender,
                "received_at": _received_at(trigger),
                "subject": (trigger.get("subject") or "")[:60],
                "category": cat,
                "status": status,
                "recall_injected": injected,
                "recall_eligible": seen[sender] >= 2,  # 2nd+ from this sender
            }
        )
        tag = cat if status == "ok" else status.upper()
        print(
            f"  [{arm}] {i}/{len(fixtures)} {sender[:26]:28} -> {tag}"
            f"{'  (recall)' if injected else ''}"
        )
    return records


def _ok(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r["status"] == "ok"]


def _split_rate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Split rate over OK-only records. Returns rate=None when there are no repeat
    senders (undefined, not 0.0) — review finding 6."""
    by: dict[str, list[str]] = collections.defaultdict(list)
    for r in _ok(records):
        by[r["sender"]].append(r["category"])
    repeats = {s: c for s, c in by.items() if len(c) >= 2}
    split = sorted(s for s, c in repeats.items() if len(set(c)) > 1)
    return {
        "split_rate": (len(split) / len(repeats)) if repeats else None,
        "n_repeat_senders": len(repeats),
        "n_split": len(split),
        "split_senders": split,
        "sender_sizes": sorted((len(c) for c in repeats.values()), reverse=True),
    }


def _recall_hit_rate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Over recall-ELIGIBLE (2nd+), OK records only (review finding 3)."""
    eligible = [r for r in _ok(records) if r["recall_eligible"]]
    hits = sum(1 for r in eligible if r["recall_injected"])
    return {
        "eligible": len(eligible),
        "hits": hits,
        "rate": (hits / len(eligible)) if eligible else None,
    }


def _simulated_split_floor(sender_sizes: list[int], p: float) -> float:
    """R1 #3 / review finding 2: translate a per-message flip rate p into an
    EXPECTED SPLIT-RATE floor over the actual sender-size distribution. A consistent
    sender of m messages splits from noise alone with prob 1-(1-p)^m."""
    if not sender_sizes:
        return 0.0
    return sum(1 - (1 - p) ** m for m in sender_sizes) / len(sender_sizes)


async def _noise_floor(
    fixtures: list[Path], *, deps: dict[str, Any], k: int, sender_sizes: list[int]
) -> dict[str, Any]:
    """Measure per-message flip rate p on REPEAT-SENDER messages (heterogeneous;
    clear messages don't flip), then simulate the split-rate floor over the real
    sender-size distribution (review finding 2)."""
    from workflow_platform.memory.learned import LearnedMemoryService

    stateless = _strip_recall(deps["definition"])
    sample = fixtures[: min(len(fixtures), 8)]
    flipped = 0
    with tempfile.TemporaryDirectory() as td:
        for path in sample:
            trigger = deps["trim"](json.loads(path.read_text()))
            cats: list[str | None] = []
            for j in range(k):
                learned = LearnedMemoryService(deps["bedrock"], db_path=Path(td) / f"nf_{j}.db")
                cat, status, _ = await _run_one(stateless, trigger, learned, deps)
                cats.append(cat if status == "ok" else None)
            ok = [c for c in cats if c is not None]
            if len(set(ok)) > 1:
                flipped += 1
            print(f"  [noise] {_sender(trigger)[:26]:28} {cats}")
    p = flipped / len(sample) if sample else 0.0
    return {
        "sampled": len(sample),
        "k": k,
        "per_message_flip_rate": p,
        "simulated_split_rate_floor": _simulated_split_floor(sender_sizes, p),
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
    parsed = [(p, json.loads(p.read_text())) for p in fixtures]
    parsed.sort(key=lambda pt: _received_at(pt[1]))  # online protocol
    if repeat_only:
        counts = collections.Counter(_sender(t) for _, t in parsed)
        parsed = [(p, t) for p, t in parsed if counts[_sender(t)] >= 2]
    if limit:
        parsed = parsed[:limit]
    ordered = [p for p, _ in parsed]
    if not ordered:
        print("No messages to run (empty after filters). Fetch more, or drop --repeat-only.")
        return 1
    print(
        f"{len(ordered)} messages ({'repeat-senders only' if repeat_only else 'all'}), "
        f"received_at order.\n"
    )

    definition = load_definition_from_yaml((LIVE_WF / "workflow.yaml").read_text())
    if definition.learned_memory is None or definition.learned_memory.recall is None:
        print("ERROR: the workflow has no learned_memory.recall block to validate.")
        return 1
    # R1 #2 / review finding 5: pin temperature 0 UNCONDITIONALLY (+ assert), and
    # bound output tokens. setdefault would preserve a nonzero workflow setting.
    for step in definition.steps:
        if step.type == "agentic":
            ic = dict(step.policy.inference_config or {})
            ic["temperature"] = 0
            ic.setdefault("maxTokens", 1024)
            step.policy.inference_config = ic
            assert step.policy.inference_config["temperature"] == 0

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

    results: dict[str, Any] = {"account": account, "n_messages": len(ordered), "exploratory": True}
    with tempfile.TemporaryDirectory() as td:
        for arm, inject in (("off", False), ("on", True)):
            print(f"=== arm: {arm} (recall {'ON' if inject else 'OFF'}) ===")
            recs = await _run_arm(
                arm, ordered, deps=deps, inject_recall=inject, scratch_db=Path(td) / f"{arm}.db"
            )
            fails = [r for r in recs if r["status"] != "ok"]
            fr = len(fails) / len(recs)
            sr = _split_rate(recs)
            results[arm] = {
                "records": recs,
                **sr,
                "failure_rate": fr,
                "recall_hit": _recall_hit_rate(recs),
            }
            print(
                f"  -> split_rate={sr['split_rate']}  ({sr['n_split']}/{sr['n_repeat_senders']} "
                f"repeat senders)  failures={len(fails)}\n"
            )
            if fr > MAX_FAILURE_RATE:
                print(
                    f"ABORT: {arm} failure rate {fr:.1%} > {MAX_FAILURE_RATE:.0%} — results "
                    "untrustworthy (failures would masquerade as classifications). "
                    "Fix the run and retry."
                )
                return 2

    # anchoring / change-direction — OK-only, both arms OK for the same message
    off_by = {(r["sender"], r["seq"]): r for r in _ok(results["off"]["records"])}
    changed = toward_first = away_first = 0
    first_on: dict[str, str] = {}
    for r in sorted(_ok(results["on"]["records"]), key=lambda x: x["seq"]):
        first_on.setdefault(r["sender"], r["category"])
        o = off_by.get((r["sender"], r["seq"]))
        if o and r["category"] != o["category"]:
            changed += 1
            if r["category"] == first_on[r["sender"]]:
                toward_first += 1
            else:
                away_first += 1
    results["anchoring"] = {
        "on_changed_vs_off": changed,
        "toward_sender_first_label": toward_first,
        "away_from_first_label": away_first,
        "note": "NON-DIAGNOSTIC without labels (R1 #1): toward-first cannot "
        "distinguish good anchoring from propagated first-error.",
    }
    if noise_k:
        print("=== noise floor (stateless K-repeat) ===")
        results["noise_floor"] = await _noise_floor(
            ordered, deps=deps, k=noise_k, sender_sizes=results["off"]["sender_sizes"]
        )

    out = DATA_DIR / f"{account}_recall_validation.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print("\n" + "=" * 64)
    print(f"split_rate  OFF={results['off']['split_rate']}  ON={results['on']['split_rate']}")
    print(f"recall hit rate (ON, 2nd+ msgs): {results['on']['recall_hit']['rate']}")
    if noise_k:
        nf = results["noise_floor"]
        print(
            f"noise: per-msg flip={nf['per_message_flip_rate']:.3f} -> simulated split-rate "
            f"floor={nf['simulated_split_rate_floor']:.3f} (ON must beat THIS, not 0)"
        )
    print("\n⚠️  EXPLORATORY — NOT a verdict on recall (EMAIL_RECALL_VALIDATION_PLAN R1). A verdict")
    print("    needs operator labels (accuracy guard), K-repeat majority vote, McNemar, and an MDE")
    print("    check — not implemented here. Consistency alone can't separate good anchoring from")
    print(
        "    propagated error. `--verdict` is intentionally fail-closed until that machinery lands."
    )
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
        help="stateless re-runs per sampled message (>=2) to estimate the split-rate floor",
    )
    p.add_argument(
        "--verdict",
        action="store_true",
        help="request a decision-grade verdict (currently FAILS CLOSED — see R1)",
    )
    args = p.parse_args()
    if args.verdict:
        print(
            "FAIL-CLOSED: a decision-grade verdict requires the mandatory evidence (operator "
            "labels + accuracy guard + K-repeat majority vote + McNemar + MDE/power + corpus "
            "freeze), none of which are implemented. This tool runs the EXPLORATORY pass only. "
            "See docs/EMAIL_RECALL_VALIDATION_PLAN.md R1. Re-run without --verdict for the "
            "exploratory result."
        )
        raise SystemExit(3)
    if args.noise_floor_k and args.noise_floor_k < 2:
        print("--noise-floor-k must be >= 2 (a single re-run cannot show a flip).")
        raise SystemExit(2)
    raise SystemExit(
        asyncio.run(_main(args.account, args.limit, args.repeat_only, args.noise_floor_k))
    )


if __name__ == "__main__":
    main()
