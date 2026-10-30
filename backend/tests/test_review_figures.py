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

Scope: the LIVE review package — none since 2026-10-30, when G30 was accepted
at round 7. G29's package and every G30 round are answered and their
registries retired; each package's
archive is its record. Between packages `GUIDE` is None and both tests skip.
A historical guide should not fail the build because a later commit added a test.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
DOCS = REPO / "docs"
#: The live guide, or None between packages.
GUIDE: Path | None = None  # G30 accepted at round 7 (2026-10-30); no live package
pytestmark = pytest.mark.skipif(GUIDE is None, reason="no live review package")
GATES = DOCS / "archives" / "GATE_OUTPUT_G30_R7.txt"
LIVE = DOCS / "archives" / "LIVE_EVIDENCE_G30_R7.txt"
RESULTS = DOCS / "TRACE_G30_R6_REVIEW_RESULTS.md"


def _printed(path: Path, pattern: str, group: int = 1) -> str:
    """A figure as PRINTED in a shipped evidence file."""
    m = re.search(pattern, path.read_text(), re.M)
    assert m, f"{path.name}: no line matches {pattern!r} — the evidence cannot back the claim"
    return m.group(group)


def _defined_controls() -> int:
    import ast

    tree = ast.parse((DOCS / "review-artifacts" / "g30_controls.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CONTROLS":
            assert isinstance(node.value, ast.List)
            return len(node.value.elts)
    raise AssertionError("CONTROLS not found in g30_controls.py")


@dataclass(frozen=True)
class Claim:
    pattern: str  # captures the figure(s) exactly as written in the guide
    truth: Callable[[], tuple[str, ...]]
    source: str  # where the truth comes from, for the failure message


CLAIMS: list[Claim] = [
    Claim(
        r"reports (\d+) claims, (\d+) test results",
        lambda: (
            _printed(GATES, r"^(\d+) claims; \d+ test results run"),
            _printed(GATES, r"^\d+ claims; (\d+) test results run"),
        ),
        "the reproductions run captured in GATE_OUTPUT_G30_R7.txt",
    ),
    Claim(
        r"defines (\d+) controls, all fire",
        lambda: (str(_defined_controls()),),
        "the number of controls DEFINED in g30_controls.py — not the number that fired "
        "in the capture, which would make this check read its own sabotage",
    ),
    Claim(
        r"(\d+) real `email-triage-apply` runs",
        lambda: (_printed(LIVE, r"email-triage-apply: runs=(\d+)"),),
        "LIVE_EVIDENCE_G30_R7.txt",
    ),
    Claim(
        r"called (\d+) times under the layer",
        lambda: (_printed(LIVE, r"label calls ok: (\d+)"),),
        "LIVE_EVIDENCE_G30_R7.txt",
    ),
    Claim(
        r"largest agent-step output was (\d+) tokens",
        lambda: (_printed(LIVE, r"largest agent-step output since phase 1: (\d+) tokens"),),
        "LIVE_EVIDENCE_G30_R7.txt",
    ),
    Claim(
        r"distillation: (\d+) observations recorded, the largest (\d+) output",
        lambda: (
            _printed(LIVE, r"distillations recorded: (\d+)"),
            _printed(LIVE, r"largest output_tokens per observation: (\d+)"),
        ),
        "LIVE_EVIDENCE_G30_R7.txt",
    ),
    Claim(
        r"including all (\d+) the viewer\s+holds",
        lambda: (_printed(LIVE, r"viewer xml-files \(write\)\s+files=\s*(\d+)"),),
        "LIVE_EVIDENCE_G30_R7.txt",
    ),
    Claim(
        r"(\d+) real `scheduled-health-report` runs, all completed",
        lambda: (_printed(LIVE, r"scheduled-health-report: runs=(\d+) completed=\1 failed=0"),),
        "LIVE_EVIDENCE_G30_R7.txt",
    ),
    Claim(
        r"all (\d+) failures\s+in your full-suite run",
        lambda: (_printed(RESULTS, r"(\d+) failed\*\*"),),
        "the reviewer's own verification table, docs/TRACE_G30_R6_REVIEW_RESULTS.md",
    ),
]

#: Digits in prose that are NOT figures, each with the reason.
EXEMPT: list[tuple[str, str]] = [
    (r"\b[Pp]hase \d\b", "a phase label, not a measurement"),
    (r"\b[Rr]ound \d\b", "a review-round label, not a measurement"),
]


def _guide_text() -> str:
    assert GUIDE is not None
    return GUIDE.read_text()


def _prose_lines() -> list[tuple[int, str]]:
    """The guide with code removed: fenced blocks and inline code are code, not
    prose figures. Line numbers are preserved for the failure message."""
    text = re.sub(r"```.*?```", lambda m: "\n" * m.group(0).count("\n"), _guide_text(), flags=re.S)
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
    m = re.search(claim.pattern, _guide_text())
    assert m, f"claim pattern no longer matches the guide: {claim.pattern!r}"
    written = tuple(g for g in m.groups() if g is not None)
    truth = claim.truth()
    assert written == truth, (
        f"the guide says {written}, {claim.source} says {truth}.\n  pattern: {claim.pattern!r}\n"
        f"Fix the PROSE to the printed value — never the other way round."
    )


def test_no_figure_in_the_guide_is_unaccounted_for() -> None:
    text = _guide_text()
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
