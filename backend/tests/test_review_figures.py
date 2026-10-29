"""Every figure in a live review guide is PRINTED by something, never typed.

Rule taken from the veracium dev seat: "every number in prose is printed by a
command, and the stage checks the prose against the printed line." It is here
because the G29 package shipped five wrong figures that nobody computed:

- "~53 bits" for a channel that is 51.3 (typed from "3^32 ≈ 53"; 3^32 is 50.7);
- "all 75 of that day's v23 rows" where its own shipped evidence says 78;
- "8 pillow advisories, 11 in total" where there were 39 and 42 — the capture
  kept `tail -12` of a listing sorted by package name;
- 28 reproduction checks for 29, and "three consumers" beside a table of four.

The reviewer reproduced the "53-bit" example: the script printed the typed
string, so re-running it reproduced a figure no one had calculated. That is the
failure this file exists for — a number that LOOKS like evidence.

How it works. A guide registers CLAIMS: a pattern that captures a figure as
written, and a function that produces the figure independently — from shipped
evidence (describing the reviewed package as captured), or from code and
fixtures (describing what ships). Then a SWEEP finds every digit in the guide's
prose and fails on any that no claim covers and no labelled exemption explains.

Limit, stated: the sweep sees DIGITS. A figure written as a word ("four
consumers") is checked only if it is registered; the evidence-bearing ones are.

Scope: the LIVE review package. When a package is retired its entry here goes,
and its archive is the record — a historical guide should not fail the build
because a later commit added a test.
"""

from __future__ import annotations

import ast
import json
import math
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.test_projection_call_sites import _DECLARED as PROJECTION_SITES
from tests.test_projection_call_sites import WRITE
from workflow_platform.engine.functions import ATTENTION_LEVELS, TRIAGE_CATEGORIES
from workflow_platform.trace_projection import MAX_VOCAB_FIELDS, MAX_VOCAB_VALUES

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
DOCS = REPO / "docs"
GUIDE = DOCS / "TRACE_G29_REVIEW_GUIDE.md"
GATES = DOCS / "archives" / "GATE_OUTPUT_G29.txt"
AUDIT = DOCS / "archives" / "AUDIT_EVIDENCE_G29.txt"
GOLDEN = BACKEND / "tests" / "golden" / "projection_v23.json"
REPRO = DOCS / "review-artifacts" / "g29_reproductions.py"


# --- independent producers of each figure ----------------------------------


def _printed(path: Path, pattern: str, group: int = 1) -> str:
    """A figure as PRINTED in a shipped evidence file."""
    m = re.search(pattern, path.read_text(), re.M)
    assert m, f"{path.name}: no line matches {pattern!r} — the evidence cannot back the claim"
    return m.group(group)


def _repro_output() -> str:
    r = subprocess.run(
        [sys.executable, str(REPRO)], cwd=BACKEND, capture_output=True, text=True, check=False
    )
    assert r.returncode == 0, f"reproduction script failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
    return r.stdout


def _repro_checks(section: str | None = None) -> int:
    lines = _repro_output().splitlines()
    if section is not None:
        start = next(i for i, ln in enumerate(lines) if ln.startswith(f"{section}. "))
        end = next(
            (i for i, ln in enumerate(lines[start + 1 :], start + 1) if re.match(r"^[A-Z]\. ", ln)),
            len(lines),
        )
        lines = lines[start:end]
    return sum(bool(re.match(r"^\s+\[(ok  |FAIL)\]", ln)) for ln in lines)


def _golden() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = json.loads(GOLDEN.read_text())["cases"]
    return cases


def _sequences() -> int:
    """List-channel size, computed from the constants — an oracle written
    independently of the reproduction script's own arithmetic."""
    return sum(len(ATTENTION_LEVELS) ** k for k in range(MAX_VOCAB_VALUES + 1))


def _ordered_subsets() -> int:
    n = len(ATTENTION_LEVELS)
    return sum(math.perm(n, k) for k in range(n + 1))


