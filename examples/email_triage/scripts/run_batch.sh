#!/usr/bin/env bash
# Fire every fixture JSON in examples/email_triage/fixtures/ through the
# workflow. Useful for iterating on the agent_memory rubric: edit the
# rubric, run the batch, query step_executions in Postgres for the
# results, repeat.
#
# Usage (from repo root):
#   examples/email_triage/scripts/run_batch.sh
#
# Same env vars as run_one.sh.

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"
fixtures_dir="${repo_root}/examples/email_triage/fixtures"
script_dir="${repo_root}/examples/email_triage/scripts"

failures=0
total=0
for fixture in "${fixtures_dir}"/*.json; do
  total=$((total + 1))
  echo
  echo "==> $(basename "${fixture}")"
  if ! "${script_dir}/run_one.sh" "${fixture}"; then
    failures=$((failures + 1))
  fi
done

echo
echo "================================================================"
echo "  ${total} fixtures fired; ${failures} failed."
echo "================================================================"
exit "${failures}"
