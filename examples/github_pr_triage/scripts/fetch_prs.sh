#!/usr/bin/env bash
# Fetch closed PRs from a public GitHub repo, enrich each with the per-PR
# detail call (the list endpoint returns null for additions/deletions/
# changed_files), and slim to just the fields the triage agent uses.
#
# Usage:
#   scripts/fetch_prs.sh <owner>/<repo> [count] > prs.json
#
# Example:
#   scripts/fetch_prs.sh anthropics/anthropic-sdk-python 50 > /tmp/prs.json
#
# Requires `gh` CLI authenticated (`gh auth login`). Public repos work
# without scopes; private repos need a token with `repo` scope. Cost: one
# `gh api` call to list + one per PR to enrich. Well under GitHub's
# 5000/hr authenticated rate limit at typical batch sizes.

set -euo pipefail

repo="${1:?Usage: $0 <owner>/<repo> [count]}"
count="${2:-50}"

# Slim each PR to just the fields the rubric in agent_memory.md actually
# reads. Drops _links, commits_url, head/base repo metadata, avatar URLs,
# label/reviewer arrays, etc. — typically ~95% of the raw payload.
# Cuts both Bedrock input tokens and the workflow_instances.trigger_payload
# JSONB size in Postgres.
SLIM='{
  number,
  title,
  body,
  user: {login: .user.login, type: .user.type},
  author_association,
  additions,
  deletions,
  changed_files,
  base: {ref: .base.ref, repo: {full_name: .base.repo.full_name}},
  head: {ref: .head.ref}
}'

gh api "repos/${repo}/pulls?state=closed&per_page=${count}" \
  | jq -r '.[].number' \
  | while read -r num; do
      gh api "repos/${repo}/pulls/${num}" | jq -c "${SLIM}"
    done \
  | jq -s '.'