def _migration_projection_lines() -> list[int]:
    """Where `backfill_instance` calls the projector — the guide cites them."""
    tree = ast.parse((BACKEND / "src/workflow_platform/trace_migration.py").read_text())
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "backfill_instance"
    )
    return sorted(
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "redact_tool_data"
    )


def _section_3_table_rows() -> int:
    text = GUIDE.read_text()
    body = text[text.index("## 3.") : text.index("### 3.1")]
    return sum(1 for ln in body.splitlines() if ln.startswith("| ") and "---" not in ln) - 1


def _golden_test_count() -> int:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/test_projection_golden.py"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    return sum("::" in ln for ln in r.stdout.splitlines())


_WORDS = {
    w: i
    for i, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
    )
}


def _word(n: int) -> str:
    return next(w for w, i in _WORDS.items() if i == n)


# --- the claims -------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    pattern: str  # captures the figure(s) exactly as written in the guide
    truth: Callable[[], tuple[str, ...]]
    source: str  # where the truth comes from, for the failure message


def _n(*xs: object) -> tuple[str, ...]:
    return tuple(f"{x:,}" if isinstance(x, int) and x >= 1000 else str(x) for x in xs)


CLAIMS: list[Claim] = [
    Claim(r"— (\d+) checks, all executed", lambda: _n(_repro_checks()), "reproduction script"),
    Claim(
        r"bounded \((\d+) fields / (\d+) values\)",
        lambda: _n(MAX_VOCAB_FIELDS, MAX_VOCAB_VALUES),
        "trace_projection constants",
    ),
    Claim(
        r"The ([\d,]+)-message catch-up",
        lambda: (_printed(DOCS / "EMAIL_RECALL_VALIDATION_PLAN.md", r"\b(1,297)\b"),),
        "the recall-validation plan that measured the run",
    ),
    Claim(
        r"answer what\s+(\w+) enum values answer",
        lambda: (_word(len(TRIAGE_CATEGORIES)),),
        "TRIAGE_CATEGORIES",
    ),
    Claim(
        r"only the (\w+) \*write\* sites",
        lambda: (_word(sum(role == WRITE for role, _ in PROJECTION_SITES.values())),),
        "test_projection_call_sites._DECLARED",
    ),
    Claim(
        r"that (\w+) consumers\n> which re-project",
        lambda: (_word(_section_3_table_rows()),),
        "rows in the guide's own §3 table",
    ),
    Claim(
        r"(\w+) consumers that hold only the row",
        lambda: (_word(_section_3_table_rows()),),
        "rows in the guide's own §3 table",
    ),
    Claim(
        r"The (\w+) consumers \*\*cannot\*\*",
        lambda: (_word(_section_3_table_rows()),),
        "rows in the guide's own §3 table",
    ),
    Claim(
        r"\(lines (\d+)/(\d+)/(\d+)\)",
        lambda: _n(*_migration_projection_lines()),
        "AST of trace_migration.backfill_instance",
    ),
    Claim(
        r"All (\w+) checks are in §C",
        lambda: (_word(_repro_checks("C")),),
        "section C of the reproduction script",
    ),
    Claim(
        r"for `attention` \((\d+) values, length ≤ (\d+)\)",
        lambda: _n(len(ATTENTION_LEVELS), MAX_VOCAB_VALUES),
        "ATTENTION_LEVELS, MAX_VOCAB_VALUES",
    ),
    Claim(
        r"\*\*([\d.]+) bits per run\*\* \(([\d.]+) \u00d7 10¹⁵ sequences\)",
        lambda: (f"{math.log2(_sequences()):.1f}", f"{_sequences() / 1e15:.2f}"),
        "computed from the constants",
    ),
    Claim(
        r"not the (\d+) bits the declaration",
        lambda: _n(int(math.log2(2 ** len(ATTENTION_LEVELS)))),
        "the subset lattice of ATTENTION_LEVELS",
    ),
    Claim(
        r"3\^32 alone is (\d+\.\d+)",
        lambda: (f"{math.log2(len(ATTENTION_LEVELS) ** MAX_VOCAB_VALUES):.1f}",),
        "computed",
    ),
    Claim(
        r"leaving (\d+) ordered subsets ≈ (\d+) bits",
        lambda: _n(_ordered_subsets(), int(math.log2(_ordered_subsets()))),
        "computed",
    ),
    Claim(
        r"\*\*Blast radius:\*\* (\d+) `step_executions` and (\d+) `workflow_instances`",
        lambda: (
            _printed(
                GATES, r"step rows stamped v23 still carrying a released key: \d+ +\(was (\d+)\)"
            ),
            _printed(GATES, r"instance contexts still carrying one: \d+ +\(was (\d+)\)"),
        ),
        "GATE_OUTPUT_G29.txt",
    ),
    Claim(
        r"all (\d+) rows re-projected",
        lambda: _n(
            int(
                _printed(
                    GATES,
                    r"step rows stamped v23 still carrying a released key: \d+ +\(was (\d+)\)",
                )
            )
            + int(_printed(GATES, r"instance contexts still carrying one: \d+ +\(was (\d+)\)"))
        ),
        "GATE_OUTPUT_G29.txt",
    ),
    Claim(
        r"(\d+) rows carry a released key, all (\d+) of that day's v23 rows",
        lambda: (
            _printed(GATES, r"rows still carrying a released key: steps=(\d+)"),
            _printed(GATES, r"v23 rows today: (\d+)"),
        ),
        "GATE_OUTPUT_G29.txt",
    ),
    Claim(
        r"\*\*([\d,]+) older\n?\s*instances",
        lambda: _n(int(_printed(GATES, r"PRE-FLIP backlog, a separate known item: (\d+)"))),
        "GATE_OUTPUT_G29.txt",
    ),
    Claim(
        r"\*\*([\d,]+) tests pass\*\*, (\d+) skipped",
        lambda: _n(
            int(_printed(GATES, r"^(\d+) passed, \d+ skipped")),
            int(_printed(GATES, r"^\d+ passed, (\d+) skipped")),
        ),
        "GATE_OUTPUT_G29.txt",
    ),
    Claim(
        r"\*\*(\d+) rows, (\d+) distinct advisories in (\d+)\n?\s*packages\*\* \(pillow (\d+)\)",
        lambda: (
            _printed(AUDIT, r"^rows: (\d+)"),
            _printed(AUDIT, r"^rows: \d+ +distinct advisories: (\d+)"),
            _printed(AUDIT, r"packages: (\d+)$"),
            _printed(AUDIT, r"by package \(distinct\): \{'pillow': (\d+)"),
        ),
        "AUDIT_EVIDENCE_G29.txt",
    ),
    Claim(
        r"The frontend had \*\*(\d+) more\*\* in (\d+) packages\. All (\d+)",
        lambda: (
            _printed(AUDIT, r"^distinct advisories: (\d+) +by package: \{'undici'"),
            _printed(AUDIT, r"'total': (\d+)\}"),
            _printed(AUDIT, r"^TOTAL cleared this session: (\d+)"),
        ),
        "AUDIT_EVIDENCE_G29.txt",
    ),
    Claim(
        r"\*\*(\d+) new cases\*\*",
        lambda: _n(sum("vocab" in str(c["id"]) for c in _golden())),
        "golden v23",
    ),
    Claim(r"is (\d+) cases stamped", lambda: _n(len(_golden())), "golden v23"),
    Claim(
        r"(\w+) of the (\w+) carry a `vocab` key",
        lambda: (
            _word(sum("vocab" in c for c in _golden())).capitalize(),
            _word(sum("vocab" in str(c["id"]) for c in _golden())),
        ),
        "golden v23",
    ),
    Claim(r"Restored: (\d+) passed", lambda: _n(_golden_test_count()), "pytest --collect-only"),
    Claim(
        r"has taken (\w+) rounds",
        lambda: (
            _word(
                max(
                    int(m.group(1))
                    for p in DOCS.glob("TRACE_F1_REVIEW_ROUND*.md")
                    if (m := re.search(r"ROUND(\d+)", p.name))
                )
            ),
        ),
        "the highest TRACE_F1_REVIEW_ROUND file",
    ),
]

