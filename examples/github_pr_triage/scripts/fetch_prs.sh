#!/usr/bin/env bash
# Fetch closed PRs from a public GitHub repo via the `gh` CLI.
#
# Usage:
#   scripts/fetch_prs.sh <owner>/<repo> [count] > prs.json
#
# Example:
#   scripts/fetch_prs.sh anthropics/anthropic-sdk-python 50 > /tmp/prs.json
#
# Requires `gh` CLI authenticated (`gh auth login`). Public repos work
# without scopes; private repos need a token with `repo` scope.

set -euo pipefail

repo="${1:?Usage: $0 <owner>/<repo> [count]}"
count="${2:-50}"

gh api "repos/${repo}/pulls?state=closed&per_page=${count}"
