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
import sys
from pathlib import Path
from typing import Any

import pytest

from workflow_platform.trace_projection import (
    PROJECTION_SCHEMA_VERSION,
    PROJECTOR_VERSION,
    SCHEMAS,
    project_audit_detail_at_rest,
    redact_tool_data,
)
from workflow_platform.trace_rehydrate import verify_projection_agreement

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _project(case: dict[str, Any]) -> Any:
    """Route a frozen case through ITS entry point (R8 P2).

    Every check used `redact_tool_data`, so an action-dispatched case would
    have been re-projected through the wrong door — and the case named
    `audit_detail.tool_call` never reached `safe_tool_call` at all."""
    if case.get("action"):
        return project_audit_detail_at_rest(case["action"], case["input"])
    return redact_tool_data(case["input"], admin=False, kind=case["kind"])


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
        actual = _project(case)
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
        once = _project(case)
        twice = _project({**case, "input": once})
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


def test_the_corpus_actually_reaches_tool_call_projection() -> None:
    """R8 P2: a case NAMED `audit_detail.tool_call` passed a flat record
    through the generic audit-detail schema and froze `{"_withheld_keys":
    true}` — `safe_tool_call` was never called, so tool-call projection could
    change without this guard noticing. A name is not coverage; this asserts
    the function is actually invoked."""
    import workflow_platform.trace_projection as tp

    fixture = _fixtures()[PROJECTOR_VERSION]
    calls: list[Any] = []
    original = tp.safe_tool_call

    def _spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return original(*args, **kwargs)

    tp.safe_tool_call = _spy
    try:
        for case in fixture["cases"]:
            _project(case)
    finally:
        tp.safe_tool_call = original

    assert calls, "no golden case reaches safe_tool_call — tool-call projection is unguarded"
    # …and via BOTH doors: the nested schema position and the action dispatch
    assert any(c.get("action") == "tool_call" for c in fixture["cases"]), (
        "no case exercises the action-dispatched at-rest tool-call path"
    )
    assert any("tool_calls" in _dumps_case(c) for c in fixture["cases"]), (
        "no case exercises tool calls in their nested schema position"
    )


def _dumps_case(case: dict[str, Any]) -> str:
    return json.dumps(case.get("input", {}), sort_keys=True, default=str)


def test_historical_fixtures_are_authentic_against_their_declared_source() -> None:
    """R8 P2: a historical fixture must be REGENERABLE from the commit it
    names, or the degradation check passes without establishing that the
    expected historical output is real.

    The previous v3 fixture named `4d9f39c` — the remediation written AFTER
    the round-6 return, not the code the reviewer held — and disagreed with
    the actual archive on the forged-marker case and the schema version. This
    re-derives each historical fixture from its declared commit and compares.

    Skipped where git is unavailable (inside an extracted review package, for
    instance), because the claim it checks is about OUR repository.
    """
    import importlib.util
    import subprocess
    import sys

    for version, fixture in _fixtures().items():
        source = str(fixture.get("generated_from", ""))
        if version == PROJECTOR_VERSION:
            # Round-12: the CURRENT version used to be skipped outright, so a
            # wrong stamp stayed latent until the NEXT bump and then surfaced
            # as "v7 names a commit declaring 6". v7 AND v8 both shipped that
            # way. It is legitimately unverifiable only while the bump is
            # uncommitted, and the generator now says so in those words.
            if source.startswith("UNVERIFIED"):
                continue
            assert source.startswith("git "), (
                f"v{version} is current and names no source commit; the generator "
                "records 'UNVERIFIED — …' for that, so this stamp is neither"
            )
        source = str(fixture.get("generated_from", ""))
        commit = source.split()[1] if source.startswith("git ") else ""
        assert commit, f"v{version} fixture does not name its source commit"

        try:
            blob = subprocess.run(
                ["git", "show", f"{commit}:backend/src/workflow_platform/trace_projection.py"],
                capture_output=True,
                text=True,
                check=True,
                cwd=Path(__file__).resolve().parents[2],
            ).stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            pytest.skip(f"git unavailable or {commit} not present; cannot re-derive v{version}")

        tmp = Path(__file__).resolve().parent / f"_historical_{version}.py"
        tmp.write_text(blob)
        try:
            spec = importlib.util.spec_from_file_location(f"_hist_{version}", tmp)
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            sys.modules[f"_hist_{version}"] = mod
            spec.loader.exec_module(mod)

            assert str(mod.PROJECTOR_VERSION) == version, (
                f"v{version} fixture names {commit}, which declares "
                f"PROJECTOR_VERSION={mod.PROJECTOR_VERSION!r}"
            )
            assert fixture["projection_schema_version"] == mod.PROJECTION_SCHEMA_VERSION, (
                f"v{version} fixture records schema "
                f"{fixture['projection_schema_version']}, source declares "
                f"{mod.PROJECTION_SCHEMA_VERSION}"
            )
            for case in fixture["cases"]:
                if case.get("action"):
                    actual = mod.project_audit_detail_at_rest(case["action"], case["input"])
                else:
                    actual = mod.redact_tool_data(case["input"], admin=False, kind=case["kind"])
                assert actual == case["expected"], (
                    f"v{version} case {case['id']} does not match what {commit} produces:\n"
                    f"  fixture: {json.dumps(case['expected'], sort_keys=True)}\n"
                    f"  source:  {json.dumps(actual, sort_keys=True)}"
                )
        finally:
            sys.modules.pop(f"_hist_{version}", None)
            tmp.unlink(missing_ok=True)


def test_the_generator_can_create_a_fixture_for_a_NEW_version(tmp_path: Path) -> None:
    """R9 P2: the APPEND path was action-aware and the CREATE path was not, so
    the first 4-element corpus entry raised "too many values to unpack" and no
    fixture was written — a new projector version could not be frozen at all.
    Only the append path had a test."""
    import importlib.util

    tools = Path(__file__).resolve().parents[1] / "tools" / "update_projection_golden.py"
    spec = importlib.util.spec_from_file_location("_gen", tools)
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    sys.modules["_gen"] = gen
    spec.loader.exec_module(gen)

    gen.GOLDEN_DIR = tmp_path  # type: ignore[attr-defined]
    assert gen.main() == 0, "generating into an empty directory failed"
    written = list(tmp_path.glob("projection_v*.json"))
    assert len(written) == 1
    data = json.loads(written[0].read_text())
    assert data["cases"], "wrote a fixture with no cases"
    assert any(c.get("action") for c in data["cases"]), (
        "the created fixture lost the action-dispatched cases"
    )


def test_every_corpus_case_is_frozen_in_the_current_golden() -> None:
    """A corpus case that is not in the golden protects NOTHING.

    Found by the round-12 pre-package corpus review (protocol step 6). THE
    GUARD above iterates the FIXTURE's cases, so adding a case to the corpus
    and forgetting to regenerate leaves it silently unchecked — the widening
    looks done and buys nothing. Three cases covering the fields the at-rest
    tightening declared sat in exactly that state until this was noticed.
    """
    from tests._projection_corpus import CORPUS

    frozen = {c["id"] for c in _fixtures()[PROJECTOR_VERSION]["cases"]}
    missing = sorted({c[0] for c in CORPUS} - frozen)
    assert not missing, (
        f"these corpus cases are not frozen in projector v{PROJECTOR_VERSION}, so the "
        f"guard never evaluates them: {missing}\n"
        "    uv run python tools/update_projection_golden.py"
    )
