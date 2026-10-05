#!/usr/bin/env python3
"""Dispute the learned-memory facts that were distilled from the triage
agent's OWN verdicts — the self-reinforcing loop found 2026-10-04.

Until then every email-triage run wrote a second observation, "The triage
agent classified the email from X as Y … Reason: …", authored `system`,
`derived_from: third_party`. veracium distilled it into facts about the
sender ("flagged as malicious sender in prior runs", "source_reliable", …),
and recall injected those facts into the next classification of that sender
— so the classifier read its own past opinions back as knowledge, and one
early mistake (Caraway → spam) re-confirmed itself on every later email.
The observation is gone from the workflows; this cleans up what it left.

Selection is exact: ACTIVE edges whose provenance is `author_of_evidence:
system` AND `derived_from: third_party` — the verdict observation's
signature. The email-content observation is `third_party` with no
`derived_from`, and is untouched.

`dispute` is veracium's non-destructive invalidation: the edge leaves every
assertable surface (recall included) and stays as queryable history, and
veracium records the dispute itself as a system episode with actor and
reason. Correct evidence can bring a fact back through `remember()`.

Only `org:`-namespaced keys are processed — those are what recall reads.

Usage (STOP the service first; back up the store):
    uv run python tools/dispute_verdict_facts.py --db .memory/learned.db          # dry run
    uv run python tools/dispute_verdict_facts.py --db .memory/learned.db --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

REASON = (
    "derived from the triage agent's own verdict (self-reinforcing loop, "
    "2026-10-04): the classifier's opinion is not evidence about the sender"
)

_SELECT = """
SELECT id, user_id, relation FROM edges
WHERE active = 1
  AND user_id LIKE 'org:%'
  AND json_extract(json, '$.provenance.author_of_evidence') = 'system'
  AND json_extract(json, '$.provenance.derived_from') = 'third_party'
"""


def select_verdict_edges(db: Path) -> list[tuple[str, str, str]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [(r[0], r[1], r[2]) for r in con.execute(_SELECT)]
    finally:
        con.close()


def _no_llm(*args: Any, **kwargs: Any) -> str:
    raise RuntimeError("dispute must not call a model")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--apply", action="store_true", help="dispute them; otherwise report")
    args = parser.parse_args()
    db = Path(args.db)
    edges = select_verdict_edges(db)
    print(f"verdict-derived active edges: {len(edges)}")
    for ns, n in Counter(ns for _, ns, _ in edges).most_common():
        print(f"  {ns}: {n}")
    for rel, n in Counter(rel for _, _, rel in edges).most_common(8):
        print(f"    {rel}: {n}")
    if not args.apply or not edges:
        print("dry run — nothing changed" if not args.apply else "nothing to do")
        return 0

    from veracium import Memory, MemoryConfig

    memory = Memory(
        llm=_no_llm, config=MemoryConfig(db_path=str(db), wiki_recompile_after_writes=0)
    )
    failed = 0
    try:
        for i, (edge_id, ns, _) in enumerate(edges, 1):
            try:
                memory.dispute(ns, edge_id, reason=REASON, actor="user")
            except Exception as exc:  # report and keep going; the recount says what is left
                failed += 1
                print(f"  could not dispute {edge_id}: {type(exc).__name__}: {exc}")
            if i % 500 == 0:
                print(f"  {i}/{len(edges)}")
    finally:
        memory.close()
    left = len(select_verdict_edges(db))
    print(f"disputed: {len(edges) - failed}   failed: {failed}   still active: {left}")
    return 0 if left == 0 and failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
