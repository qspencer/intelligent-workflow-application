#!/usr/bin/env bash
#
# run-local-be.sh — start the backend locally in a full-feature configuration
# suitable for manually testing every application feature.
#
# Backend only. For the canvas GUI (C5-C7), start the frontend dev server in a
# second terminal: `cd frontend && npm run dev` (serves :4200, proxies to here).
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
#   scripts/run-local-be.sh                # full config above
#   scripts/run-local-be.sh --in-memory    # skip Postgres (ephemeral repos, no docker)
#   scripts/run-local-be.sh --no-triggers  # don't auto-start triggers (no schedule/gmail
#                                        #   Bedrock spend; fire workflows manually instead)
#   scripts/run-local-be.sh --replay       # BEDROCK_MODE=replay (no AWS/Bedrock calls)
#   scripts/run-local-be.sh --local-auth    # AUTH_MODE=local: first-party email+password
#                                        #   login (docs/AUTH_PLAN.md) instead of dev
#                                        #   headers. Wants Postgres (users live in the
#                                        #   DB); create users with tools/create_user.py.
#                                        #   See run-local-be-auth.sh for the one-command
#                                        #   stop-service-and-run wrapper.
#   scripts/run-local-be.sh --as-service   # (re)install + restart systemd --user unit
#                                        #   `workflow-be` and tail its logs; survives
#                                        #   RDP disconnects. One-time prereq (sudo):
#                                        #   loginctl enable-linger $USER. Any other
#                                        #   flag combined with --as-service is baked
#                                        #   into the unit's ExecStart.
#   scripts/run-local-be.sh --cheatsheet   # print operational commands (service ctl,
#                                        #   logs, health) and exit
#
# Env overrides: PORT (8001), GMAIL_ACCOUNT, DATABASE_URL, BEDROCK_MODE, LOG_FORMAT
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND="$REPO_ROOT/backend"

# --- status helpers (colour only on a real terminal) --------------------------
if [ -t 1 ]; then C_STEP=$'\033[1;36m'; C_OK=$'\033[1;32m'; C_WARN=$'\033[1;33m'; C_OFF=$'\033[0m'
else C_STEP=''; C_OK=''; C_WARN=''; C_OFF=''; fi
STEP=0
step() { STEP=$((STEP + 1)); printf '%s[%d] %s%s\n' "$C_STEP" "$STEP" "$*" "$C_OFF"; }
ok()   { printf '    %s✓%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '    %s!%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
die()  { printf '    %s✗%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; exit 1; }

# --- defaults + flags ---------------------------------------------------------
USE_POSTGRES=1
START_TRIGGERS=1
AS_SERVICE=0
AUTH_MODE_CHOICE=dev
PASSTHROUGH_ARGS=()
PORT="${PORT:-8001}"
BEDROCK_MODE="${BEDROCK_MODE:-live}"
GMAIL_ACCOUNT="${GMAIL_ACCOUNT:-intelligent.workflow.engine@quentinspencer.com}"
DEFAULT_DB_URL="postgresql+asyncpg://workflow:workflow@localhost:5432/workflow"

usage() { sed -n '2,39p' "$0" | sed 's/^# \{0,1\}//'; }

cheatsheet() {
  cat <<'SHEET'
workflow-be operational commands
────────────────────────────────
Restart (fresh unit + code):   scripts/run-local-be.sh --as-service
Or plain restart:              systemctl --user restart workflow-be
Status:                        systemctl --user status workflow-be
Follow logs:                   journalctl --user -u workflow-be -f
Recent logs (last 100):        journalctl --user -u workflow-be -n 100
Stop:                          systemctl --user stop workflow-be
Health check:                  curl -s http://localhost:8001/api/health

Foreground (no service):       scripts/run-local-be.sh
Unit file:                     ~/.config/systemd/user/workflow-be.service
SHEET
}

for arg in "$@"; do
  case "$arg" in
    --as-service)  AS_SERVICE=1 ;;
    --in-memory)   USE_POSTGRES=0;   PASSTHROUGH_ARGS+=("$arg") ;;
    --no-triggers) START_TRIGGERS=0; PASSTHROUGH_ARGS+=("$arg") ;;
    --replay)      BEDROCK_MODE=replay; PASSTHROUGH_ARGS+=("$arg") ;;
    --local-auth)  AUTH_MODE_CHOICE=local; PASSTHROUGH_ARGS+=("$arg") ;;
    --cheatsheet)  cheatsheet; exit 0 ;;
    -h|--help)     usage; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# --- systemd --user service path ---------------------------------------------
