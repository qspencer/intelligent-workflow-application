"""Fetch the N most recent inbox messages from a Gmail account and write
each as a JSON file matching `EmailMessage.model_dump_json()`.

Output lands under `data/email_triage/<account>/` at the project root.
That directory is gitignored — these are real emails and shouldn't be
committed.

Usage (from repo root):
    BEDROCK_LIVE=0 uv run --project backend python backend/tools/fetch_gmail_inbox.py \
        --account intelligent.workflow.engine@quentinspencer.com \
        --count 20

Or, from `backend/`:
    uv run python tools/fetch_gmail_inbox.py --account <addr> --count 20

Requires `.secrets/gmail/<account>/{client_credentials.json,refresh_token}`
to be present — see `docs/EMAIL_CONNECTOR_PLAN.md` Gate 4 if not.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from workflow_platform.connectors.email.bootstrap import maybe_build_gmail_connector
from workflow_platform.secrets import EnvSecretStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("fetch_gmail_inbox")

REPO_ROOT = Path(__file__).resolve().parents[2]


async def fetch(account: str, count: int, label: str | None) -> int:
    out_dir = REPO_ROOT / "data" / "email_triage" / account
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Output dir: %s", out_dir)

    connector = maybe_build_gmail_connector(
        account=account, secret_store=EnvSecretStore()
    )
    if connector is None:
        logger.error(
            "Failed to build Gmail connector. Check .secrets/gmail/%s/ has "
            "client_credentials.json and refresh_token (chmod 0600).",
            account,
        )
        return 1

    logger.info("Polling inbox (max %d messages, label=%r)…", count, label)
    messages = await connector.poll_inbox(max_messages=count, label=label)
    logger.info("Got %d messages.", len(messages))

    for i, msg in enumerate(messages, start=1):
        # Filename: NN_<subject-slug>.json. Slug is short, ascii-only,
        # filesystem-safe; uniqueness comes from the NN prefix not the slug.
        slug = "".join(c if c.isalnum() else "_" for c in msg.subject)[:40].strip("_")
        if not slug:
            slug = "no_subject"
        path = out_dir / f"{i:02d}_{slug}.json"
        path.write_text(msg.model_dump_json(indent=2))
        logger.info(
            "  [%02d] from=%-30s subject=%r",
            i,
            msg.from_address.address,
            msg.subject[:60],
        )

    logger.info("Done. %d files in %s", len(messages), out_dir)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True, help="Gmail address")
    parser.add_argument(
        "--count", type=int, default=20, help="Max messages to fetch (default 20)"
    )
    parser.add_argument(
        "--label",
        default="INBOX",
        help="Gmail label filter (default INBOX). Use empty string for all mail.",
    )
    args = parser.parse_args()
    label = args.label if args.label else None
    return asyncio.run(fetch(args.account, args.count, label))


if __name__ == "__main__":
    raise SystemExit(main())
