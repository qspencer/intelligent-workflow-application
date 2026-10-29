"""G29 controls: sabotage each detector with the defect it claims to catch.

Run from `backend/`:   .venv/bin/python ../docs/review-artifacts/g29_controls.py

A detector that has never been seen to fail is a claim, not a control. Each
entry plants its defect on the check's OWN input, asserts the targeted test now
fails (exit 1), restores the file, and asserts it passes again (exit 0).

A sabotaged Python file is parsed with the project interpreter first, so a
syntax break cannot be read as a control firing — Python 3.14 accepts PEP 758
`except A, B:`, which the system 3.12 rejects, and an early version of this
harness mistook exactly that for a break. Only `.py` files are parsed: a later
version parsed the Markdown guide too, and recorded three controls as syntax
failures instead of running them.
"""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
CONTROLS = [
    (
        "validator disabled - a declaration loads again",
        "src/workflow_platform/workflow/definition.py",
        "        if self.released_fields is not None:\n            raise ValueError(",
        "        if False:\n            raise ValueError(",
        "tests/test_g29_withdrawn.py",
    ),
    (
        "clone reverted to model_copy - skips the validator",
        "src/workflow_platform/api/workflows.py",
        "definition = WorkflowDefinition.model_validate(\n"
        '                    {**source.model_dump(), "id": new_id, "name": new_name}\n'
        "                )",
        'definition = source.model_copy(deep=True, update={"id": new_id, "name": new_name})',
        "tests/test_g29_withdrawn.py::"
        "test_clone_cannot_propagate_a_declaration_from_an_unvalidated_source",
    ),
    (
        "a persisting site goes undeclared",
        "tests/test_g29_withdrawn.py",
        '    ("api/workflows.py", "import_workflow"): "test_import_refuses_a_declaration",\n',
        "",
        "tests/test_g29_withdrawn.py::test_every_definition_persisting_site_is_declared",
    ),
    (
        "fixed-point second pass handed the vocabulary (the pre-G29 form)",
        "tests/test_projection_golden.py",
        'twice = _project({**case, "input": once, "vocab": None})',
        'twice = _project({**case, "input": once})',
        "tests/test_projection_golden.py::test_every_golden_case_is_a_projection_fixed_point",
    ),
    (
        "the agreement predicate starts passing a vocabulary (G29 from the consumer side)",
        "src/workflow_platform/trace_rehydrate.py",
        'if redact_tool_data(raw, admin=False, kind="step_output") == stored_safe',
        'if redact_tool_data(raw, admin=False, kind="step_output", vocab=None) == stored_safe',
        "tests/test_projection_call_sites.py",
    ),
    (
        "a projection site goes undeclared",
        "tests/test_projection_call_sites.py",
        '    ("trace_vault.py", "output_has_raw", "redact_tool_data"): (VERIFY, False),\n',
        "",
        "tests/test_projection_call_sites.py::test_every_projection_site_is_declared",
    ),
    (
        "a figure in the guide is typed wrong (51.3 -> 53.0 bits)",
        "../docs/TRACE_G29_REVIEW_GUIDE.md",
        "**51.3 bits per run**",
        "**53.0 bits per run**",
        "tests/test_review_figures.py",
    ),
    (
        "an unregistered figure appears in the guide's prose",
        "../docs/TRACE_G29_REVIEW_GUIDE.md",
        "Two specific things to push on:",
        "Two specific things to push on, across 42 surfaces:",
        "tests/test_review_figures.py::test_no_figure_in_the_guide_is_unaccounted_for",
    ),
    (
        "the shipped evidence disagrees with the guide (78 -> 79 rows)",
        "../docs/archives/GATE_OUTPUT_G29.txt",
        "v23 rows today: 78;",
        "v23 rows today: 79;",
        "tests/test_review_figures.py",
    ),
]


def pytest(target: str) -> int:
    r = subprocess.run(
        ["uv", "run", "pytest", target, "-q", "-p", "no:warnings"], capture_output=True, text=True
    )
    return r.returncode


ok = True
for label, rel, old, new, target in CONTROLS:
    f = ROOT / rel
    original = f.read_text()
    n = original.count(old)
    if n != 1:
        print(f"--- {label}\n    ANCHOR FOUND {n}x in {rel} - control invalid")
        ok = False
        continue
    f.write_text(original.replace(old, new, 1))
    try:
        if f.suffix == ".py":  # only Python can have a syntax break
            ast.parse(f.read_text())
        sabotaged = pytest(target)
    except SyntaxError as e:
        sabotaged = f"SYNTAX({e.lineno})"  # type: ignore[assignment]
    finally:
        f.write_text(original)
    restored = pytest(target)
    fired = sabotaged == 1 and restored == 0
    ok &= fired
    print(
        f"--- {label}\n    sabotaged exit={sabotaged} (want 1)   restored exit={restored} "
        f"(want 0)   {'FIRED' if fired else 'DID NOT FIRE'}"
    )
print("\nALL CONTROLS FIRED" if ok else "\nA CONTROL DID NOT FIRE")
sys.exit(0 if ok else 1)
