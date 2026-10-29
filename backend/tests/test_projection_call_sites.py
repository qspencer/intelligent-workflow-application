"""Every site that projects a trace asset, DERIVED from source and declared.

G29 gave `redact_tool_data` a per-call `vocab` argument. Three write sites
supplied it; fifteen consumers could not, because they hold a row or an audit
detail and not a definition — and each of them silently produced a different
projection from the one the write path stored. Eleven tests covered the new
branch and none noticed, because every one of them drove the write path.

The routine that would have caught it (from the veracium dev seat): derive the
POPULATION of sites an invariant governs mechanically, and refuse an undeclared
one. A new projection site, or an existing one that starts passing a
vocabulary, fails here until someone states which side of that line it is on.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "workflow_platform"

_ENTRY_POINTS = frozenset(
    {
        "redact_tool_data",
        "output_has_raw",
        "project_audit_detail_at_rest",
        "project_audit_detail_final",
    }
)

#: Roles. A WRITE site produces the stored projection; every other role holds
#: only what was stored and must be able to reproduce it from that alone.
WRITE = "write"  # projects raw onto the operational row at run time
READ = "read"  # below-grant read surfaces
VERIFY = "verify"  # agreement predicate, zero-raw verifier, vault decision
MIGRATE = "migrate"  # backfill and at-rest migration
AUDIT = "audit"  # audit-detail projection at append / for display
INTERNAL = "internal"  # one projection entry point delegating to another

#: (module, innermost function, entry point) -> (role, supplies a vocabulary).
#:
#: Only WRITE sites may supply one, and today what they supply is always None:
#: `WorkflowDefinition` refuses a non-null `released_fields` on every path
#: (`test_g29_withdrawn.py`). If that ever changes, every non-WRITE row below
#: becomes a consumer that disagrees with the write path — which is the
#: defect, and the reason `test_every_golden_case_is_a_projection_fixed_point`
#: re-projects with NO vocabulary.
_DECLARED: dict[tuple[str, str, str], tuple[str, bool]] = {
    ("engine/executor.py", "_mark_instance", "redact_tool_data"): (WRITE, True),
    ("engine/executor.py", "_project_step_output_onto", "redact_tool_data"): (WRITE, True),
    ("engine/executor.py", "_run_step_once", "redact_tool_data"): (WRITE, True),
    ("api/workflows.py", "explain_step", "redact_tool_data"): (READ, False),
    ("api/workflows.py", "get_instance", "redact_tool_data"): (READ, False),
    ("api/redaction.py", "project_audit_detail", "project_audit_detail_final"): (READ, False),
    ("trace_rehydrate.py", "verify_projection_agreement", "redact_tool_data"): (VERIFY, False),
    (
        "trace_rehydrate.py",
        "verify_audit_projection_agreement",
        "project_audit_detail_at_rest",
    ): (VERIFY, False),
    ("trace_migration.py", "_has_raw", "redact_tool_data"): (VERIFY, False),
    ("trace_vault.py", "output_has_raw", "redact_tool_data"): (VERIFY, False),
    ("trace_vault.py", "record_step_output", "output_has_raw"): (VERIFY, False),
    ("trace_vault.py", "audit_detail_has_raw", "project_audit_detail_final"): (VERIFY, False),
    ("trace_migration.py", "backfill_instance", "redact_tool_data"): (MIGRATE, False),
    (
        "trace_migration.py",
        "migrate_audit_details",
        "project_audit_detail_at_rest",
    ): (MIGRATE, False),
    ("audit_writer.py", "append", "project_audit_detail_at_rest"): (AUDIT, False),
    ("tools/escalation.py", "execute", "project_audit_detail_at_rest"): (AUDIT, False),
    (
        "trace_projection.py",
        "project_audit_detail_at_rest",
        "project_audit_detail_final",
    ): (INTERNAL, False),
    ("trace_projection.py", "project_audit_detail_final", "redact_tool_data"): (INTERNAL, False),
}


def _derived() -> dict[tuple[str, str, str], set[bool]]:
    """Every call to a projection entry point, keyed by its INNERMOST function
    (route handlers are nested inside a router factory), with whether each call
    passes `vocab=`."""
    found: dict[tuple[str, str, str], set[bool]] = {}

    def visit(node: ast.AST, rel: str, enclosing: str | None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = node.name
        if isinstance(node, ast.Call) and enclosing is not None:
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in _ENTRY_POINTS:
                passes = any(k.arg == "vocab" for k in node.keywords)
                found.setdefault((rel, enclosing, name), set()).add(passes)
        for child in ast.iter_child_nodes(node):
            visit(child, rel, enclosing)

    for path in sorted(SRC.rglob("*.py")):
        visit(ast.parse(path.read_text()), str(path.relative_to(SRC)), None)
    return found


def test_every_projection_site_is_declared() -> None:
    derived = set(_derived())
    declared = set(_DECLARED)
    assert derived - declared == set(), (
        f"undeclared projection site(s): {sorted(derived - declared)}. Declare each "
        f"with its role. If it is not a WRITE site it holds only what was stored, and "
        f"must reproduce the stored projection from that alone."
    )
    assert declared - derived == set(), f"declared site(s) gone: {sorted(declared - derived)}"


def test_each_site_supplies_a_vocabulary_exactly_as_declared() -> None:
    derived = _derived()
    wrong = {
        site: (sorted(passes), _DECLARED[site][1])
        for site, passes in derived.items()
        if site in _DECLARED and passes != {_DECLARED[site][1]}
    }
    assert not wrong, f"vocab= usage differs from the declaration: {wrong}"


def test_only_write_sites_may_supply_a_vocabulary() -> None:
    """A consumer that supplies one is reading with something the stored row
    does not carry — the G29 defect from the other side."""
    offenders = [s for s, (role, vocab) in _DECLARED.items() if vocab and role != WRITE]
    assert not offenders, f"non-write sites supplying a vocabulary: {offenders}"
