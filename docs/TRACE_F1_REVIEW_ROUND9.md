# Reviewer's guide — round 9: the round-8 return

**Package:** `trace-f1-review-r9-<sha>.tar.gz` · branch `main` · `git archive`.
Evidence: `docs/archives/GATE_OUTPUT_R9.txt`, `docs/archives/CODE_MANIFEST_R9.txt`.

---

## 0. Your three, fixed

| # | Finding | Fix |
|---|---|---|
| **P1** | Renaming left dotted config references pointing at old ids; omitting the key failed too, via the function's default | Config values are rewritten as references when their **key** says so (the twelve `*_from` keys read by stock functions, plus the learned-memory reference fields), and a default naming a renamed step is **materialised**. Ordinary config stays data. |
| **P2** | The golden `tool_call` case never called `safe_tool_call` | The corpus is **entry-point aware**; cases added for the action-dispatched at-rest path; a test **instruments the run** and asserts the function is actually invoked, by both doors. |
| **P2** | The historical fixture did not match the round-6 archive | Regenerated from **`b67391d`** — the archive you hold — not `4d9f39c`, the remediation written after the round-6 return. Both discrepancies you named now agree. |

**Materialisation fails safe.** A function counts as a reader of a defaulted
key unless it is known and provably is not. An unused config key is cosmetic;
a missing one breaks the run, which is the defect this exists to prevent.

**P1 is pinned by EXECUTION tests**, as you asked: the original definition
completes, the renamed one completes, with the reference explicit *and*
implicit. They assert on instance state rather than step output, because
safe-only storage withholds the undeclared field that would otherwise carry
the signal — a detail worth knowing if you write similar tests.

---

## 1. Two things your findings forced, beyond the fixes

**Immutability is about a case's OUTPUT, not the SET of cases.** Adding
coverage had been impossible without pretending the projector changed. The
generator now appends new cases to the current version's fixture and
**re-verifies every existing one first**; a changed expected output is still a
refusal.

**Historical authenticity is now checked, not asserted.** A test re-derives
each superseded fixture from the commit it names and compares, and the
generator records `HEAD` so future fixtures are checkable the same way. This
is what your "the check passes without establishing that the expected
historical output is authentic" was pointing at, and you were right that the
generator's refusal to overwrite does not by itself establish it.

---

## 2. What we found ourselves, before sending

You have now returned eight rounds, so we started a **findings ledger**
(`docs/REVIEW_FINDINGS_LEDGER.md`): every finding classified into seven
recurring mechanisms, with a detector for any class seen twice, and a
pre-package protocol we run on ourselves.

Its first run found a defect you had not reported: the **version stamps were
declared inside the projected output**, so a supplied
`projector_version: "AKIAIOSFODNN7EXAMPLE"` survived as projection metadata.
Nothing ever read them from the JSON — every consumer uses the row column — so
the in-output copy was pure attack surface and a second source of truth for
the stamp. Removed; **projector v6**.

Also closed from your §4.4 list while this was out: `workflow_id` is
**minted** rather than slugged from the model's proposed name (it was
published to ordinary readers as CONFIG while being model-authored), and the
**tool-summary numbers are bounded** — arity clamped, size to an order of
magnitude, ~5 bits and ~10 values instead of 64 bits each. The residual is
stated rather than claimed to be zero.

**The ledger's honest entry about round 8:** all three of your findings were
about our EVIDENCE or collateral damage, not projection defects — and **two of
them were classes already in our ledger with a detector written down and never
run.** The correction recorded is that a detector in prose is not a detector;
the round-trip probe and "assert the function was called" are executable tests
now.

---

## 3. Self-audit run before sending (§4 of the ledger)

Stated so you can judge it, and skip what we already covered:

| Step | Result |
|---|---|
| Five gates, standalone, `$?` read before any pipe | all 0 |
| Forgery pass — every generated field × every asset kind | 30 cases pass |
| Round-trip — all **11 real shipped definitions** through minting | 0 problems; function names, paths and non-reference config unchanged |
| Corpus review | all 5 asset kinds, 3 at-rest actions, `safe_tool_call` reached 3× |
| Adversarial probe of the newest surface (minting) | config that *looks* like a reference, a step id that is a **substring of another**, a non-dict step, a non-string id — all handled |

---

## 4. Open, unchanged and declared

**Audit at rest** — we reproduced it and then STOPPED, because it has a
prerequisite: audit details are **not vaulted** (the vault holds
output/tool_calls/model_output/trigger_payload/recall/error). Aligning at-rest
with the read path's default-deny would not withhold the model-chosen tool
name, it would DESTROY it, for the grant holder too — including the signal
`tool_param_override_blocked` exists to capture. Audit-detail vaulting comes
first; it is a named B1 follow-up, and at-rest over-retention is B1-class
while B1 is deferred.

**The catalog digest** is designed but unbuilt; we would rather confirm the
frame with you than guess. **F3/F4/F6** unchanged. Contract A and B1 remain
unclaimed; the flip stays ON under the single-operator restriction across
every trace surface.

---

## 5. Question for this round

Given round 8 produced no projection defects, and the remaining enumerated
items are either B1-gated or waiting on your design read: **is the F1/F5
foundation now at the point where the remaining work is B1's, not F1/F5's?**
We are not asking you to accept Contract A — we are asking whether this line
of review has reached its useful end, and the next package should be about
audit-detail vaulting and the catalog digest instead.
