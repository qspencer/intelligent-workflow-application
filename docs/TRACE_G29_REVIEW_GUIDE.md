# Reviewer's guide — per-workflow released vocabularies (G29, projector v23)

**Branch:** `main` · **Commits:** `3f1c77e` (the change) + `081cfb3` (golden
provenance + guard compatibility) · **Nineteenth package on this subsystem by
count, but not a remediation round:** nothing here answers a finding you
returned. It asks about one new widening of the below-grant disclosure surface.

> **Read §3 before §2.** We packaged this intending to ask whether the argument
> in §2 holds. While assembling the package we found that the implementation
> **breaks the projector's fixed-point property**, and that three consumers
> which re-project a stored row are wrong as a result — including the frozen
> §4.3 agreement predicate and the zero-raw B1 verifier. The change is
> therefore presented as **withdrawn pending your ruling**, not as a candidate
> to approve. §2 is still the argument we want attacked, because it decides
> whether the *idea* survives; §3 decides whether this *shape* of it can.

| | |
|---|---|
| Change | `docs/NEXT_STEPS.md` G29 · projector v22 → v23 |
| Reproductions | `docs/review-artifacts/g29_reproductions.py` — 28 checks, all executed |
| Gates / manifest | `docs/archives/GATE_OUTPUT_G29.txt` · `CODE_MANIFEST_G29.txt` |
| Prior ruling this answers | `test_rr_p1_registered_key_with_unvalidated_value_is_redacted` (GR4-R4) |
| Live state | declared on `email_triage_apply`; **18 affected production rows**, §6 |

---

## 1. What the change does

A step output's business verdict — `category`, `attention` — is per-workflow
business vocabulary. The platform projector redacts it by default, correctly:
GR4-R4 ruled that a platform-level `category` enum puts one workflow's business
vocabulary into the component that governs all of them. An earlier attempt to
register these fields platform-side went red on exactly that test and was
reverted rather than have its pin rewritten (`85a7644`).

G29 moves the declaration to where the vocabulary lives:

```yaml
# examples/email_triage_apply/workflow.yaml
released_fields:
  category: [personal, notification, newsletter, promotion, spam]
  attention: [urgent, awaiting-reply, review]
```

`WorkflowDefinition.released_fields` is bounded (8 fields / 32 values) by a loud
load-time validator, and normalized again — silently, dropping malformed
entries — at the projection boundary, because a projection sits on the write
path of every run and must never be able to stop one being recorded. The
projector gains one branch, gated on `node is _STEP_OUTPUT`:

```python
elif vocab is not None and node is _STEP_OUTPUT and k in vocab:
    if _vocab_member(vocab[k], v):
        out[k] = v
    else:
        withheld = True   # declared field, undeclared value → withheld AND flagged
        continue
```

`_STEPS_SNAPSHOT` reuses that same object, so the identity check buys the R7-P1
nesting property: a declared field behaves identically in the stored row, the
instance context and an audit detail, and nowhere else (A9/A10 in the
reproductions). A list fails **whole**, never per-element — a partially dropped
axis reads as a complete smaller axis, a different verdict rather than a
redacted one.

**What it buys.** The 1,297-message catch-up of 2026-10-28 could not be analysed
for self-consistency or drift at all; the only route was an audited raw-trace
grant over real mail — a far larger disclosure, taken repeatedly, to answer what
five enum values answer.

## 2. The argument we want attacked

**Soundness is provenance, not shape.** Both halves of the declaration — the key
*and* the closed value set — come from a definition the platform loads. Never
model output, never a trigger payload. That is the same test that already makes
`wildcard_keys: "platform"` sound, applied to a value position instead of a key
position.

Shape cannot establish who produced a value. That was §1.4a's lesson and it is
why the earlier name-based attempt leaked `{"category": SECRET}`: `category`
looked like an enum field, so a value was admitted on the strength of its key's
name. Here a value survives only by being **equal to a string the operator
wrote in YAML**, so what a below-grant reader learns is *which of N
operator-authored strings*, and nothing else.

Two specific things to push on:

1. **Is "config is platform-provenanced" true enough at this boundary?** A
   definition can be authored through `POST /api/workflows`, imported, or
   scaffolded from an LLM draft (`workflow_platform.scaffold`). The scaffold
   path means a *model* can propose `released_fields`, and an Admin/Designer
   approves it by saving. We treat that as config because a human saves it, the
   same way we treat `capabilities` globs as config. **If the save step is not a
   sufficient provenance boundary, the argument fails here** — and this is the
   place we think it is most likely to fail.
