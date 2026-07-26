#!/usr/bin/env python3
"""Retire stale wf-attn/awaiting-reply labels (TWO_AXIS §3 lifecycle).

awaiting-reply is a STATE: true at arrival, false once the user replies.
This sweep finds messages still carrying the label, thread-checks each
(grouped by thread — one metadata-only fetch per thread), and removes the
label where a newer sent message exists. Heuristic, not proof (a newer
sent message may answer a different message in the thread) — the named
false-retirement risk from the design.

Dry-run by default; `--apply` executes. Label REMOVAL is CLI-only surface:
no agent tool can reach `remove_labels` (the add-only fence holds).

Usage:
    uv run python tools/retire_attention_labels.py [--apply] [--account A]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workflow_platform.connectors.email.bootstrap import maybe_build_gmail_connector
from workflow_platform.secrets import EnvSecretStore

LABEL = "wf-attn/awaiting-reply"


async def run(args: argparse.Namespace) -> int:
    connector = maybe_build_gmail_connector(account=args.account, secret_store=EnvSecretStore())
    if connector is None:
        print(f"no credentials for {args.account!r}; aborting")
        return 2

    messages = await connector.poll_inbox(
        since=None, label=None, max_messages=500, query=f'label:"{LABEL}"'
    )
    print(f"messages carrying {LABEL}: {len(messages)}")
    by_thread: dict[str, list] = defaultdict(list)
    for msg in messages:
        by_thread[msg.thread_id or msg.message_id].append(msg)

    retired = kept = unknown = 0
    for thread_id, thread_messages in by_thread.items():
        for msg in thread_messages:
            try:
                replied = await connector.thread_has_newer_sent_message(thread_id, msg.received_at)
            except Exception as exc:
                unknown += 1
                print(f"  ? {msg.message_id} ({msg.subject[:40]!r}): lookup failed: {exc}")
                continue
            if not replied:
                kept += 1
                continue
            if args.apply:
                await connector.remove_labels(msg.message_id, [LABEL])
                print(f"  - retired: {msg.received_at:%m-%d} {msg.subject[:50]!r}")
            else:
                print(f"  - would retire: {msg.received_at:%m-%d} {msg.subject[:50]!r}")
            retired += 1

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[{mode}] retired={retired} kept={kept} unknown={unknown}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually remove labels")
    parser.add_argument("--account", default="qspencer@gmail.com")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
