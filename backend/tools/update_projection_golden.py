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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from _projection_corpus import CORPUS
from workflow_platform.trace_projection import (
    PROJECTION_SCHEMA_VERSION,
    PROJECTOR_VERSION,
    redact_tool_data,
)

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "tests" / "golden"


def main() -> int:
    out = GOLDEN_DIR / f"projection_v{PROJECTOR_VERSION}.json"
    if out.exists():
        print(f"REFUSED: {out.name} already exists.")
        print("A released version's golden output is immutable. If the projector's")
        print("output changed, bump PROJECTOR_VERSION and re-run; if it did not,")
        print("there is nothing to write.")
        return 1
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "projector_version": PROJECTOR_VERSION,
        "projection_schema_version": PROJECTION_SCHEMA_VERSION,
        "cases": [
            {
                "id": cid,
                "kind": kind,
                "input": inp,
                "expected": redact_tool_data(inp, admin=False, kind=kind),
            }
            for cid, kind, inp in CORPUS
        ],
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out.name}: {len(payload['cases'])} cases at projector v{PROJECTOR_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