# When --as-service is given, we write/refresh the unit, restart the service,
# and hand off to journalctl. The pre-flight + uvicorn launch runs INSIDE the
# service (systemd re-invokes this script without --as-service).
if [ "$AS_SERVICE" = 1 ]; then
  UNIT_DIR="$HOME/.config/systemd/user"
  UNIT_FILE="$UNIT_DIR/workflow-be.service"
  mkdir -p "$UNIT_DIR"

  EXEC_LINE="$SCRIPT_DIR/run-local-be.sh"
  if [ "${#PASSTHROUGH_ARGS[@]}" -gt 0 ]; then
    for a in "${PASSTHROUGH_ARGS[@]}"; do EXEC_LINE="$EXEC_LINE $a"; done
  fi

  cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=Intelligent Workflow Backend (local dev)
After=network-online.target

[Service]
Type=exec
WorkingDirectory=$REPO_ROOT
ExecStart=$EXEC_LINE
Restart=on-failure
RestartSec=5
Environment=PATH=$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=-%h/.config/workflow-be.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
UNIT

  # Warn if linger isn't enabled — service will die at logout without it.
  if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    warn "linger not enabled for $USER — service will stop at logout."
    warn "Run once:  sudo loginctl enable-linger $USER"
  fi

  systemctl --user daemon-reload
  systemctl --user enable workflow-be.service >/dev/null 2>&1 || true
  systemctl --user restart workflow-be.service
  ok "workflow-be.service (re)started with ExecStart: $EXEC_LINE"
  echo "    stop with:  systemctl --user stop workflow-be"
  echo "    following logs (Ctrl-C detaches; service keeps running)..."
  exec journalctl --user -u workflow-be -n 0 -f
fi

# --- pre-flight ---------------------------------------------------------------
step "Pre-flight checks"
command -v uv >/dev/null 2>&1 || die "'uv' not found on PATH."
ok "uv: $(uv --version 2>/dev/null | head -1)"

# Port free? The most common collision: the workflow-be systemd service is
# already serving this port — this script and the service are two ways of
# running the same backend. (Inside the service this check is harmless:
# systemd stops the old instance before ExecStart, so the port is free.)
if ss -tln 2>/dev/null | grep -q ":$PORT "; then
  if systemctl --user is-active --quiet workflow-be 2>/dev/null; then
    warn "port $PORT is in use and the workflow-be systemd service is active."
    warn "The backend is already running — use it directly, or for a foreground run:"
    warn "  systemctl --user stop workflow-be    # then re-run this script"
    warn "(scripts/run-local-be.sh --cheatsheet lists the service commands)"
    die "not starting a second backend on port $PORT."
  fi
  die "port $PORT is already in use by another process (see: ss -tlnp | grep :$PORT). Free it or set PORT=<other>."
fi
ok "port $PORT is free"

