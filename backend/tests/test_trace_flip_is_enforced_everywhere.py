"""The safe-only flip must be read in ONE place, and every engine that can
write to a real database must read it.

Found 2026-09-18 while trying to confirm audit-detail vaulting in production:
the fired run produced no vault rows, because `tools/fire.py` never set
`trace_safe_only`. Nor did the other five operator tools that build a
`WorkflowEngine`. `fire.py` and `run_email_triage_batch.py` honour
`DATABASE_URL`, so running a workflow by hand against PRODUCTION wrote
unprojected raw into the operational tables the service keeps projected.

A control enforced at one entry point and absent at five others is not
enforced. This enumerates the constructions from source (rule R-c) so a new
one fails the build until it decides.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOTS = (pathlib.Path("src/workflow_platform"), pathlib.Path("tools"))

#: Constructions that deliberately do NOT read the env flip, with the reason.
#: A test fixture pins the flip explicitly per case; that is the point of a
#: fixture and reading the ambient environment would make those tests depend
#: on the developer's shell.
EXEMPT: dict[str, str] = {
    "src/workflow_platform/engine/executor.py": (
        "the dataclass default — the field's own declaration, not a construction"
    ),
}


def _engine_constructions() -> dict[str, list[int]]:
    """file -> line numbers of every `WorkflowEngine(...)` call."""
    found: dict[str, list[int]] = {}
    for root in _ROOTS:
        for f in root.rglob("*.py"):
            try:
                tree = ast.parse(f.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "WorkflowEngine"
                ):
                    found.setdefault(str(f), []).append(node.lineno)
    return found


def _sets_flip(path: str, lineno: int) -> bool:
    tree = ast.parse(pathlib.Path(path).read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "WorkflowEngine"
            and node.lineno == lineno
        ):
            return any(k.arg == "trace_safe_only" for k in node.keywords)
    return False


def test_every_engine_construction_decides_the_flip() -> None:
    offenders: list[str] = []
    for path, linenos in sorted(_engine_constructions().items()):
        if path in EXEMPT:
            continue
        for lineno in linenos:
            if not _sets_flip(path, lineno):
                offenders.append(f"{path}:{lineno}")
    assert not offenders, (
        "these build a WorkflowEngine without deciding `trace_safe_only`, so they "
        f"silently default to OFF: {offenders}. If the engine can reach a real "
        "database, that writes unprojected raw into the operational tables."
    )


def test_the_flip_is_read_from_exactly_one_place() -> None:
    """The env var name must appear only in `trace_flip.py` (and this test).
    Two readers is the M3 class, and this control started life as exactly
    that: `main.py` read it and six tools did not."""
    from workflow_platform.trace_flip import ENV_VAR

    readers: list[str] = []
    for root in _ROOTS:
        for f in root.rglob("*.py"):
            if f.name == "trace_flip.py":
                continue
            if ENV_VAR in f.read_text():
                readers.append(str(f))
    assert not readers, (
        f"{ENV_VAR} is read outside trace_flip.py in {readers}. One reader, or the "
        "spellings drift and a control becomes advisory."
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("", False),
        ("0", False),
        ("no", False),
        ("on", False),
    ],
)
def test_the_flip_accepts_only_the_documented_spellings(
    value: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workflow_platform.trace_flip import ENV_VAR, trace_safe_only_from_env

    monkeypatch.setenv(ENV_VAR, value)
    assert trace_safe_only_from_env() is expected


def test_an_unset_flip_is_OFF(monkeypatch: pytest.MonkeyPatch) -> None:
    from workflow_platform.trace_flip import ENV_VAR, trace_safe_only_from_env

    monkeypatch.delenv(ENV_VAR, raising=False)
    assert trace_safe_only_from_env() is False
