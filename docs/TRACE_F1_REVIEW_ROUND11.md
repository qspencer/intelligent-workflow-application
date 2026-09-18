# Reviewer's guide — round 11: the round-10 return, and two detectors

**Package:** `trace-f1-review-r11-<sha>.tar.gz` · branch `main` · `git archive`.
Evidence: `docs/archives/GATE_OUTPUT_R11.txt`, `docs/archives/CODE_MANIFEST_R11.txt`.

---

## 0. Your three, fixed

| # | Finding | Fix |
|---|---|---|
| **P1** | Template rewriting changed ordinary config data; a step was silently skipped | Data is returned **unchanged**, full stop. Three narrow exceptions, each justified by what actually reads the field. §1. |
| **P1** | The rewriter's identifier grammar was narrower than execution's (`prépare`) | Context paths are parsed the way `_resolve_context_value` parses them — split on dots, the id is whatever lies between. |
| **P2** | The "schema-derived" gate enumerated nothing | It reads `model_fields` off every definition model and classifies each field; unclassified fails the build. **And it ships a control** that adds a field and asserts the gate fires. |

---

## 1. On the template finding — our reasoning was inverted

Worth stating precisely, because you found a wrong *inference*, not a wrong
fact. Round 9 argued: `{…}` is a platform template form, so rewriting it
anywhere is safe. The premise was right; the engine renders placeholders in
**exactly one field**, `learned_memory.observations[].text`. Everywhere else
`{steps.a.value}` is DATA, and rewriting it in a `noop` config changed a
literal a downstream condition compared against.

Now: data is never rewritten. The exceptions are
**declared reference positions**, **`observations[].text`** (the one rendered
field, using the engine's own grammar), and **agent-facing `goal` /
`system_prompt`**, where a *delimited* reference is prose a model reads.

Execution equivalence is asserted, not just structure: the test runs the
workflow before and after minting and checks **the same steps ran**, because
"it still loads" was never the property in question.

---

## 2. Two detectors built before sending, from gaps our own ledger admitted

Both map to classes you have already found, so shipping with them open would
have been shipping a known hole.

**Duplicate definitions, extended from constants to FUNCTIONS.** While fixing
your P1 #2, two live definitions of `_rewrite_context_path` turned out to
coexist, with Python using the stale one — which is why the first attempt at
that fix appeared to do nothing. A shadowed definition is invisible at the
call site and passes every type check. Zero today; this is prevention.

**Grammar agreement**, your `prépare` finding generalised. It found a live
instance: the scaffold's placeholder pattern was a **hand-copy** of the
engine's — identical that day, free to diverge the next, the same shape as
the two projector-version constants. It is **imported** now, so divergence is
impossible rather than detectable. Then the general property: *for every step
id the ENGINE can resolve a reference to, minting must rewrite it* — asserted
over plain, underscored, hyphenated, non-ASCII (`prépare`, `步骤`),
80-character, numeric and underscore-only ids. A dotted id skips, with the
reason recorded: the engine cannot resolve it either.

---

## 3. What we changed about how we work

Your round-10 P2 was the fourth time a test or document of ours claimed
coverage the code did not provide. We restructured
`docs/REVIEW_FINDINGS_LEDGER.md` around that:

- **A coverage claim must have been seen to FAIL.** Every gate now needs a
  control that demonstrates detection. A gate never observed failing is a
  claim, not a control.
- **A detector in prose is not a detector** (rounds 8 and 9 each returned a
  class already in the ledger with its detector described and never run).
- **When a class recurs as "another instance we missed", the detector is an
  ENUMERATION of the space**, not another instance.
- We widened the M3 mechanism to "one rule, more than one implementation, and
  they disagree" — which is where your identifier-grammar finding actually
  belongs, and where we had filed it wrongly.

The ledger also now carries a **stopping rule**: this line ends when a round
returns no finding a listed detector could have caught. Rounds 8, 9 and 10 do
not meet that bar — every finding was a class we had already named.

---

## 4. Self-audit for this package

| Step | Result |
|---|---|
| Five gates, standalone, `$?` before any pipe | all 0 · **1156 passed** |
| Every cited gate shown failing (controls) | schema-addition control + the golden guard's two proofs |
| Forgery, predicate agreement, grammar agreement | 40 passed |
| Round-trip over all 11 real shipped definitions | 0 dangling, all load |
| Schema-field enumeration | complete, with its exclusions asserted |

---

## 5. Open, unchanged

**Audit-detail vaulting** is designed and blocked on two operator decisions
(`docs/TRACE_AUDIT_VAULT_DESIGN.md`): how an audit vault row is addressed —
the vault keys on `(org, instance, step_attempt, kind)`, one row per kind per
attempt, while a step attempt emits **many** audit entries, so the existing
key would collapse them and destroy all but one; and which details are
vaulted. The first needs a production migration. **The catalog
snapshot/digest** is next after it, to your specification. **F3/F4/F6**
unchanged. Contract A and B1 remain unclaimed; the flip stays ON under the
single-operator restriction across every trace surface.

---

## 6. The question, narrowed

Four rounds have now produced no defect in the projection primitive itself;
the findings have been our tooling and our claims about it. We are not asking
you to close the line. We are asking: **is there anything left in F1/F5 that
review can surface, or is the remaining risk now in the unbuilt work — the
audit vault and the catalog digest — where a design read is worth more than
another code round?**
