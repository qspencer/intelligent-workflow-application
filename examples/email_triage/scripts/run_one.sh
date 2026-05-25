#!/usr/bin/env bash
# Fire a single email-fixture JSON through the workflow via tools/fire.py.
#
# Usage (from repo root):
#   examples/email_triage/scripts/run_one.sh examples/email_triage/fixtures/01_urgent_meeting_moved.json
#
# Env vars:
#   DATABASE_URL    -- if set, persists to Postgres and the run shows up
#                      in the dashboard. Unset -> in-memory, one-shot.
#   BEDROCK_MODE    -- live (default), record, or replay.
#   WORKFLOW_PLATFORM_GMAIL_ACCOUNT -- if set, fire.py will wire the
#                      EmailSendTool + EmailLabelApplyTool. Unset means
#                      the workflow will fail because the agent can't
#                      call those tools.

set -euo pipefail

fixture_path="${1:?Usage: $0 <path-to-email-fixture.json>}"
repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"

cd "${repo_root}/backend"
uv run python tools/fire.py \
  --definition "${repo_root}/examples/email_triage/workflow.yaml" \
  --trigger "$(cat "${fixture_path}")"
