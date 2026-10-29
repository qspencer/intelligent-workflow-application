"""G29 (projector v23) — executable reproductions for the external reviewer.

Run from `backend/` with no AWS, no Postgres and no veracium:

    uv run python ../docs/review-artifacts/g29_reproductions.py
    # or, with no project env at all:
    PYTHONPATH=src python3 ../docs/review-artifacts/g29_reproductions.py

Imports only `trace_projection`, `trace_rehydrate`, `trace_migration` and
`trace_vault`; none of them import veracium. Every line the review guide
asserts is checked here, so a discrepancy on your machine is itself a finding.

Sections A and B are what G29 claims to do, and they pass.
Section C is the defect found while packaging: the projection is no longer a
FIXED POINT, and the four consumers that re-project a stored row rely on that.
"""

from __future__ import annotations

import json

from workflow_platform.trace_migration import _has_raw
from workflow_platform.trace_projection import (
    MAX_VOCAB_FIELDS,
    MAX_VOCAB_VALUES,
    PROJECTOR_VERSION,
    _normalize_vocabulary,
    redact_tool_data,
)
from workflow_platform.trace_rehydrate import verify_projection_agreement
from workflow_platform.trace_vault import output_has_raw

STEP = {"admin": False, "kind": "step_output"}

#: `examples/email_triage_apply/workflow.yaml` — the only live declaration.
VOCAB = {
    "category": ["personal", "notification", "newsletter", "promotion", "spam"],
    "attention": ["urgent", "awaiting-reply", "review"],
}

#: A non-token sentinel, so survival can only mean laundering.
SECRET = "victim@example.com"

#: The shape the live `record` step actually writes, confirmed against the 18
#: affected production rows (keys only were read: `_withheld_keys, attention,
#: category, parse_ok`).
RAW = {
    "category": "promotion",
    "parse_ok": True,
    "attention": [],
    "summary": "Sale ends tonight",
    "sender": "deals@shop.example",
}

_failures: list[str] = []


def check(label: str, got: object, want: object) -> None:
    ok = got == want
    if not ok:
        _failures.append(label)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {json.dumps(got, default=str)}")
        print(f"         want {json.dumps(want, default=str)}")


def show(label: str, got: object) -> None:
    print(f"         {label}: {json.dumps(got, default=str)}")


print(
    f"projector version {PROJECTOR_VERSION} · bounds {MAX_VOCAB_FIELDS} fields / {MAX_VOCAB_VALUES} values\n"
)

print("A. what the mechanism does — these are the guarantees G29 claims")

check(
    "A1 no declaration behaves exactly as v22",
    redact_tool_data({"category": "promotion", "parse_ok": True}, **STEP),
    {"parse_ok": True, "_withheld_keys": True},
)
check(
    "A2 a declared scalar drawn from the set survives",
    redact_tool_data({"category": "promotion", "parse_ok": True}, vocab=VOCAB, **STEP),
    {"category": "promotion", "parse_ok": True},
)
check(
    "A3 a declared list drawn from the set survives",
    redact_tool_data({"attention": ["urgent"]}, vocab=VOCAB, **STEP),
    {"attention": ["urgent"]},
)
check(
    "A4 a declared field with an UNDECLARED value is withheld AND flagged",
    redact_tool_data({"category": f"{SECRET} prose"}, vocab=VOCAB, **STEP),
    {"_withheld_keys": True},
)
check(
    "A5 a list fails WHOLE, never per-element",
    redact_tool_data({"attention": ["urgent", SECRET]}, vocab=VOCAB, **STEP),
    {"_withheld_keys": True},
)
check(
    "A6 an undeclared field is still dropped whole",
    redact_tool_data({"summary": SECRET, "category": "spam"}, vocab=VOCAB, **STEP),
    {"category": "spam", "_withheld_keys": True},
)
check(
    "A7 a declaration cannot release a reserved projector field",
    redact_tool_data(
        {"_withheld_keys": "true", "category": "spam"},
        vocab={"_withheld_keys": ["true"], **VOCAB},
        **STEP,
    ),
    {"category": "spam", "_withheld_keys": True},
)
check(
    "A8 a declaration cannot override a platform-declared child's validator",
    redact_tool_data({"model": f"{SECRET} prose"}, vocab={"model": [f"{SECRET} prose"]}, **STEP),
    {"model": "[redacted — raw-trace grant required]"},
)
check(
    "A9 R7-P1: honoured where a step output NESTS (context.steps.<id>)",
    redact_tool_data(
        {"steps": {"record": {"category": "promotion", "summary": SECRET}}},
        admin=False,
        kind="context",
        vocab=VOCAB,
    ),
    {"steps": {"record": {"category": "promotion", "_withheld_keys": True}}},
)
check(
    "A10 R7-P1: and NOT at the top level of another asset kind",
    redact_tool_data({"category": "promotion"}, admin=False, kind="audit_detail", vocab=VOCAB),
    {"_withheld_keys": True},
)
for bad in (
    None,
    "nope",
    42,
    [1, 2],
    {"ok": "not-a-list"},
    {"bad key": ["x"]},
    {"has@at": ["x"]},
    {"category": []},
    {"category": ["x" * 200]},
):
    check(
        f"A11 hostile declaration normalizes away: {str(bad)[:24]}", _normalize_vocabulary(bad), {}
    )

