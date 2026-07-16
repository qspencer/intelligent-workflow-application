#!/usr/bin/env bash
# Review email-triage verdicts: mark each correct/incorrect (with the true
# category). Wraps backend/tools/review_triage.py with the local defaults.
#
#   ./scripts/review-emails.sh              # start / resume the review
#   ./scripts/review-emails.sh --summary    # accuracy + corrections so far
#
# Env overrides: DATABASE_URL (defaults to the local dev Postgres).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$SCRIPT_DIR/../backend"

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://workflow:workflow@localhost:5432/workflow}"

cd "$BACKEND"
exec uv run python tools/review_triage.py "$@"