# Gmail credentials on disk. Every complete credential dir is usable by
# gmail_poll triggers (the orchestrator seeds each trigger's account from
# .secrets); the send/label TOOLS bind to the single $GMAIL_ACCOUNT.
# (Existence only — a trigger self-disables with one warning if absent.)
GMAIL_OK=0
for dir in "$REPO_ROOT"/.secrets/gmail/*/; do
  [ -d "$dir" ] || continue
  acct="$(basename "$dir")"
  if [ -f "$dir/client_credentials.json" ] && [ -f "$dir/refresh_token" ]; then
    if [ "$acct" = "$GMAIL_ACCOUNT" ]; then
      GMAIL_OK=1
      ok "Gmail credentials: $acct (tools account: email_send / email_label_apply)"
    else
      ok "Gmail credentials: $acct (poll triggers only)"
    fi
  else
    warn "Gmail credentials: $acct incomplete (missing client_credentials.json or refresh_token)"
  fi
done
[ "$GMAIL_OK" = 1 ] || warn "Gmail tools account $GMAIL_ACCOUNT has no credentials — email tools will not be wired"

# AWS creds only matter for live Bedrock.
if [ "$BEDROCK_MODE" = "live" ]; then
  if command -v aws >/dev/null 2>&1; then
    if aws sts get-caller-identity >/dev/null 2>&1; then
      ok "AWS credentials: resolve (live Bedrock ready)"
    else
      warn "AWS credentials: do NOT resolve — agentic steps will fail (use --replay)"
    fi
  else
    warn "AWS CLI not found — can't verify creds; live Bedrock may fail (use --replay)"
  fi
else
  ok "Bedrock: replay mode — no AWS calls"
fi

# --- repositories -------------------------------------------------------------
if [ "$USE_POSTGRES" = 1 ]; then
  command -v docker >/dev/null 2>&1 || die "docker not found. Re-run with --in-memory."
  DATABASE_URL="${DATABASE_URL:-$DEFAULT_DB_URL}"

  step "Postgres: start container (docker compose up -d postgres)"
  ( cd "$REPO_ROOT" && docker compose up -d postgres >/dev/null )
  ok "container up"

  step "Postgres: wait for healthy"
  ( cd "$REPO_ROOT"
    for _ in $(seq 1 30); do
      if docker compose ps postgres --format json | grep -q '"Health":"healthy"'; then exit 0; fi
      sleep 2
    done
    exit 1
  ) || die "Postgres did not become healthy in time."
  ok "healthy"

  step "Migrations: alembic upgrade head"
  ( cd "$BACKEND" && DATABASE_URL="$DATABASE_URL" uv run alembic upgrade head )
  ok "schema up to date"
  export DATABASE_URL
else
  step "Repositories: in-memory (nothing persists across restarts)"
  ok "no Postgres / docker needed"
  unset DATABASE_URL 2>/dev/null || true
fi

# --- runtime env --------------------------------------------------------------
step "Configure runtime environment"
export AUTH_MODE="$AUTH_MODE_CHOICE"
if [ "$AUTH_MODE" = "local" ]; then
  # Local-loop convenience: seed the per-role test accounts on boot
  # (known credentials — never set this on a network-reachable deploy).
  export WORKFLOW_PLATFORM_SEED_TEST_USERS="${WORKFLOW_PLATFORM_SEED_TEST_USERS:-1}"
fi
export BEDROCK_MODE
export WORKFLOW_DEFINITIONS_DIR="$REPO_ROOT/examples"
export LOG_FORMAT="${LOG_FORMAT:-text}"
export WORKFLOW_PLATFORM_START_TRIGGERS="$START_TRIGGERS"
[ "$GMAIL_OK" = 1 ] && export WORKFLOW_PLATFORM_GMAIL_ACCOUNT="$GMAIL_ACCOUNT"
ok "env set"

echo "──────────────────────────────────────────────────────────────"
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
if [ "$AUTH_MODE" = "local" ]; then
  echo "   auth         : local (email+password login; manage users via"
  echo "                  backend/tools/create_user.py or the Users admin page)"
  echo "                  test accounts seeded on boot: admin@test.local /"
  echo "                  org-admin@ / org-user@ / org-viewer@test.local"
  echo "                  (password: test-password). Permanent admin: set"
  echo "                  WORKFLOW_PLATFORM_ADMIN_EMAIL + _ADMIN_PASSWORD."
else
  echo "   auth         : dev  (send X-Dev-User / X-Dev-Groups; default acts as admin)"
fi
echo "   backend on   : http://localhost:$PORT"
echo "   frontend     : not started — for the canvas GUI (C5-C7) run in another"
echo "                  terminal:  cd frontend && npm run dev   (→ http://localhost:4200)"
echo "──────────────────────────────────────────────────────────────"

# --- launch -------------------------------------------------------------------
step "Launch uvicorn (Ctrl-C to stop) — startup logs + per-trigger status follow"
cd "$BACKEND"
exec uv run uvicorn workflow_platform.main:app --reload --port "$PORT"
