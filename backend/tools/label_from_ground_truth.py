#!/usr/bin/env python3
"""Apply wf/* labels to historical mail from the human ground-truth corpus.

The 154-message labeled corpus (.memory/triage-ground-truth.jsonl) holds a
human-verified category for every message — judgment already spent once.
Applying labels from it is a zero-LLM, zero-error lookup: the codification
thesis in one script. Touches ONLY Gmail (no engine run, no memory writes,
so veracium evidence counts stay undistorted).

Last answer wins per message_id (same rule as seed_outcomes). Idempotent:
Gmail label-add is a no-op when the label is already present.

Usage:
    uv run python tools/label_from_ground_truth.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.connectors.email.bootstrap import maybe_build_gmail_connector
from workflow_platform.connectors.email.gmail import GmailMessageNotFound
from workflow_platform.engine.functions import TRIAGE_CATEGORIES
from workflow_platform.secrets import EnvSecretStore

ACCOUNT = "qspencer@gmail.com"
LABELS_PATH = Path(__file__).resolve().parent.parent / ".memory" / "triage-ground-truth.jsonl"


async def run(args: argparse.Namespace) -> int:
    if not LABELS_PATH.exists():
        print(f"no ground-truth file at {LABELS_PATH}")
        return 2
    by_message: dict[str, str] = {}
    for line in LABELS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        by_message[entry["message_id"]] = entry["true_category"]

    invalid = {m: c for m, c in by_message.items() if c not in TRIAGE_CATEGORIES}
    for message_id in invalid:
        del by_message[message_id]

    connector = maybe_build_gmail_connector(account=ACCOUNT, secret_store=EnvSecretStore())
    if connector is None:
        print(f"no credentials for {ACCOUNT!r}; aborting")
        return 2

    stats: Counter[str] = Counter()
    for message_id, category in by_message.items():
        label = f"wf/{category}"
        if args.dry_run:
            stats[f"would:{label}"] += 1
            continue
        try:
            await connector.apply_labels(message_id, [label])
            stats[f"applied:{label}"] += 1
        except GmailMessageNotFound:
            stats["missing_message"] += 1
        except Exception as exc:
            stats["error"] += 1
            print(f"  ! {message_id}: {exc}")

    print(f"messages in corpus: {len(by_message)} (+{len(invalid)} skipped: stale category)")
    for key, count in sorted(stats.items()):
        print(f"  {key}: {count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
