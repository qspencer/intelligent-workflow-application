#!/usr/bin/env python3
"""Pre-create the wf/* triage labels on a Gmail account (EMAIL_TRIAGE_ACT_PLAN §4).

The acting triage workflow can only APPLY labels — `_resolve_label_id`
refuses to create them, which makes the mailbox's own label list a physical
allowlist. This CLI is the one place labels come from: idempotent, prints
what already exists, creates what's missing.

Usage:
    uv run python tools/setup_triage_labels.py qspencer@gmail.com [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.connectors.email.bootstrap import maybe_build_gmail_connector
from workflow_platform.engine.functions import TRIAGE_CATEGORIES
from workflow_platform.secrets import EnvSecretStore


async def run(args: argparse.Namespace) -> int:
    connector = maybe_build_gmail_connector(account=args.account, secret_store=EnvSecretStore())
    if connector is None:
        print(f"no credentials for {args.account!r} under .secrets/gmail/ — aborting")
        return 2

    wanted = [f"wf/{category}" for category in TRIAGE_CATEGORIES]
    existing: set[str] = set()
    for name in wanted:
        try:
            await connector._resolve_label_id(name)
            existing.add(name)
        except Exception:
            pass

    created = 0
    for name in wanted:
        if name in existing:
            print(f"  = {name} (exists)")
            continue
        if args.dry_run:
            print(f"  + {name} (would create)")
            continue
        label_id = await connector.create_label(name)
        print(f"  + {name} (created, id={label_id})")
        created += 1
    print(
        f"done: {len(existing)} existing, {created} created"
        + (" [dry-run]" if args.dry_run else "")
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("account")
    parser.add_argument("--dry-run", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
