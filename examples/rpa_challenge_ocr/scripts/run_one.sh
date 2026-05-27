#!/usr/bin/env bash
# Fire the RPA Challenge OCR workflow once via tools/fire.py.
#
# Usage (from repo root):
#   examples/rpa_challenge_ocr/scripts/run_one.sh
#
# Env vars:
#   DATABASE_URL    — if set, persists to Postgres and the run shows up
#                     in the dashboard. Unset → in-memory, one-shot.
#   BEDROCK_MODE    — live (default), record, or replay.
#   BROWSER_HEADLESS — 'false' to watch the run in a real Chromium window
#                     (defaults to true / no UI).

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"

cd "${repo_root}/backend"
uv run python tools/fire.py \
  --definition "${repo_root}/examples/rpa_challenge_ocr/workflow.yaml" \
  --trigger '{}'
