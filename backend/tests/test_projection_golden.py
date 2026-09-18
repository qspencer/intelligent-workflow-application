"""The golden-fixture version guard (reviewer's §4.3, built after R7).

WHY THIS EXISTS. Twice in consecutive rounds the projector's OUTPUT changed
while `PROJECTOR_VERSION` stayed put — round 6 → 7 most recently — and each
time the consequence was the same: a record written by the older projector,
re-projected by the newer one, DISAGREED and was reported as TAMPERING rather
than degrading. Both times a human was supposed to remember to bump a
constant, and both times a human did not. A comment saying "bump this" has now
failed twice; this fails the build instead.

The rule: **a released version's expected output is immutable.** Change what
the projector emits and the golden file for the current version stops matching,
which is only resolvable by bumping the version and freezing a new one
(`uv run python tools/update_projection_golden.py`, which refuses to overwrite).

Coverage follows the reviewer's list: every asset kind, repeated projection,
reconstruction of historical records, and schema-version consistency. Golden
tests cannot establish behaviour for every input — the generative properties in
`test_trace_boundary_properties.py` carry that load; this one pins that what we
ALREADY decided stays decided.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from workflow_platform.trace_projection import (
    PROJECTION_SCHEMA_VERSION,
    PROJECTOR_VERSION,
    SCHEMAS,
    redact_tool_data,
)
from workflow_platform.trace_rehydrate import verify_projection_agreement

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _fixtures() -> dict[str, dict[str, Any]]:
    out = {}
    for p in sorted(GOLDEN_DIR.glob("projection_v*.json")):
        data = json.loads(p.read_text())
        out[str(data["projector_version"])] = data
    return out


def test_a_golden_fixture_exists_for_the_current_version() -> None:
    """A new projector version must freeze its own expected output."""
    fx = _fixtures()
    assert PROJECTOR_VERSION in fx, (
        f"no golden fixture for projector v{PROJECTOR_VERSION}. If you bumped the "
        f"version, freeze its output:\n"
        f"    uv run python tools/update_projection_golden.py"
    )


def test_current_projection_matches_its_frozen_golden() -> None:
    """THE GUARD. If this fails, the projector's output changed.

    That is not a test to update — it is a VERSION to bump. Editing the frozen
    answer for a released version is the exact move that made round-6 records
    read as tampering under round 7."""
    fixture = _fixtures()[PROJECTOR_VERSION]
    drifted = []
    for case in fixture["cases"]:
        actual = redact_tool_data(case["input"], admin=False, kind=case["kind"])
        if actual != case["expected"]:
            drifted.append(
                f"    {case['id']} ({case['kind']})\n"
                f"      frozen:  {json.dumps(case['expected'], sort_keys=True)}\n"
                f"      current: {json.dumps(actual, sort_keys=True)}"
            )
    assert not drifted, (
        f"projector v{PROJECTOR_VERSION} no longer produces its frozen output:\n"
        + "\n".join(drifted)
        + "\n\n  The output changed, so the VERSION must change. Bump "
        "PROJECTOR_VERSION, then:\n    uv run python tools/update_projection_golden.py\n"
        "  Do NOT edit the existing fixture: stored records were written by it."
    )


def test_the_golden_corpus_covers_every_asset_kind() -> None:
    """A kind with no frozen case is a kind the guard cannot protect."""
    fixture = _fixtures()[PROJECTOR_VERSION]
    covered = {c["kind"] for c in fixture["cases"]}
    missing = set(SCHEMAS) - covered
    assert not missing, f"asset kinds absent from the golden corpus: {sorted(missing)}"


def test_every_golden_case_is_a_projection_fixed_point() -> None:
    """Repeated projection must not move — the backfill and its verifier rely
    on it, and a marker that is stripped-and-rewritten on re-projection made an
    already-backfilled row look raw forever (R5)."""
    fixture = _fixtures()[PROJECTOR_VERSION]
    for case in fixture["cases"]:
        once = redact_tool_data(case["input"], admin=False, kind=case["kind"])
        twice = redact_tool_data(once, admin=False, kind=case["kind"])
        assert twice == once, f"{case['id']}: not idempotent\n  once={once}\n  twice={twice}"


@pytest.mark.parametrize("old_version", sorted(set(_fixtures()) - {PROJECTOR_VERSION}) or [None])
def test_historical_records_degrade_and_never_read_as_tampering(old_version: str | None) -> None:
    """Every SUPERSEDED version's frozen output must still reconstruct as
    `unsupported` — degraded, explicitly — not `mismatch`.

    This is the property both version collisions actually broke (criterion 17:
    a projector bump must not make pre-change rows read corrupt)."""
    if old_version is None:
        pytest.skip("no superseded version fixtures yet (v4 is the first frozen)")
    fixture = _fixtures()[old_version]
    for case in fixture["cases"]:
        if case["kind"] != "step_output":
            continue  # the agreement predicate is defined over step outputs
        assert (
            verify_projection_agreement(case["input"], case["expected"], old_version)
            == "unsupported"
        ), f"v{old_version} record {case['id']} does not degrade cleanly"


def test_schema_version_consistency() -> None:
    """One authority for both stamps, and the schema version recorded in the
    fixture must match the build that froze it (R6/R7)."""
    from workflow_platform import trace_projection as proj
    from workflow_platform.persistence import models

    assert models.PROJECTION_SCHEMA_VERSION is proj.PROJECTION_SCHEMA_VERSION
    assert models.PROJECTOR_VERSION is proj.PROJECTOR_VERSION
    fixture = _fixtures()[PROJECTOR_VERSION]
    assert fixture["projection_schema_version"] == PROJECTION_SCHEMA_VERSION, (
        "the frozen fixture was written under a different schema version; if the "
        "projected STRUCTURE changed, both stamps move together"
    )
