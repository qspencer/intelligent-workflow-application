#!/usr/bin/env bash
# The five CI-equivalent gates, as ONE command with ONE exit code.
#
# Why this exists: twice in the trace epic a commit went out with mypy red
# (592b1bc, c0c4f03). Both times the gates HAD been run — the failure was
# chaining `git commit` to `pytest` with && while reading the mypy output by
# eye. Five commands with five exit codes is five chances to read the wrong
# one, so:
#
#     ./scripts/gate.sh && git commit -m "..."
#
# Each gate's exit code is captured IMMEDIATELY, before any pipe: piping
# ruff through `tail` reports tail's status, which is how a red gate read
# green earlier in this project.
set -uo pipefail
cd "$(dirname "$0")/../backend" || exit 2

failed=()
run() {
  local name="$1"; shift
  local out; out=$("$@" 2>&1); local code=$?
  if [ $code -eq 0 ]; then
    printf '  \033[32m✓\033[0m %-22s\n' "$name"
  else
    printf '  \033[31m✗\033[0m %-22s exit=%s\n' "$name" "$code"
    echo "$out" | tail -15 | sed 's/^/      /'
    failed+=("$name")
  fi
}

run "ruff check"   uv run ruff check .
run "ruff format"  uv run ruff format --check .
run "mypy strict"  uv run mypy src tests
run "pytest"       uv run pytest -q
run "pip-audit"    uv run --with pip-audit pip-audit

if [ ${#failed[@]} -ne 0 ]; then
  printf '\n\033[31mFAILED:\033[0m %s\n' "${failed[*]}"
  exit 1
fi
printf '\nAll five gates green.\n'
