"""Backfill DMARC report mail into the dmarc-ingest workflow.

The `gmail_poll` trigger only sees mail received after it starts (cursor
initializes to "now"). This one-shot fetches *historical* messages matching
the same Gmail query, downloads their attachments to the same spool layout
the trigger uses (`<spool>/<message_id>/<filename>`), and either prints the
workflow payloads as a JSON array (pipe them wherever you like) or — with
`--fire` — POSTs them straight to the running backend's run-batch endpoint,
one workflow instance per message.

Prereqs: Gmail credentials for the account under
`.secrets/gmail/<account>/` (Gates 3-4 in docs/EMAIL_CONNECTOR_PLAN.md) and,
for `--fire`, the backend running with the `dmarc-ingest` workflow loaded.

Usage:
    cd backend
    uv run python tools/fetch_dmarc.py \\
        --account qrsconsulting@quentinspencer.com \\
        --since 2024-01-01 \\
        --fire

Exits non-zero if any fired row fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from workflow_platform.connectors.email import maybe_build_gmail_connector
from workflow_platform.secrets import EnvSecretStore

DEFAULT_QUERY = "has:attachment (filename:zip OR filename:gz)"
BATCH_LIMIT = 100  # server-side cap on run-batch rows


async def collect_payloads(args: argparse.Namespace) -> list[dict[str, Any]]:
    connector = maybe_build_gmail_connector(account=args.account, secret_store=EnvSecretStore())
    if connector is None:
        print(
            f"No Gmail credentials for {args.account!r} under .secrets/gmail/. "
            "Run tools/gmail_auth.py first (Gates 3-4 in docs/EMAIL_CONNECTOR_PLAN.md).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
    # label=None: search the whole mailbox, not just INBOX — historical
    # reports are often archived.
    messages = await connector.poll_inbox(
        since=since, label=None, max_messages=args.max, query=args.query
    )
    print(f"{len(messages)} matching message(s) since {args.since}.", file=sys.stderr)

    payloads: list[dict[str, Any]] = []
    for msg in messages:
        target = Path(args.spool_dir) / msg.message_id
        target.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for i, att in enumerate(msg.attachments):
            name = Path(att.filename).name or f"attachment-{i}"
            dest = target / name
            dest.write_bytes(await connector.download_attachment(msg.message_id, att.attachment_id))
            paths.append(str(dest))
        print(f"  {msg.message_id}: {len(paths)} attachment(s) — {msg.subject!r}", file=sys.stderr)
        payloads.append({**msg.model_dump(mode="json"), "attachment_paths": paths})
    return payloads


def fire_batches(payloads: list[dict[str, Any]], args: argparse.Namespace) -> int:
    headers = {"X-Dev-User": "fetch-dmarc", "X-Dev-Groups": "admins"}
    url = f"{args.base_url.rstrip('/')}/api/workflows/{args.workflow_id}/run-batch"
    failed_total = 0
    with httpx.Client(timeout=300.0) as client:
        for start in range(0, len(payloads), BATCH_LIMIT):
            chunk = payloads[start : start + BATCH_LIMIT]
            resp = client.post(url, json=chunk, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            failed_total += int(body.get("failed", 0))
            print(
                f"batch {start // BATCH_LIMIT + 1}: submitted={body['submitted']} "
                f"succeeded={body['succeeded']} failed={body['failed']}",
                file=sys.stderr,
            )
            for row in body.get("results", []):
                if not row.get("ok"):
                    print(f"  row {row['index']}: {row.get('error')}", file=sys.stderr)
    return failed_total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--account", required=True, help="Gmail address with .secrets credentials")
    parser.add_argument("--since", required=True, help="ISO date, e.g. 2024-01-01")
    parser.add_argument(
        "--query", default=DEFAULT_QUERY, help=f"Gmail query (default: {DEFAULT_QUERY!r})"
    )
    parser.add_argument(
        "--spool-dir", default="/tmp/dmarc-spool", help="Attachment spool directory"
    )
    parser.add_argument("--max", type=int, default=500, help="Max messages to fetch")
    parser.add_argument("--fire", action="store_true", help="POST payloads to run-batch")
    parser.add_argument("--workflow-id", default="dmarc-ingest")
    parser.add_argument("--base-url", default="http://localhost:8001")
    args = parser.parse_args()

    payloads = asyncio.run(collect_payloads(args))
    if not args.fire:
        json.dump(payloads, sys.stdout, indent=2)
        print()
        return 0
    if not payloads:
        return 0
    return 1 if fire_batches(payloads, args) else 0


if __name__ == "__main__":
    raise SystemExit(main())
