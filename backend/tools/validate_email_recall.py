"""Persistent/sequential validation of veracium per-entity recall on email triage.

Implements docs/EMAIL_RECALL_VALIDATION_PLAN.md (design + R1 review). Question:
does per-entity recall reduce the same-sender classification split rate WITHOUT
sacrificing correctness?

Two within-subjects arms over the SAME messages in ascending received_at order,
each on its OWN persistent SCRATCH learned-memory store (never the production
store), with the SAME observe path so the only delta is recall INJECTION:

  OFF  recall block stripped  -> observes, but nothing injected (baseline)
  ON   recall block intact    -> observes AND injects prior sender context

Default (no --verdict) is the EXPLORATORY consistency pass. `--verdict` runs the
LABELED path: K-repeat majority vote, accuracy(ON) vs accuracy(OFF) vs operator
labels, and the concrete mixed-sender collapse check — but it reports the
AGGREGATE consistency delta as UNDERPOWERED (the paired test needs >=5 senders
flipping the same way; at N~17 only ~2-4 can differ). Consistency alone cannot
tell good anchoring from a propagated first-error, which is why the labeled
accuracy guard and the mixed-sender check carry the interpretation.

Usage (from backend/):
    BEDROCK_MODE=live uv run python tools/validate_email_recall.py \
        --account qspencer@gmail.com --verdict --k 3      # labeled verdict
    BEDROCK_MODE=live uv run python tools/validate_email_recall.py \
        --account qspencer@gmail.com --repeat-only --noise-floor-k 5   # exploratory
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


async def _build_deps(account: str) -> tuple[dict[str, Any], list[tuple[Path, dict]]]:
    os.environ["WORKFLOW_PLATFORM_GMAIL_ACCOUNT"] = account
    from workflow_platform.bedrock import BedrockClient, BedrockMode
    from workflow_platform.engine import ToolCatalog
    from workflow_platform.engine.functions import default_function_registry
    from workflow_platform.memory import MemoryManager
    from workflow_platform.workflow import load_definition_from_yaml

    parsed = [(p, json.loads(p.read_text())) for p in sorted((DATA_DIR / account).glob("*.json"))]
    parsed.sort(key=lambda pt: _received_at(pt[1]))
    definition = load_definition_from_yaml((LIVE_WF / "workflow.yaml").read_text())
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
        "tools": ToolCatalog([]),
        "functions": default_function_registry,
        "memory": memory,
        "trim": _trim,
    }
    return deps, parsed


async def _run_verdict(account: str, k: int) -> int:
    labels = _load_labels(account)
    if not labels:
        print("No labels found. Label the crux set first (tools/label_email_recall.py).")
        return 3
    deps, parsed = await _build_deps(account)
    # repeat senders only (the informative rows); keep received_at order
    counts = collections.Counter(_sender(t) for _, t in parsed)
    repeat = [(p, t) for p, t in parsed if counts[_sender(t)] >= 2]
    # ground truth per sender + which senders are genuinely MIXED
    truth_by_sender: dict[str, set[str]] = collections.defaultdict(set)
    for _, t in repeat:
        lab = labels.get(t["message_id"])
        if lab:
            truth_by_sender[_sender(t)].add(lab)
    mixed_senders = {s for s, v in truth_by_sender.items() if len(v) > 1}

    print(f"VERDICT run: {len(repeat)} repeat-sender messages, K={k} passes/arm, temperature 0.")
    print(
        f"  {len(truth_by_sender)} repeat senders labeled | genuinely MIXED: "
        f"{sorted(mixed_senders)}\n"
    )

    with tempfile.TemporaryDirectory() as tdname:
        td = Path(tdname)
        maj = {}
        for arm, inject in (("off", False), ("on", True)):
            print(f"=== arm {arm} (recall {'ON' if inject else 'OFF'}) ===")
            maj[arm] = await _run_arm_k(arm, repeat, deps=deps, inject=inject, k=k, td=td)

    # ---- metrics over MAJORITY labels ----
    def _split_by_arm(arm: str) -> tuple[float | None, list[str]]:
        by: dict[str, list[str]] = collections.defaultdict(list)
        for _, t in repeat:
            c = maj[arm].get(t["message_id"], "<fail>")
            if c not in ("<fail>", "<tie>"):
                by[_sender(t)].append(c)
        reps = {s: c for s, c in by.items() if len(c) >= 2}
        split = sorted(s for s, c in reps.items() if len(set(c)) > 1)
        return (len(split) / len(reps) if reps else None), split

    def _accuracy(arm: str, only: set[str] | None = None) -> tuple[int, int]:
        ok = tot = 0
        for _, t in repeat:
            mid = t["message_id"]
            if only is not None and mid not in only:
                continue
            true = labels.get(mid)
            pred = maj[arm].get(mid)
            if not true or pred in (None, "<fail>", "<tie>"):
                continue
            tot += 1
            ok += pred == true
        return ok, tot

    changed = {
        t["message_id"]
        for _, t in repeat
        if maj["on"].get(t["message_id"]) != maj["off"].get(t["message_id"])
    }
    sr_off, split_off = _split_by_arm("off")
    sr_on, split_on = _split_by_arm("on")
    acc_off = _accuracy("off")
    acc_on = _accuracy("on")
    acc_off_ch = _accuracy("off", changed)
    acc_on_ch = _accuracy("on", changed)

    # the CONCRETE correctness case study: on genuinely-mixed senders, did ON wrongly
    # COLLAPSE the sender to one category (bad anchoring)?
    collapse = []
    for s in sorted(mixed_senders):
        on_cats = {
            maj["on"].get(t["message_id"])
            for _, t in repeat
            if _sender(t) == s and maj["on"].get(t["message_id"]) not in ("<fail>", "<tie>")
        }
        off_cats = {
            maj["off"].get(t["message_id"])
            for _, t in repeat
            if _sender(t) == s and maj["off"].get(t["message_id"]) not in ("<fail>", "<tie>")
        }
        collapse.append(
            {
                "sender": s,
                "true_categories": sorted(truth_by_sender[s]),
                "OFF_categories": sorted(off_cats),
                "ON_categories": sorted(on_cats),
                "ON_wrongly_collapsed": len(on_cats) == 1 and len(truth_by_sender[s]) > 1,
            }
        )

    result = {
        "account": account,
        "k": k,
        "n_repeat_msgs": len(repeat),
        "n_repeat_senders": len(truth_by_sender),
        "mixed_senders": sorted(mixed_senders),
        "split_rate": {
            "off": sr_off,
            "on": sr_on,
            "off_split_senders": split_off,
            "on_split_senders": split_on,
        },
        "accuracy": {
            "off": f"{acc_off[0]}/{acc_off[1]}",
            "on": f"{acc_on[0]}/{acc_on[1]}",
            "off_on_changed": f"{acc_off_ch[0]}/{acc_off_ch[1]}",
            "on_on_changed": f"{acc_on_ch[0]}/{acc_on_ch[1]}",
        },
        "n_changed_on_vs_off": len(changed),
        "mixed_sender_check": collapse,
        "AGGREGATE_POWER": "UNDERPOWERED: paired McNemar needs >=5 senders flipping the "
        "same way for p<.05; only ~2-4 senders can differ between arms at "
        "N=17. The split-rate delta is descriptive ONLY, not significant. "
        "An aggregate verdict needs a much larger corpus.",
    }
    out = DATA_DIR / f"{account}_recall_verdict.json"
    out.write_text(json.dumps(result, indent=2, default=str))
    print("\n" + "=" * 66)
    print(f"split_rate  OFF={sr_off}  ON={sr_on}   (DESCRIPTIVE — aggregate UNDERPOWERED)")
    print(
        f"accuracy    OFF={acc_off[0]}/{acc_off[1]}  ON={acc_on[0]}/{acc_on[1]}  "
        f"(on changed msgs: OFF {acc_off_ch[0]}/{acc_off_ch[1]} vs ON {acc_on_ch[0]}/{acc_on_ch[1]})"
    )
    print(f"ON changed {len(changed)} of {len(repeat)} messages vs OFF")
    print("\nCONCRETE correctness — genuinely-mixed senders (recall must NOT collapse):")
    for c in collapse:
        flag = "  ✗ ON WRONGLY COLLAPSED" if c["ON_wrongly_collapsed"] else "  ✓ preserved"
        print(
            f"  {c['sender'][:34]:36} true={c['true_categories']} "
            f"OFF={c['OFF_categories']} ON={c['ON_categories']}{flag}"
        )
    print(f"\n{result['AGGREGATE_POWER']}")
    print(f"Full JSON: {out}")
    return 0


def _load_labels(account: str) -> dict[str, str]:
    """message_id -> operator source label (from the labeled template)."""
    import csv as _csv

    path = DATA_DIR / f"{account}_labels_template.csv"
    labels: dict[str, str] = {}
    if not path.exists():
        return labels
    with path.open(newline="") as fh:
        header: list[str] = []
        for raw in _csv.reader(fh):
            if not raw or raw[0].startswith("#"):
                continue
            if raw[0] == "message_id":
                header = raw
            elif header:
                row = dict(zip(header, raw + [""] * (len(header) - len(raw)), strict=False))
                if row.get("label"):
                    labels[row["message_id"]] = row["label"]
    return labels


async def _run_arm_k(
    arm: str,
    fixtures: list[tuple[Path, dict]],
    *,
    deps: dict[str, Any],
    inject: bool,
    k: int,
    td: Path,
) -> dict[str, str]:
    """K independent full-sequence passes (each its own persistent scratch store);
    return the MAJORITY category per message_id (review R1 #4). Ties -> '<tie>'."""
    from workflow_platform.memory.learned import LearnedMemoryService

    definition = deps["definition"] if inject else _strip_recall(deps["definition"])
    per_msg: dict[str, list[str]] = collections.defaultdict(list)
    for rep in range(k):
        learned = LearnedMemoryService(deps["bedrock"], db_path=td / f"{arm}_{rep}.db")
        for _path, trig in fixtures:
            cat, status, _ = await _run_one(definition, deps["trim"](trig), learned, deps)
            per_msg[trig["message_id"]].append(cat if status == "ok" else "<fail>")
        print(f"  [{arm}] pass {rep + 1}/{k} done")
    out: dict[str, str] = {}
    for mid, cats in per_msg.items():
        ok = [c for c in cats if c not in ("<fail>",)]
        if not ok:
            out[mid] = "<fail>"
            continue
        top = collections.Counter(ok).most_common()
        out[mid] = top[0][0] if (len(top) == 1 or top[0][1] > top[1][1]) else "<tie>"
    return out


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
        help="run the labeled verdict path (K-repeat majority + accuracy guard + mixed-sender "
        "check); reports the aggregate consistency delta as UNDERPOWERED per the MDE",
    )
    p.add_argument("--k", type=int, default=3, help="verdict passes per arm (majority vote)")
    args = p.parse_args()
    if args.verdict:
        raise SystemExit(asyncio.run(_run_verdict(args.account, args.k)))
    if args.noise_floor_k and args.noise_floor_k < 2:
        print("--noise-floor-k must be >= 2 (a single re-run cannot show a flip).")
        raise SystemExit(2)
    raise SystemExit(
        asyncio.run(_main(args.account, args.limit, args.repeat_only, args.noise_floor_k))
    )


if __name__ == "__main__":
    main()
