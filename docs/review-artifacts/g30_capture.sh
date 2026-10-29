#!/usr/bin/env bash
# Build the round's gate capture (default docs/archives/GATE_OUTPUT_G30_R3.txt): the five gates (full output, exit
# codes before any pipe), then every claim (g30_reproductions.py) and every
# control (g30_controls.py), both with the opt-in suites ENABLED.
#
#     TEST_DATABASE_URL=<a db whose tables you can lose> ./g30_capture.sh
#
# Written to a temp file and moved into place at the end: the figure test reads
# this file, so during a run it reads the PREVIOUS capture, never a half-written
# one. The first capture on a fresh tree therefore shows the figure claims red;
# a second converges. (The control-count figure is taken from the harness
# definition, not from this file, so the capture never checks its own controls.)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
# Round 1 wrote GATE_OUTPUT_G30.txt; each round keeps its own file so an
# earlier package's recorded hashes stay true of the tree.
OUT="${1:-$REPO/docs/archives/GATE_OUTPUT_G30_R3.txt}"
: "${TEST_DATABASE_URL:?set TEST_DATABASE_URL to a database whose tables you can lose}"
export BROWSER_LIVE=1 TEST_DATABASE_URL
TMP="$(mktemp)"

# The gates mirror CI's unit run: WITHOUT the opt-in env. With TEST_DATABASE_URL
# exported, pytest also ran the Postgres integration tests against a database
# that was never migrated to head — two question-feature tests failed on the
# first capture for that reason alone. CI runs those after `alembic upgrade
# head`; this package cites that run, and D3 runs below with the env set.
env -u TEST_DATABASE_URL -u BROWSER_LIVE "$REPO/scripts/capture_gates.sh" "$TMP"; gates=$?
{
  echo "=== claims: g30_reproductions.py (BROWSER_LIVE=1, TEST_DATABASE_URL set) ==="
} >> "$TMP"
( cd "$REPO/backend" && uv run python "$HERE/g30_reproductions.py" ) >> "$TMP" 2>&1; claims=$?
echo "exit: $claims" >> "$TMP"; echo >> "$TMP"
{
  echo "=== controls: g30_controls.py (BROWSER_LIVE=1, TEST_DATABASE_URL set) ==="
} >> "$TMP"
( cd "$REPO/backend" && .venv/bin/python "$HERE/g30_controls.py" ) >> "$TMP" 2>&1; controls=$?
echo "exit: $controls" >> "$TMP"

mv "$TMP" "$OUT"
echo "gates=$gates claims=$claims controls=$controls  -> $OUT"
[ "$gates" -eq 0 ] && [ "$claims" -eq 0 ] && [ "$controls" -eq 0 ]
