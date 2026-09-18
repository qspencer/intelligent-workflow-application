# Reviewer's guide — round 10: the round-9 return, and a self-audit

**Package:** `trace-f1-review-r10-<sha>.tar.gz` · branch `main` · `git archive`.
Evidence: `docs/archives/GATE_OUTPUT_R10.txt`, `docs/archives/CODE_MANIFEST_R10.txt`.

---

## 0. Your three, fixed

| # | Finding | Fix |
|---|---|---|
| **P1** | Several supported reference positions still broke after renaming | The reference model was wrong, not just incomplete — see §1. Rewriting is now **by schema location**, with step ids and context paths treated as different kinds. |
| **P1** | Default insertion introduced behaviour the original never requested | Fields and defaults are declared **per function**. A function gets only the defaults **it** defines. |
| **P2** | The generator failed when creating a fixture for a new version | Both paths share `_project_case`; a fresh-directory test covers the one that had none. |

---

## 1. Where the model was wrong, not merely incomplete

Worth stating plainly, because it explains why three rounds kept finding
positions:

- **`inputs` on an agentic step holds CONTEXT PATHS, not step ids.** Treating
  them as ids meant they were never rewritten and the selected input resolved
  to null.
- **`pin_params` was not handled at all**, and it is FAIL-CLOSED — an
  unresolved pin fails the step before dispatch.
- **Learned-memory reference fields live under `learned_memory`**, not step
  config, which is where we looked.
- **Defaults were materialised only while walking an existing config dict**,
  so omitting `config` entirely still dangled.

And your P2: the global tables merged facts from different functions.
`record_email_triage` READS `route_from`, but that default lives in the helper
`_record_codified`, so materialising it switched on routing the original never
had. Rewriting by key NAME also rewrote `route_from` on `noop`, where it is
ordinary returned data.

---

## 2. The self-audit you prompted, and what it found

Three consecutive rounds found "another position we missed", so we stopped
guessing positions and **enumerated the space**: every field of every
definition model, a reference planted in every string-bearing position, mint,
and see what survives.

**The executable surface is complete.** The five survivors are `description`,
trigger config, trigger `example_payload`, a step `label` and a
`condition_label` — prose and sample data we decline to rewrite, because
rewriting undelimited prose is what corrupted a comparison literal in round 6
and labels are display-only. That list is now an assertion: a new schema field
that can hold a reference shows up as an unexpected survivor.

It closed two gaps: delimited **`prior_steps.<id>`** (what an agent actually
reads when a step declares no `inputs`), and it confirmed — rather than
assumed — that a draft already containing a `step_1` is handled, since the
mapping is from original ids in one pass.

### 2b. And the adversarial step caught an over-reach we had just added

The same protocol's "what would I find if paid to fail this?" step found that
the delimited rewrite was applied to **every** string:

```
dest_dir: "/out/<steps.a>/"    -> rewritten   (ordinary config DATA)
trigger config                 -> rewritten
"if x<steps.a.count then stop" -> rewritten   ('<' is a LESS-THAN OPERATOR)
```

Nothing in the platform renders `<…>`, so those were data corruption of
exactly the kind you found in round 6 — introduced by us, in the fix for your
round-9 finding, and caught before sending. Delimiters must now CLOSE, and
the prose rewrite applies only to `goal` and `system_prompt`, the text a model
reads. `{…}` placeholders still apply everywhere because the engine really
does render them.

---

## 3. Self-audit results for this package

| Step | Result |
|---|---|
| Five gates, standalone, `$?` before any pipe | all 0 · **1143 passed** |
| Forgery pass — every generated field × every kind | 30 cases |
| Round-trip — all 11 real shipped definitions | 0 dangling references, all still load |
| Corpus review | 5/5 kinds, 3 at-rest actions, `safe_tool_call` reached |
| Schema-derived reference enumeration | complete; 5 deliberate exclusions asserted |
| Adversarial pass | found §2b before you did |

---

## 4. Open, unchanged

**Audit-detail vaulting** and **the catalog snapshot/digest** are the next
work, in that order, on your guidance: vaulting before stronger at-rest
filtering so authorized recovery survives; the digest naming a retained,
immutable snapshot verified during reconstruction, with unavailable history
producing an explicit `unsupported`. Neither reopens the ownership
architecture. **F3/F4/F6** unchanged. Contract A and B1 remain unclaimed; the
flip stays ON under the single-operator restriction across every trace
surface.

---

## 5. What we are tracking about these rounds

`docs/REVIEW_FINDINGS_LEDGER.md` classifies all findings into seven
mechanisms. Rounds 7, 8 and 9 produced **no projection defects** — the
findings moved to our tooling and our evidence, which are the classes our own
protocol is meant to catch. Rounds 8 and 9 each contained a class already in
the ledger with a detector written in prose and never run; the correction
recorded is that **a detector in prose is not a detector**, and the
round-trip, corpus and enumeration probes are executable tests now.

We are not asking you to shorten the review. We are trying to make the rounds
you spend land on judgement rather than on things we could have executed our
way to.