2. **Does a declaration bound the channel, or just name it?** See §4 — for a
   list-valued field we now believe it only names it.

## 3. The defect — projection is no longer a fixed point

Every consumer that re-projects a stored row depends on an invariant the
verifier states in its own docstring: *"the default-deny projection is a fixed
point on already-safe data."* v23 breaks it. The vocabulary is supplied **per
call**, and only the three *write* sites in `engine/executor.py` supply it:

```
grep -rn "redact_tool_data(\|output_has_raw(" backend/src | grep -c "vocab="   # 3
```

So `project(project(x)) != project(x)` whenever a declaration is in force, and
four consumers that hold only the row — not the definition — are wrong:

| # | Consumer | Consequence |
|---|---|---|
| **C2** | `api/workflows.py` read path (`get_instance`, `explain_step`) | The released verdict is **stripped again on every below-grant read**. The feature's whole purpose is not delivered through Contract A's surface; the value is visible only to a direct SQL query. |
| **C3** | `trace_rehydrate.verify_projection_agreement` — the **frozen §4.3 predicate** | Returns **`"mismatch"`** for a correctly written row. Mismatch means *"the operational row was tampered with or the vault object does not belong to it → integrity failure"*, so a grant-holder read of any v23-released row degrades, and a real tamper is now indistinguishable from a normal row on this path. |
| **C4** | `trace_migration._has_raw` — the **zero-raw B1 verifier** | Flags every released row as still carrying raw, so the B1 gate reports findings on rows that are correctly projected. |
| — | `trace_migration` backfill re-projection (lines 169/205/218) | Would **strip** a released field out of a stored row on any future sweep. Over-redaction, not disclosure — but it is silent data loss on the operational row. |

All six checks are in §C of `docs/review-artifacts/g29_reproductions.py` and all
reproduce. The shape used is the real one, confirmed against the 18 affected
production rows (`_withheld_keys, attention, category, parse_ok`).

### 3.1 Why this is the architecture, not a wiring omission

The four consumers **cannot** simply be handed the vocabulary.

Round 6 removed a process-wide mutable catalog from the projector specifically
to make projection a pure function again. v23 reintroduces that dependency — not
as global state, but as a parameter only some callers can supply. The verifier
scans the whole store; the agreement predicate runs on one row; the read path
has an instance. None of them has the definition, and loading it would not fix
the predicate anyway: **a definition is mutable**. Edit `released_fields`
tomorrow and every historical row re-projects differently, so the agreement
predicate starts reporting tampering on untouched rows — which is criterion 17
(*a projector bump must not make pre-change rows unreadable*) arriving through a
door the version stamp does not cover.

`docs/NEXT_STEPS.md` G29 already named this as the open question to answer
before building:

> *"Open questions for whoever specs it: **where the declaration is bound
> (definition load vs. projection time) so a definition edit cannot
> retroactively widen stored projections**…"*

It was built bound at projection time. The three defects are what that costs.

### 3.2 Why the obvious fix needs F3/F4 first

The reproducible-projection fix is to **record the vocabulary that was in force
on the row**, the way `projector_version` already is — a column, not a field
inside the output, so a step function cannot forge it. Then every consumer
re-projects from the row alone, purity is restored, and a later definition edit
cannot touch history.

But that makes the agreement predicate consult **operational metadata a DB
adversary controls**, and widens release when it is tampered with. That is
precisely **F3** — the projection stamp as a mutable fail-open bit — which is
still **open** on this subsystem, and binding the vocabulary into an
authenticated commitment is **F4**, also open. So the correct version of G29
appears to *depend* on two findings you have already identified and we have not
yet closed. We would rather hear that from you than build a third fail-open bit.

Three candidate dispositions, for you to rule between:

- **(a) Withdraw.** Revert the declaration; verdict-level analysis keeps needing
  the audited grant. Costs nothing but the analysis convenience, and the grant
  is the control working as designed.
- **(b) Row-recorded vocabulary**, sequenced *after* F3/F4 so the stamp it rides
  on is integrity-protected.
- **(c) Something else** — in particular, if you think the right answer is that
  a business verdict should never be below-grant and the analysis should be done
  by offline re-classification from the vault, say so and we will stop
  attempting this.

## 4. The residual we were going to ask about anyway

A **list**-valued declared field releases its *arrangement*, not just its
members. `_vocab_member` accepts up to `MAX_VOCAB_VALUES` elements with repeats
and order preserved, so the channel is the set of **sequences** over the
alphabet, not the subset lattice: for `attention` (3 values, length ≤ 32) that is
~3^32 ≈ **53 bits per run**, not the ~3 bits the declaration reads like.

