#!/usr/bin/env bash
# Fire every PR in a JSON array through the workflow, one at a time.
#
# Usage (from repo root):
#   examples/github_pr_triage/scripts/run_batch.sh /tmp/prs.json
#
# Generate the input via fetch_prs.sh:
#   examples/github_pr_triage/scripts/fetch_prs.sh anthropics/anthropic-sdk-python 50 > /tmp/prs.json
#   examples/github_pr_triage/scripts/run_batch.sh /tmp/prs.json
#
# Env vars: same as run_one.sh.
#
# Cost note: each PR is one Bedrock call. At Claude Haiku 4.5, expect
# roughly $0.0002 per PR ($0.02 per 100). Bigger PRs cost more because
# the body is in the prompt.

set -euo pipefail

prs_path="${1:?Usage: $0 <path-to-prs-json-array>}"
repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"

count=0
errors=0
while read -r pr; do
  count=$((count + 1))
  echo "--- PR ${count} ---"
  if ! (cd "${repo_root}/backend" && uv run python tools/fire.py \
        --definition "${repo_root}/examples/github_pr_triage/workflow.yaml" \
        --trigger "$pr"); then
    errors=$((errors + 1))
  fi
  echo
done < <(jq -c '.[]' "${prs_path}")

echo "Done: ${count} PRs processed, ${errors} failed."
[ "${errors}" -eq 0 ]
