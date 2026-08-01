#!/usr/bin/env python3
"""Self-validate docs/VERIFICATION_INDEX.md against the codebase (external
review 2026-08-01, finding 11).

Checks the STABLE things (a renamed test or moved file breaks the review
handoff; line numbers drift and are deliberately NOT checked — the
reviewer's own point):

  1. Every cited file exists (by path suffix — tolerant of the index's
     mixed relative-path style).
  2. Every cited test (`file.py::test_x` or bare `::test_x`) exists as a
     `def test_x` in backend/tests/.
  3. No table row tagged CONTRADICTED is also tagged a VERIFIED status
     (the mislabel class the review kept catching).
  4. Best-effort (WARN only): backticked code symbols resolve to a
     def/class/assignment under backend/src.

Exit 1 on any hard failure (1-3); symbol misses are warnings. Run:
    cd backend && uv run python tools/check_verification_index.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
INDEX = ROOT / "docs" / "VERIFICATION_INDEX.md"
SRC = ROOT / "backend" / "src"
TESTS = ROOT / "backend" / "tests"

FILE_TOKEN = re.compile(r"`([^`]+?\.(?:py|md|yaml|yml|tf|toml))(?::L?\d+(?:[-\u2013]\d+)?)?`")
TEST_QUALIFIED = re.compile(r"`?([\w./-]+\.py)::(test_\w+)")
TEST_BARE = re.compile(r"(?<![\w.])::(test_\w+)")
SYMBOL = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`")


def _all_py(root: Path) -> list[Path]:
    return list(root.rglob("*.py"))


def _basename_index(roots: list[Path]) -> dict[str, list[Path]]:
    idx: dict[str, list[Path]] = {}
    for root in roots:
        for p in root.rglob("*"):
            if p.is_file():
                idx.setdefault(p.name, []).append(p)
    return idx


def check() -> int:
    text = INDEX.read_text()
    by_name = _basename_index(
        [
            ROOT / "backend",
            ROOT / "docs",
            ROOT / "examples",
            ROOT / "infra",
            ROOT / ".github",
        ]
    )
    failures: list[str] = []
    warnings: list[str] = []

    # 1. files
    cited_files = {m.group(1) for m in FILE_TOKEN.finditer(text)}
    for token in sorted(cited_files):
        suffix = token.split("/")[-1]
        candidates = by_name.get(suffix, [])
        if not candidates:
            failures.append(f"CITED FILE NOT FOUND: `{token}`")
        elif "/" in token and not any(
            str(c).replace("\\", "/").endswith(token) for c in candidates
        ):
            failures.append(f"CITED FILE PATH MISMATCH: `{token}` (basename exists elsewhere)")

    # 2. tests
    test_src = "\n".join(p.read_text(errors="ignore") for p in _all_py(TESTS))
    defined_tests = set(re.findall(r"def (test_\w+)", test_src))
    for m in TEST_QUALIFIED.finditer(text):
        _f, name = m.group(1), m.group(2)
        if name not in defined_tests:
            failures.append(f"CITED TEST NOT FOUND: ::{name}")
    for m in TEST_BARE.finditer(text):
        if m.group(1) not in defined_tests:
            failures.append(f"CITED TEST NOT FOUND: ::{m.group(1)}")

    # 3. CONTRADICTED must not be marked verified
    for line in text.splitlines():
        if line.startswith("|") and "CONTRADICTED" in line and "VERIFIED" in line:
            failures.append(f"CONTRADICTED row also marked VERIFIED: {line.strip()[:80]}…")

    # 4. symbols (warn only — prose in backticks yields false positives)
    src_text = "\n".join(p.read_text(errors="ignore") for p in _all_py(SRC))
    src_text += test_src  # test-only fixtures/classes are legitimate symbols
    known_actions = set(re.findall(r'action[=(]?["\'](\w+)["\']', src_text))
    for m in SYMBOL.finditer(text):
        sym = m.group(1).split(".")[-1]
        if len(sym) < 4 or sym.endswith(".py"):
            continue
        if not re.search(
            rf"\b(?:def|class) {re.escape(sym)}\b|\b{re.escape(sym)}\s*[:=]", src_text
        ):
            # only warn for symbol-shaped tokens actually likely to be code
            if sym in defined_tests or sym in known_actions:
                continue
            if re.fullmatch(r"[a-z_][a-z0-9_]{3,}|[A-Z][A-Za-z0-9]{3,}", sym):
                warnings.append(f"symbol not resolved in src/tests (may be prose): `{sym}`")

    for w in sorted(set(warnings)):
        print(f"WARN  {w}")
    for f in failures:
        print(f"FAIL  {f}")
    print(
        f"\n{len(cited_files)} files, {len(defined_tests)} tests defined; "
        f"{len(failures)} failures, {len(set(warnings))} symbol warnings."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(check())
