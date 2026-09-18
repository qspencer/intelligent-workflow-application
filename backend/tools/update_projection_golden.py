"""Freeze the current projector's output as this version's golden fixture.

Run ONLY when a projection change is intended:

    uv run python tools/update_projection_golden.py

It writes `tests/golden/projection_v<PROJECTOR_VERSION>.json` and REFUSES to
overwrite one that already exists. That refusal is the guard's whole point: a
released version's expected output is immutable, so changing what the
projector emits forces a NEW version rather than a quiet edit to the old
answer — the failure that shipped twice, in rounds 6 and 7.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from _projection_corpus import CORPUS
from workflow_platform.trace_projection import (
    PROJECTION_SCHEMA_VERSION,
    PROJECTOR_VERSION,
    project_audit_detail_at_rest,
    redact_tool_data,
)

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "tests" / "golden"


def _project_case(case: tuple) -> object:
    """Route a corpus case through its entry point: a 4th element names an
    audit ACTION and goes through the at-rest dispatcher instead."""
    _cid, kind, inp = case[0], case[1], case[2]
    action = case[3] if len(case) > 3 else None
    if action is not None:
        return project_audit_detail_at_rest(action, inp)
    return redact_tool_data(inp, admin=False, kind=kind)


def main() -> int:
    out = GOLDEN_DIR / f"projection_v{PROJECTOR_VERSION}.json"
    existing = json.loads(out.read_text())["cases"] if out.exists() else None
    if existing is not None:
        # R8 P2 refinement: immutability applies to a frozen case's EXPECTED
        # OUTPUT, not to the SET of cases. Coverage must be able to grow
        # without pretending the projector changed — but an existing entry may
        # never move, so appending re-verifies every one of them first.
        existing_source = json.loads(out.read_text()).get("generated_from", "")
        by_id = {c["id"]: c for c in existing}
        drifted = [
            c["id"]
            for c in existing
            if _project_case(
                (c["id"], c["kind"], c["input"], *([c["action"]] if c.get("action") else []))
            )
            != c["expected"]
        ]
        if drifted:
            print(f"REFUSED: {out.name} has cases whose output has CHANGED: {drifted}")
            print("A released version's frozen output is immutable. Bump PROJECTOR_VERSION.")
            return 1
        added = [c for c in CORPUS if c[0] not in by_id]
        if not added:
            print(f"{out.name} is current: {len(existing)} cases, none drifted, nothing to add.")
            return 0
        cases = existing + [
            {
                "id": c[0],
                "kind": c[1],
                "input": c[2],
                **({"action": c[3]} if len(c) > 3 else {}),
                "expected": _project_case(c),
            }
            for c in added
        ]
        payload = {
            "projector_version": PROJECTOR_VERSION,
            "projection_schema_version": PROJECTION_SCHEMA_VERSION,
            "generated_from": existing_source,
            "cases": cases,
        }
        out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(
            f"appended {len(added)} new case(s) to {out.name}; "
            f"{len(existing)} existing cases re-verified unchanged"
        )
        return 0
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    # R8 P2: record the commit, so a superseded fixture can later be
    # RE-DERIVED from its source and shown authentic rather than trusted.
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    payload = {
        "projector_version": PROJECTOR_VERSION,
        "projection_schema_version": PROJECTION_SCHEMA_VERSION,
        "generated_from": f"git {head} — the commit that froze this version" if head else "",
        # R9 P2: the append path was action-aware and this one was not, so
        # creating a fixture for a NEW version raised "too many values to
        # unpack" on the first 4-element corpus entry and wrote nothing.
        # Both paths share `_project_case` now.
        "cases": [
            {
                "id": c[0],
                "kind": c[1],
                "input": c[2],
                **({"action": c[3]} if len(c) > 3 else {}),
                "expected": _project_case(c),
            }
            for c in CORPUS
        ],
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out.name}: {len(payload['cases'])} cases at projector v{PROJECTOR_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
