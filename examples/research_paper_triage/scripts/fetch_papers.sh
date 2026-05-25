#!/usr/bin/env bash
# Fetch arXiv papers matching the agent-memory / context search query,
# parse the Atom feed, and emit a slim JSON array matching the trigger
# payload shape the triage workflow expects.
#
# Usage:
#   scripts/fetch_papers.sh [count] [search_query] > papers.json
#
# Defaults:
#   count = 50
#   search_query = AI agent memory + context, recent first
#
# Example:
#   scripts/fetch_papers.sh 50 > /tmp/papers.json
#   scripts/fetch_papers.sh 25 'abs:"long context" AND cat:cs.CL' > /tmp/lc.json
#
# arXiv API: http://export.arxiv.org/api/query
# Rate-limit: be nice — the API requests at most 1 request every 3 seconds
# for bulk usage. We make one call per script invocation, so OK.

set -euo pipefail

count="${1:-50}"
query="${2:-(abs:agent AND (abs:memory OR abs:\"long context\" OR abs:\"retrieval augmented\")) AND (cat:cs.AI OR cat:cs.CL OR cat:cs.LG)}"

python3 - "$count" "$query" <<'PYEOF'
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

count = int(sys.argv[1])
query = sys.argv[2]

params = urllib.parse.urlencode(
    {
        "search_query": query,
        "start": 0,
        "max_results": count,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
)
url = f"http://export.arxiv.org/api/query?{params}"
req = urllib.request.Request(url, headers={"User-Agent": "workflow-platform-paper-triage/0.1"})
data = urllib.request.urlopen(req, timeout=30).read()

root = ET.fromstring(data)
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

papers = []
for entry in root.findall("atom:entry", NS):
    arxiv_id_full = (entry.findtext("atom:id", namespaces=NS) or "").strip()
    arxiv_id = arxiv_id_full.split("/abs/")[-1] if "/abs/" in arxiv_id_full else arxiv_id_full
    primary = entry.find("arxiv:primary_category", NS)
    paper = {
        "id": arxiv_id,
        "title": " ".join((entry.findtext("atom:title", namespaces=NS) or "").split()),
        "abstract": " ".join((entry.findtext("atom:summary", namespaces=NS) or "").split()),
        "authors": [
            (a.findtext("atom:name", namespaces=NS) or "").strip()
            for a in entry.findall("atom:author", NS)
        ],
        "primary_category": primary.attrib.get("term") if primary is not None else None,
        "categories": [c.attrib.get("term") for c in entry.findall("atom:category", NS)],
        "published": entry.findtext("atom:published", namespaces=NS),
        "comment": " ".join((entry.findtext("arxiv:comment", namespaces=NS) or "").split()),
    }
    papers.append(paper)

print(json.dumps(papers, indent=2))
PYEOF