Live exposure today is far smaller — `record_email_triage` dedupes the axis
before the projector sees it, leaving 16 ordered subsets ≈ 4 bits. **But that is
a property of one parser, not a guarantee of the projector**, and the contract is
written against the projector. A future workflow declaring a list field without
such a parser gets the full channel. If (b) proceeds, canonicalizing a released
list (dedupe + declaration order) would collapse this to the subset lattice and
costs nothing a grant-holder needs, since raw is unchanged in the vault — we have
*not* built that, and it would be a projector-output change requiring a version
bump.

## 5. A second, minor finding — the two layers disagree (fails closed)

The load-time validator accepts any string ≤ 120 chars; the projector keeps only
`_short_token` members (no spaces, no `@`). So `category: ["hello world"]` passes
authoring validation and the projector silently drops the member — and a mixed
declaration silently releases a **smaller set than declared** (§D of the
reproductions). It fails closed, so it is an authoring trap rather than a
disclosure defect, but it is **M3** — one rule, two implementations that
disagree — the class the ledger lists as top-three recurring. Not fixed here:
with the change withdrawn the inconsistency is dormant, and patching it would
add churn to code whose disposition you have not ruled on yet.

## 6. Live state, and what we are asking the operator to do

The declaration is live on `email_triage_apply` under
`WORKFLOW_PLATFORM_TRACE_SAFE_ONLY=1` and
`WORKFLOW_PLATFORM_RAW_GRANT_DUAL_CONTROL=1`.

- **18 step rows** carry a released key, in a single window **2026-10-29
  03:19 → 10:31 UTC**. Key set `_withheld_keys, attention, category, parse_ok`;
  released values seen: `newsletter`, `notification`, `promotion`.
- **No data is at risk.** The raw is vaulted and AEAD-sealed as always; a grant
  still recovers everything. The harm is to two *controls*: those 18 rows read
  as integrity mismatches to a grant holder, and as findings to the B1 verifier.
- Removing the four `released_fields` lines restores **exact v22 behaviour** —
  check A1 proves the no-declaration path is byte-identical — after which the 18
  rows need one re-projection sweep to become fixed points again.

## 7. Out of scope — please do not spend the review here

**F3** (mutable fail-open stamp), **F4** (unauthenticated vault commitment) and
**F6** (per-kind release audit) remain open and unbuilt, as in round 18. They are
named in §3.2 only because G29's candidate fix appears to depend on them. Round
18's own open question — whether the access-kind vocabulary should name a
deterministic step's output — is also still unanswered and unrelated.

## 8. Verification, stated honestly

- **1,428 tests pass**, 23 skipped. Ruff, ruff-format, mypy strict clean.
- **pip-audit is RED** and not from this change: `pillow` 12.3.0 carries 8
  advisories fixed in 13.0.0, transitively via `pdf2image`/`pytesseract`. It was
  clean at round 18. Four gates green, one red — stated so the count is honest.
- The golden corpus has **7 new cases** freezing every branch of the new code
  (released scalar, released list, out-of-set value, partially-valid list,
  undeclared field, an attempt to release `_withheld_keys`, and
  vocabulary-absent); `projection_v23.json` is 76 cases stamped `git 3f1c77e`.
  Six of the seven carry a `vocab` key — the seventh is the vocabulary-absent
  case and deliberately carries none, which is what lets the authenticity guard
  replay it through pre-v23 projectors that have no `vocab` parameter.
- **The tests did not catch the §3 defect**, which is the thing to hold us to.
  Four unit tests and seven golden cases all exercise the projector *through the
  write path*, where the vocabulary is supplied. Nothing asserted the fixed
  point, and nothing round-tripped a stored row back through a read surface.
  The detector this earns — *re-project every golden fixture and assert the
  projection is a fixed point* — is a two-line property test that would have
  failed immediately. It is **recorded in `docs/REVIEW_FINDINGS_LEDGER.md` and
  deliberately not yet built**, because it is red until §3.2 is ruled on and we
  are not shipping a red gate to make a point.

## 9. What we most want from you

1. **Rule on §3.2** — (a) withdraw, (b) row-recorded vocabulary after F3/F4, or
   (c) never below-grant. This is the decision we cannot make for ourselves: it
   widens a surface that has taken eighteen rounds, and our own confidence about
   it has now been wrong once in this very package.
2. **Attack §2.** Specifically the scaffold path: is "a human saved the
   definition" a sufficient provenance boundary for a *value* set, given that it
   is already the boundary we rely on for capability globs?
3. **§4** — is the subset lattice the right release for a multi-valued axis, or
   should a list field simply not be declarable?
