#!/usr/bin/env bash
# Fire a single PR JSON object through the workflow via tools/fire.py.
#
# Usage (from repo root):
#   examples/github_pr_triage/scripts/run_one.sh examples/github_pr_triage/fixtures/01_perfect_pr.json
#
# Env vars:
#   DATABASE_URL    — if set, persists to Postgres and the run shows up in
#                     the dashboard. Unset → in-memory, one-shot.
#   BEDROCK_MODE    — live (default), record, or replay.
#
# Exit code: 0 if the workflow completed, non-zero on FAILED / KILLED.

set -euo pipefail

pr_path="${1:?Usage: $0 <path-to-pr-json>}"
repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"

cd "${repo_root}/backend"
uv run python tools/fire.py \
  --definition "${repo_root}/examples/github_pr_triage/workflow.yaml" \
  --trigger "$(cat "${pr_path}")"