#: Digits in prose that are NOT figures, each with the reason. Matched as a
#: regex against the line, so the exemption is as narrow as the context.
EXEMPT: list[tuple[str, str]] = [
    (r"\b[Rr]ound\s+\d+\b", "a review-round reference, not a measurement"),
    (r"\bcriterion \d+\b", "a named contract criterion"),
    (r"\bitem \d+\b", "a list-item reference into another document"),
    (
        r"said \"~3\^32 ≈ 53 bits\"",
        "the WRONG figure, quoted inside its own correction note",
    ),
    (
        r"This bullet said 11 advisories, 8 of them",
        "the WRONG figures, quoted inside their own correction note",
    ),
    (
        r"accepts any string ≤ 120 chars",
        "describes the reviewed tree's validator, removed in 111542f; see the archive",
    ),
]


def _prose_lines() -> list[tuple[int, str]]:
    """The guide with code removed: fenced blocks and inline code are code, not
    prose figures. Line numbers are preserved for the failure message."""
    text = re.sub(
        r"```.*?```", lambda m: "\n" * m.group(0).count("\n"), GUIDE.read_text(), flags=re.S
    )
    blank = lambda m: " " * len(m.group(0))  # noqa: E731 — keep columns: positions map back
    return [(i, re.sub(r"`[^`]*`", blank, ln)) for i, ln in enumerate(text.splitlines(), 1)]


