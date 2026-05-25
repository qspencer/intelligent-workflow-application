#!/usr/bin/env bash
# Fire every paper in a JSON array through the workflow, one at a time.
#
# Usage (from repo root):
#   examples/research_paper_triage/scripts/run_batch.sh /tmp/papers.json
#
# Generate the input via fetch_papers.sh:
#   examples/research_paper_triage/scripts/fetch_papers.sh 50 > /tmp/papers.json
#   examples/research_paper_triage/scripts/run_batch.sh /tmp/papers.json
#
# Env vars: same as run_one.sh.
#
# Cost note: each paper is one Bedrock call. Abstracts run ~1–3KB so a
# 50-paper batch costs roughly $0.01–0.02 at Haiku 4.5.

set -euo pipefail

papers_path="${1:?Usage: $0 <path-to-papers-json-array>}"
repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"

count=0
errors=0
while read -r paper; do
  count=$((count + 1))
  echo "--- paper ${count} ---"
  if ! (cd "${repo_root}/backend" && uv run python tools/fire.py \
        --definition "${repo_root}/examples/research_paper_triage/workflow.yaml" \
        --trigger "$paper"); then
    errors=$((errors + 1))
  fi
  echo
done < <(jq -c '.[]' "${papers_path}")

echo "Done: ${count} papers processed, ${errors} failed."
[ "${errors}" -eq 0 ]
