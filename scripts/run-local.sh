#!/usr/bin/env bash
#
# run-local.sh — start the backend locally in a full-feature configuration
# suitable for manually testing every application feature.
#
# Defaults (the "test everything" config):
#   - Postgres repositories (persistence -> dashboard history, cost reports,
#     fork/retry across restarts). Brought up via docker compose + migrated.
#   - Live Bedrock (agentic steps actually run; needs AWS creds).
#   - All triggers auto-started (filesystem / webhook / schedule / gmail_poll).
#   - Gmail seeded from .secrets/ if present (enables the email-triage trigger).
#   - Dev auth (X-Dev-User / X-Dev-Groups headers; the dashboard role switcher).
#   - Human-readable (text) logs.
#
# Usage:
#   scripts/run-local.sh                # full config above
#   scripts/run-local.sh --in-memory    # skip Postgres (ephemeral repos, no docker)
#   scripts/run-local.sh --no-triggers  # don't auto-start triggers (no schedule/gmail
#                                        #   Bedrock spend; fire workflows manually instead)
#   scripts/run-local.sh --replay       # BEDROCK_MODE=replay (no AWS/Bedrock calls)
#
# Env overrides: PORT (8001), GMAIL_ACCOUNT, DATABASE_URL, BEDROCK_MODE, LOG_FORMAT
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND="$REPO_ROOT/backend"

# --- defaults + flags ---------------------------------------------------------
USE_POSTGRES=1
START_TRIGGERS=1
PORT="${PORT:-8001}"
BEDROCK_MODE="${BEDROCK_MODE:-live}"
GMAIL_ACCOUNT="${GMAIL_ACCOUNT:-intelligent.workflow.engine@quentinspencer.com}"
DEFAULT_DB_URL="postgresql+asyncpg://workflow:workflow@localhost:5432/workflow"

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; }

for arg in "$@"; do
  case "$arg" in
    --in-memory)   USE_POSTGRES=0 ;;
    --no-triggers) START_TRIGGERS=0 ;;
    --replay)      BEDROCK_MODE=replay ;;
    -h|--help)     usage; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# --- pre-flight ---------------------------------------------------------------
command -v uv >/dev/null 2>&1 || { echo "ERROR: 'uv' not found on PATH." >&2; exit 1; }

# Gmail credentials present on disk? (existence only — the trigger self-disables
# with a single warning if absent, so this is non-fatal.)
GMAIL_OK=0
if [ -f "$REPO_ROOT/.secrets/gmail/$GMAIL_ACCOUNT/client_credentials.json" ] \
   && [ -f "$REPO_ROOT/.secrets/gmail/$GMAIL_ACCOUNT/refresh_token" ]; then
  GMAIL_OK=1
fi

# AWS creds only matter for live Bedrock.
if [ "$BEDROCK_MODE" = "live" ]; then
  if command -v aws >/dev/null 2>&1 && ! aws sts get-caller-identity >/dev/null 2>&1; then
    echo "WARNING: BEDROCK_MODE=live but AWS creds don't resolve — agentic steps" >&2
    echo "         will fail. Re-run with --replay to avoid Bedrock entirely." >&2
  fi
fi

# --- Postgres + migrations ----------------------------------------------------
if [ "$USE_POSTGRES" = 1 ]; then
  command -v docker >/dev/null 2>&1 || {
    echo "ERROR: docker not found. Re-run with --in-memory to skip Postgres." >&2; exit 1; }
  DATABASE_URL="${DATABASE_URL:-$DEFAULT_DB_URL}"
  echo ">> Starting Postgres (docker compose up -d postgres)…"
  ( cd "$REPO_ROOT" && docker compose up -d postgres )
  echo ">> Waiting for Postgres to report healthy…"
  ( cd "$REPO_ROOT"
    for _ in $(seq 1 30); do
      if docker compose ps postgres --format json | grep -q '"Health":"healthy"'; then exit 0; fi
      sleep 2
    done
    echo "ERROR: Postgres did not become healthy in time." >&2; exit 1
  )
  echo ">> Applying migrations (alembic upgrade head)…"
  ( cd "$BACKEND" && DATABASE_URL="$DATABASE_URL" uv run alembic upgrade head )
  export DATABASE_URL
else
  echo ">> Using in-memory repositories (nothing persists across restarts)."
  unset DATABASE_URL 2>/dev/null || true
fi

# --- runtime env --------------------------------------------------------------
export AUTH_MODE=dev
export BEDROCK_MODE
export WORKFLOW_DEFINITIONS_DIR="$REPO_ROOT/examples"
export LOG_FORMAT="${LOG_FORMAT:-text}"
export WORKFLOW_PLATFORM_START_TRIGGERS="$START_TRIGGERS"
[ "$GMAIL_OK" = 1 ] && export WORKFLOW_PLATFORM_GMAIL_ACCOUNT="$GMAIL_ACCOUNT"

# --- summary ------------------------------------------------------------------
echo "──────────────────────────────────────────────────────────────"
echo " backend configuration"
if [ "$USE_POSTGRES" = 1 ]; then
  echo "   repositories : Postgres ($DATABASE_URL)"
else
  echo "   repositories : in-memory (ephemeral)"
fi
echo "   bedrock      : $BEDROCK_MODE"
echo "   triggers     : $([ "$START_TRIGGERS" = 1 ] && echo "on (fs / webhook / schedule / gmail)" || echo "off")"
if [ "$GMAIL_OK" = 1 ]; then
  echo "   gmail        : $GMAIL_ACCOUNT (seeded from .secrets/)"
else
  echo "   gmail        : not configured — email-triage trigger self-disables"
fi
echo "   definitions  : $WORKFLOW_DEFINITIONS_DIR"
echo "   auth         : dev  (send X-Dev-User / X-Dev-Groups; default acts as admin)"
echo "   listening on : http://localhost:$PORT   (frontend dev server proxies here)"
echo "──────────────────────────────────────────────────────────────"

# --- launch -------------------------------------------------------------------
cd "$BACKEND"
exec uv run uvicorn workflow_platform.main:app --reload --port "$PORT"
