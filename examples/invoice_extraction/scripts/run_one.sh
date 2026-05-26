#!/usr/bin/env bash
# Fire a single invoice PDF through the extraction workflow via tools/fire.py.
#
# Usage (from repo root):
#   examples/invoice_extraction/scripts/run_one.sh \
#       examples/invoice_extraction/fixtures/invoice_Liz\ Thompson_19562.pdf
#
# Env vars:
#   DATABASE_URL                       — if set, persists to Postgres
#                                        (run visible in dashboard).
#   BEDROCK_MODE                       — live (default) / record / replay.
#   WORKFLOW_PLATFORM_GMAIL_ACCOUNT    — set to enable the notify_* steps.
#                                        Without it, the agent's email_send
#                                        calls fail "Unknown tool" and the
#                                        notify steps audit FAILED.

set -euo pipefail

pdf_path="${1:?Usage: $0 <path-to-invoice.pdf>}"
abs_pdf="$(cd "$(dirname "$pdf_path")" && pwd)/$(basename "$pdf_path")"
repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"

cd "${repo_root}/backend"
uv run python tools/fire.py \
  --definition "${repo_root}/examples/invoice_extraction/workflow.yaml" \
  --trigger "{\"file_path\": \"${abs_pdf}\"}"
