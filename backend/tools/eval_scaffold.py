#!/usr/bin/env python3
"""Run the L1/L2 scaffold eval (docs/product/LLM_EVAL_FRAMEWORK.md).

Feeds each test case from docs/product/LLM_EVAL_TEST_SUITE.md to the scaffold
model with the platform's live catalog, then scores L1 (parses + validates)
and L2 (constraint satisfaction). Criteria naming catalog capabilities we
haven't built are reported `unsatisfiable`, not failed; free-text criteria are
deferred to the (future, human-calibrated) L3/L4 judge.

Usage:
    uv run python tools/eval_scaffold.py                       # default model, all cases
    uv run python tools/eval_scaffold.py --model <bedrock-id> --limit 5
    uv run python tools/eval_scaffold.py --only simple_file_move

Costs real Bedrock spend in live mode (~$0.005-0.01/case at Haiku pricing).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.bedrock import BedrockClient
from workflow_platform.catalog import build_catalog
from workflow_platform.connectors.email import maybe_build_gmail_connector
from workflow_platform.engine import ToolCatalog, default_function_registry
from workflow_platform.evals import evaluate_model, load_cases
from workflow_platform.scaffold import DEFAULT_SCAFFOLD_MODEL
from workflow_platform.secrets import EnvSecretStore
from workflow_platform.tools import (
    EmailLabelApplyTool,
    EmailSendTool,
    FileReadTool,
    FileWriteTool,
    PdfExtractTool,
    Tool,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SUITE = BACKEND_DIR.parent / "docs" / "product" / "LLM_EVAL_TEST_SUITE.md"


def _build_tools() -> list[Tool]:
    """Same catalog surface the production scaffold endpoint advertises:
    stock tools, plus Gmail tools when credentials are wired."""
    tools: list[Tool] = [PdfExtractTool(), FileReadTool(), FileWriteTool()]
    import os

    account = os.environ.get("WORKFLOW_PLATFORM_GMAIL_ACCOUNT")
    connector = maybe_build_gmail_connector(account=account, secret_store=EnvSecretStore())
    if connector is not None:
        tools.extend([EmailSendTool(connector), EmailLabelApplyTool(connector)])
    return tools


async def run(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases)
    if args.only:
        cases = [c for c in cases if c.id == args.only]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("No cases matched.")
        return 2

    catalog = build_catalog(default_function_registry(), ToolCatalog(_build_tools()))
    bedrock = BedrockClient()
    print(f"model    : {args.model}")
    print(f"cases    : {len(cases)}  (suite: {args.cases})")
    print(f"bedrock  : mode={bedrock.mode.value}\n")

    report = await evaluate_model(
        bedrock, model=args.model, cases=cases, catalog=catalog, concurrency=args.concurrency
    )

    for r in report["results"]:
        scoreable = [c for c in r["criteria"] if c["status"] in ("pass", "fail")]
        n_pass = sum(1 for c in scoreable if c["status"] == "pass")
        flags = "".join(
            {"unsatisfiable": "u", "judge": "j"}.get(c["status"], "")
            for c in r["criteria"]
            if c["status"] in ("unsatisfiable", "judge")
        )
        l1 = "ok " if r["l1_pass"] else "L1✗"
        line = f"  {l1} L2 {n_pass}/{len(scoreable):<2} {r['case_id']:<38} [{r['category']}]"
        if flags:
            line += f" ({flags})"
        print(line)
        if not r["l1_pass"]:
            print(f"        {r['l1_error']}")
        for c in scoreable:
            if c["status"] == "fail":
                print(f"        ✗ {c['name']}: {c['detail']}")

    print(f"\ncatalog  : {report['catalog_hash']}")
    print(f"L1       : {report['l1_pass']}/{report['cases']}  ({report['l1_rate']:.0%})")
    if report["l2_rate"] is not None:
        print(
            f"L2       : {report['l2_criteria_passed']}/{report['l2_criteria_scored']}"
            f" criteria  ({report['l2_rate']:.0%})"
        )
    if report["unsatisfiable"]:
        print(f"unsatisfiable criteria (catalog gaps, excluded): {len(report['unsatisfiable'])}")
        for case_id, name in report["unsatisfiable"]:
            print(f"  - {case_id}: {name}")
    print(f"judge-deferred criteria (L3/L4): {report['judge_deferred']}")

    verdict_l1 = report["l1_rate"] == 1.0
    verdict_l2 = (report["l2_rate"] or 0) >= 0.90
    print(
        f"\nframework pass criteria: L1 100% -> {'✅' if verdict_l1 else '❌'}"
        f"   L2 >=90% -> {'✅' if verdict_l2 else '❌'}"
        "   (L3/L4 pending the judged layers)"
    )

    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, default=str))
        print(f"full report: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_SCAFFOLD_MODEL)
    parser.add_argument("--cases", default=str(DEFAULT_SUITE))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", default="")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--report", default="/tmp/scaffold-eval-report.json")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
