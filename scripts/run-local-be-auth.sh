#!/usr/bin/env bash
#
# run-local-be-auth.sh — start the backend in AUTH_MODE=local (first-party
# email + password login, docs/AUTH_PLAN.md) in the foreground.
#
# The workflow-be systemd service and this script are two ways of running the
# same backend on port 8001, so: if the service is running it is stopped
# first, and restarted automatically when this script exits (Ctrl-C included)
# — your dev-mode service comes back on its own.
#
# Users live in Postgres; create the first one with:
#   cd backend && DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
#       uv run python tools/create_user.py you@example.com --roles Admin
#
# Any extra flags are passed through to run-local-be.sh (e.g. --replay).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

STOPPED_SERVICE=0
if systemctl --user is-active --quiet workflow-be 2>/dev/null; then
  echo "workflow-be service is running — stopping it for the foreground run..."
  systemctl --user stop workflow-be
  STOPPED_SERVICE=1
fi

CHILD=0
cleanup() {
  trap - EXIT INT TERM
  if [ "$CHILD" != 0 ] && kill -0 "$CHILD" 2>/dev/null; then
    kill "$CHILD" 2>/dev/null || true
    wait "$CHILD" 2>/dev/null || true
  fi
  if [ "$STOPPED_SERVICE" = 1 ]; then
    echo ""
    echo "restarting the workflow-be service..."
    systemctl --user start workflow-be \
      || echo "! failed — start it manually: systemctl --user start workflow-be"
  fi
}
trap cleanup EXIT INT TERM

# Run the backend as a background child and wait on it: a foreground child
# would make bash defer signal traps until it exits, so a TERM to this
# wrapper would never restore the service. `wait` is interruptible; on any
# signal, cleanup kills the backend (run-local-be.sh execs uvicorn, so the
# child PID IS uvicorn) and brings the service back.
"$SCRIPT_DIR/run-local-be.sh" --local-auth "$@" &
CHILD=$!
wait "$CHILD"