print("\nB. the residual we would have asked you to challenge")
print("      a LIST-valued field releases its ARRANGEMENT, not just its members:")
show(
    "repeats survive",
    redact_tool_data({"attention": ["urgent", "urgent", "review"]}, vocab=VOCAB, **STEP),
)
show("order survives  ", redact_tool_data({"attention": ["review", "urgent"]}, vocab=VOCAB, **STEP))
check(
    "B1 a 32-long list of members survives whole",
    redact_tool_data({"attention": ["urgent"] * 32}, vocab=VOCAB, **STEP),
    {"attention": ["urgent"] * 32},
)
check(
    "B2 33 exceeds MAX_VOCAB_VALUES and is withheld",
    redact_tool_data({"attention": ["urgent"] * 33}, vocab=VOCAB, **STEP),
    {"_withheld_keys": True},
)
print("      -> channel for a list field is sequences over the alphabet, not the")
print("         subset lattice: ~53 bits for a 3-value set at length<=32.")
print("         Live exposure is ~4 bits ONLY because `record_email_triage`")
print("         dedupes first — a parser property, not a projector guarantee.")

print("\nC. THE DEFECT — projection is no longer a fixed point")

stored = redact_tool_data(RAW, vocab=VOCAB, **STEP)
check(
    "C0 the write path stores the released verdict (this part works)",
    stored,
    {"category": "promotion", "parse_ok": True, "attention": [], "_withheld_keys": True},
)
check(
    "C1 re-projecting an already-projected row CHANGES it (fixed point broken)",
    redact_tool_data(stored, **STEP),
    {"parse_ok": True, "_withheld_keys": True},
)
check(
    "C2 the read path (GET instance -> kind='step_row') strips it again",
    redact_tool_data({"step_id": "record", "output": stored}, admin=False, kind="step_row"),
    {"step_id": "record", "output": {"parse_ok": True, "_withheld_keys": True}},
)
check(
    "C3 the frozen §4.3 agreement predicate reports integrity failure",
    verify_projection_agreement(RAW, stored, PROJECTOR_VERSION),
    "mismatch",
)
check(
    "C4 the zero-raw (B1) verifier flags the correctly-projected row",
    (_has_raw(stored, "step_output"), output_has_raw(stored)),
    (True, True),
)
check(
    "C5 ... whereas a v22-era row is a fixed point, as the verifier assumes",
    _has_raw(redact_tool_data(RAW, **STEP), "step_output"),
    False,
)

print("\nD. the authoring-time / projection-time disagreement (minor, fails closed)")
print("      the definition validator accepts any string <=120 chars; the")
print("      projector keeps only `_short_token` members (no spaces, no `@`):")
check(
    "D1 a declared member with a space is silently dropped from the set",
    _normalize_vocabulary({"category": ["hello world"]}),
    {},
)
check(
    "D2 ... and a mixed declaration silently releases a SMALLER set",
    {k: sorted(v) for k, v in _normalize_vocabulary({"category": ["spam", "hello world"]}).items()},
    {"category": ["spam"]},
)

print()
if _failures:
    print(f"{len(_failures)} CHECK(S) FAILED: {', '.join(_failures)}")
    raise SystemExit(1)
print("all checks reproduced as the review guide states them.")
