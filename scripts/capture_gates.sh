#!/usr/bin/env bash
# Capture the five gates as REVIEW EVIDENCE: full output, exit codes taken
# before any pipe, and the tree + aggregate source hash the run describes.
#
#     ./scripts/capture_gates.sh docs/archives/GATE_OUTPUT_<round>.txt
#
# Why it exists. The G29 package's first capture was a scratch script that kept
# `tail -12` of each gate. pip-audit sorts its table by package name, so that
# kept the last twelve lines of a forty-seven line listing: certifi and
# graphql-core were cut off, the package reported 11 advisories where there
# were 42, and it explained the missing two as having "surfaced after pillow
# was fixed". Evidence is the producing output, so nothing here is truncated.
set -uo pipefail
out="${1:?usage: capture_gates.sh <output file>}"
case "$out" in /*) ;; *) out="$PWD/$out" ;; esac
cd "$(dirname "$0")/../backend" || exit 2

tree=$(git rev-parse HEAD^{tree})
aggregate=$(find src tests -name '*.py' -type f | sort | xargs sha256sum | sha256sum | cut -d' ' -f1)
{
  echo "Captured standalone gate results"
  echo
  echo "Gates ran at tree: $tree   (manifest aggregate: $aggregate)"
  echo "Full output; each exit code captured before any pipe."
  echo "Host: $(uname -sro) · $(uv run python -V 2>&1) · $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  # veracium opens its store on ONE SQLite build only; a capture that does not
  # say which one ran cannot show the learned-memory tests ran where they can.
  echo "Runtime: $(uv run python -c 'import sqlite3, importlib.metadata as m; print("sqlite", sqlite3.sqlite_version, "· veracium", m.version("veracium"))' 2>&1)"
  echo
} > "$out"

failed=0
gate() {
  local label="$1"; shift
  local body; body=$("$@" 2>&1); local code=$?
  { echo "=== $label ==="; echo "$body"; echo "exit: $code"; echo; } >> "$out"
  [ "$code" -eq 0 ] || failed=1
  printf '  %-26s exit=%s\n' "$label" "$code"
}

gate "ruff check"         uv run ruff check .
gate "ruff format --check" uv run ruff format --check .
gate "mypy src tests"     uv run mypy src tests
gate "pytest"             uv run pytest -q
gate "pip-audit"          uv run --with pip-audit pip-audit
echo "Wrote $out"
exit "$failed"
