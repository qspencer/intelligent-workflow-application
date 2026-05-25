#!/usr/bin/env bash
# Fire a single paper JSON object through the workflow via tools/fire.py.
#
# Usage (from repo root):
#   examples/research_paper_triage/scripts/run_one.sh examples/research_paper_triage/fixtures/01_directly_relevant_memgpt.json
#
# Env vars:
#   DATABASE_URL    — if set, persists to Postgres and the run shows up in
#                     the dashboard. Unset → in-memory, one-shot.
#   BEDROCK_MODE    — live (default), record, or replay.

set -euo pipefail

paper_path="${1:?Usage: $0 <path-to-paper-json>}"
repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"

cd "${repo_root}/backend"
uv run python tools/fire.py \
  --definition "${repo_root}/examples/research_paper_triage/workflow.yaml" \
  --trigger "$(cat "${paper_path}")"