_FIGURE = re.compile(r"(?<![\w.§^~-])\d[\d,.]*\d|(?<![\w.§^~-])\d(?![\w])")
_STRUCTURE = [
    re.compile(r"^\s*#+\s*\d+(\.\d+)*\.?\s"),  # heading numbering
    re.compile(r"^\s*\d+\.\s"),  # list numbering
]
_NOT_A_FIGURE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}|§\d+(\.\d+)*|10¹⁵")


@pytest.mark.parametrize("claim", CLAIMS, ids=lambda c: c.pattern[:48])
def test_every_registered_figure_matches_what_produces_it(claim: Claim) -> None:
    m = re.search(claim.pattern, GUIDE.read_text())
    assert m, f"claim pattern no longer matches the guide: {claim.pattern!r}"
    written = tuple(g for g in m.groups() if g is not None)
    truth = claim.truth()
    assert written == truth, (
        f"the guide says {written}, {claim.source} says {truth}.\n  pattern: {claim.pattern!r}\n"
        f"Fix the PROSE to the printed value — never the other way round."
    )


def test_no_figure_in_the_guide_is_unaccounted_for() -> None:
    text = GUIDE.read_text()
    covered: list[tuple[int, int]] = []
    for claim in CLAIMS:
        for m in re.finditer(claim.pattern, text):
            covered.append((m.start(), m.end()))
    for pattern, _reason in EXEMPT:
        for m in re.finditer(pattern, text):
            covered.append((m.start(), m.end()))

    offsets = [0]
    for ln in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(ln))

    unaccounted = []
    for lineno, prose in _prose_lines():
        if any(s.match(prose) for s in _STRUCTURE):
            prose = re.sub(r"^\s*#*\s*\d+(\.\d+)*\.?\s", lambda m: " " * len(m.group(0)), prose)
        prose = _NOT_A_FIGURE.sub(lambda m: " " * len(m.group(0)), prose)
        for m in _FIGURE.finditer(prose):
            pos = offsets[lineno - 1] + m.start()
            if not any(a <= pos < b for a, b in covered):
                unaccounted.append(f"  line {lineno}: {m.group(0)!r} in {prose.strip()[:90]!r}")
    assert not unaccounted, (
        "figures in the guide that nothing produces — register a Claim that computes "
        "each, or an EXEMPT entry saying why it is not a measurement:\n" + "\n".join(unaccounted)
    )
