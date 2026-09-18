# External review — findings ledger and self-detection strategy

**Purpose.** Eight rounds on one primitive is too many. This ledger exists so
the *next* epic converges in four or five, by making the reviewer's recurring
mechanisms into things **we** test for before a package leaves.

It is maintained, not written once: every returned finding is classified here
before its fix is committed, and a class that recurs gets a detector, not
another patch.

---

## 1. The shape of the problem

Across rounds 1–7 of the trace-governance review, roughly **45 findings**
returned. Very few were novel. They cluster into **seven mechanisms**, and the
top three account for most of the volume:

| # | Mechanism | Rounds seen | Why it kept recurring |
|---|---|---|---|
| **M1** | **Shape used where SOURCE was the question** | 1,2,3,4,5,6,7 | A validator answers "does this look safe?" when the real question is "who produced it?" Every tightening of the pattern invited the next round to find another surface. |
| **M2** | **Our own generated metadata trusted from input** | 2,3,5,6,7 | A field WE write is read back from a record an attacker can shape. Prefix-matched markers, forged `_withheld_key_count`, supplied `projector_version`, unbounded tool-summary numbers. |
| **M3** | **One rule, more than one implementation — and they disagree** | 3,5,6,7,10 | Widened after R10. Not only "enforced at one site, not all" (entry-point-only withholding, at-rest vs read-path) but **two implementations of the same rule that must agree and do not**: two projector-version constants, completeness vs compatibility predicates, a rewriter's identifier grammar narrower than the resolver's, and two live definitions of one function with Python using the stale one. |
| **M4** | **Version / compatibility discipline** | 5,6,7 | Output changed, the version did not, and valid older records read as *tampering*. Twice by hand. |
| **M5** | **Totality gaps on hostile input** | 3,5,6 | `TypeError` on an unhashable name, a non-total marker predicate, null handling. |
| **M6** | **Our EVIDENCE was wrong** | 4,5,6,7 | Claims in guides and commits that execution did not support; a masked exit code; a whole CI job never run locally; tests that asserted a thing they did not exercise. |
| **M7** | **Over-correction breaking a legitimate consumer** | 6,7 | A fix that traded a disclosure defect for a data-loss one — substring rewriting that corrupted config, key-dropping that would have broken grant-holder rehydration. |

**M6 is now the dominant remaining class — four occurrences, rounds 4, 6, 8 and 10.** Three of the returns contained a finding
about *our reporting*, not our code: a test that claimed coverage it lacked, a
guide that said "whole-word" where the code did substring, "all token paths are
platform-computed" when three were not, a gate capture printing `exit: 0`
beneath a failure. **A reviewer who cannot trust the evidence has to re-derive
everything, which is what makes rounds expensive.**

---

## 2. The ledger

`Self?` = could we plausibly have found it with the detectors in §3.

### Rounds 1–3 (the projector's first three code reviews)

| Finding | Class | Self? |
|---|---|---|
| Key allowlist passed any value whose KEY was listed | M1 | yes — default-deny probe |
| Safe-by-TYPE let scalars through | M1 | yes |
| `_marker()` prefix-matches, so a forged marker is an input capability | M2 | yes — forgery probe |
| Same marker defect RECURRED after being reported closed | M2+M3 | yes — call-site sweep |
| Authorizes by name/lexical shape, not path/source | M1 | hard — this is the architectural one |
| Mutable P3a stamp (fail-open bit) | M2 | yes |
| `content_commitment` is a plaintext low-entropy oracle | — | design review, not a probe |
| TWO projector versions in two modules | M3 | yes — constant-duplication sweep |
| Partial release audited as all-or-nothing | M3 | yes |

### Round 4 (foundation) and its re-review

| Finding | Class | Self? |
|---|---|---|
| `safe_tool_call` copied its OWN structural fields unvalidated | M2 | yes |
| Escalation `context` is model-authored free text | M1 | yes |
| Fork preserved output without stamping | M3 | yes |
| Retry path stored `str(exc)` unprojected | M3 | yes — writer sweep |
| Dict KEYS emitted verbatim (keys are content) | M1 | yes — sentinel-in-key probe |
| Routing ids validated with a pattern admitting `@` | M1 | yes |
| Marker predicate non-total and prefix-matching | M2+M5 | yes |
| `query` missing from the at-rest denylist | M3 | yes — denylist-vs-schema diff |

### Round 4 confirmation (our first package)

| Finding | Class | Self? |
|---|---|---|
| "All retained token paths are platform-computed" — FALSE, 3 counter-examples | **M6** | **yes — execute every claim** |
| Ceiling argument right, CONCLUSION too broad | — | judgement; the reviewer's best contribution |
| B1 deferral trigger too late and too narrow | — | judgement |
| Sidecar claimed a test covered a case it did not | **M6** | **yes — run the case** |

### Round 5

| Finding | Class | Self? |
|---|---|---|
| Version collision: changed output, same version | M4 | yes — golden guard (now built) |
| Process-wide mutable catalog; `register` REPLACED rather than widened | M2+M3 | yes — reproducibility probe |
| `_withheld_key_count` from input retained as raw | M2 | yes — forgery probe |
| Withheld-only object read as COMPLETE | M3 | yes — predicate-agreement probe |
| Hostile-key drop left no signal | M3 | yes |
| `wildcard_keys="platform"` unjustified (scaffolded step ids) | M1 | yes — provenance question |
| Closed usage schema dropped real engine counters | M7 | yes — round-trip a real output |
| `TypeError` on unhashable tool name | M5 | yes — totality fuzz |

### Round 6

| Finding | Class | Self? |
|---|---|---|
| Scaffold substring rewrite corrupted function names, paths, literals | M7 | yes — round-trip a real definition |
| Duplicate step ids silently minted apart | M7 | yes |
| Withholding vs completeness disagreed (3 cases) | M3 | yes — predicate-agreement probe |
| `PROJECTION_SCHEMA_VERSION` stale AND declared twice | M3+M4 | yes — constant sweep |
| §4.4: the projector cannot establish the PRODUCER (5 groups) | M1 | this was the architecture; their best round |

### Round 7

| Finding | Class | Self? |
|---|---|---|
| Business withholding not recursive (entry-point only) | M3 | yes — same rule at every node |
| Producer check missed PROJECTION metadata and `parse_ok` | M1+M2 | yes — ownership totality |
| Version collision AGAIN | M4 | yes — golden guard |
| Minting rewrote ordinary data and quoted literals | M7 | yes — round-trip probe |
| Invalid legacy reserved value vanished silently | M2+M3 | yes |

### Rounds 8–10

| Finding | Class | Self? |
|---|---|---|
| Golden `tool_call` case never invoked `safe_tool_call` — flat record, generic schema | M6 | yes — assert the function is CALLED |
| Historical fixture generated from the wrong commit (the post-return remediation, not the archive) | M6 | yes — re-derive from the named commit |
| Scaffold substring rewrite mangled a function name, a path and a comparison literal | M7 | yes — round-trip a real definition |
| Reference positions missed: agent `inputs` (context paths, not ids), `pin_params` (fail-closed), learned-memory fields, omitted `config` | M7 | yes — enumerate the space (R-c) |
| A helper's default materialised onto its caller, switching on behaviour the original lacked | M7 | yes — per-function declaration |
| Generator could not create a fixture for a NEW version (create path not action-aware) | M6 | yes — the append path had a test, the create path did not |
| Template rewriting changed ordinary config data → a step silently skipped | M7 | yes — execution equivalence |
| Rewriter's identifier grammar narrower than the resolver's (`prépare`) | M3 | yes — grammar agreement (NOT YET BUILT) |
| The "schema-derived" gate enumerated nothing | M6 | yes — R-b, a gate must be seen to fail |

### Self-inflicted, found by us mid-remediation (worth counting)

Authorized-producer list written from memory (`record_invoice` for
`record_invoice_extraction`); `package.json` and `pyproject.toml` committed with
conflict markers; a brittle test pinning a version literal; **the golden guard
passing on the first real change it should have caught** (corpus too narrow).

---

## 3. Detectors — and the three rules that govern them

**The rule: a class that has returned TWICE gets a detector, not a fix.**
Three further rules were learned the hard way and belong here, not in a
round narrative, because they apply at the moment a detector is written:

> **R-a. A detector in prose is not a detector.** Rounds 8 and 9 each
> returned a class already in this ledger with its detector described in
> words and never executed. If it is not a test, it does not exist.
>
> **R-b. A coverage claim must have been seen to FAIL.** M6 has recurred four
> times, always as a test or document claiming coverage the code did not
> provide. Every gate needs a **control** that demonstrates detection — add
> the thing it should catch and watch it go red. A gate never observed
> failing is a claim, not a control.
>
> **R-c. When a class recurs as "another instance we missed", the detector is
> not another instance — it is an ENUMERATION of the space**, derived from
> the schema or the source, with the deliberate exclusions written down. The
> ownership table, the per-function reference declaration and the field
> classification are all this shape: no default, unclassified fails the build.
>
> **R-d. A detector covering ONE caller of a rule is not a detector for the
> rule.** Round 11 returned two findings, and both landed on rows in the
> table below that were already marked ✅. The grammar-agreement detector
> asserted its property over the context path only, so the prose rewriter
> kept a narrower grammar unseen; the execution-equivalence gate compared row
> COUNTS, so it could not distinguish a step that ran from one that was
> skipped. Neither ✅ was a lie about whether a test existed — both were a lie
> about **what it reached**. So a ✅ must now name the surfaces it covers, and
> R-b's control obligation applies **per surface**: a gate seen failing on one
> caller is evidence about that caller and nothing else.

| For | Detector | State |
|---|---|---|
| M1 | **Ownership table** — every retained field names its producer; no default | ✅ `test_every_declared_field_has_a_declared_owner` |
| M1 | **Per-function reference declaration** — derived from source, drift fails | ✅ `test_function_reference_declaration_matches_the_source` |
| M2 | **Forgery probe** — every generated field × every asset kind, fed back as hostile input | ✅ `test_M2_no_generated_field_can_carry_a_supplied_value` (found a defect the reviewer had not) |
| M3 | **Predicate agreement** — functions answering the same question must agree | ✅ `test_M3_predicates_answering_the_same_question_agree` |
| M3 | **Duplicate-definition sweep** — constants AND functions/methods | ✅ `test_M3_version_constants_have_exactly_one_definition` + `test_M3_no_module_defines_the_same_function_twice` (gap closed after R10) |
| M3 | **Grammar agreement** — two components parsing the same thing must accept the same language | ✅ **both reference surfaces** (R11). The scaffold IMPORTS the engine's placeholder grammar, and `_remap_step_ref` is now the single splitter behind *both* the context-path and the agent-prose rewriters — the delimited regex no longer decides where an id ends. `test_M3_whatever_the_ENGINE_can_resolve_the_scaffold_can_rewrite` asserts the property over `inputs`, `goal` AND `system_prompt` for plain/underscored/hyphenated/non-ASCII/long/numeric ids. Control: reverted to the old grammar, saw it fail on exactly the two non-ASCII ids. *Was ✅ before R11 while covering the context path only.* |
| M4 | **Golden version guard** — output change without a version bump fails | ✅ `test_projection_golden.py`, proven to fire twice; corpus must widen with new behaviour |
| M5 | **Totality fuzz** — every entry point, hostile values, at depth | ✅ generative properties |
| M6 | **Claims register + controls** — every factual claim executed before it is written; every gate has a control | ✅ protocol §4.2 + `test_the_classification_gate_fails_when_a_field_is_added` |
| M7 | **Round-trip probe** — a REAL artifact through the change, diffed | ✅ `test_minting_every_real_shipped_definition_leaves_no_dangling_reference` (found the edge-alias no-op) |
| M7 | **Execution equivalence** — the same run before and after a transformation | ✅ **with a control** (R11). `_assert_same_steps_ran` maps original→minted ids and compares terminal state keyed by `(step_id, attempt)`, so a differing retry count also counts. Control: `test_the_equivalence_check_can_fail` drives the reviewer's sabotage (config literal preserved, renamed branch forced `False`) and requires the comparison to report it. *Was ✅ before R11 while comparing `len(rows)` — and a SKIPPED step still writes a row, so it could not see the very substitution it existed to catch.* |
| — | **Schema-field classification** — every definition field classified, unclassified fails | ✅ with a control |

## 4. The pre-package protocol (our own "round 0")

Run before any package leaves. Roughly an hour; the returns cost days.

1. **Gates, all five, standalone.** `ruff`, `format`, `mypy`, `pytest`,
   `pip-audit`. Read each exit code before any pipe. *(A red CI ran unnoticed
   for several pushes because the local set had four of five.)*
2. **Execute every claim you are about to write.** Open the sidecar and, for
   each factual sentence, run the command that proves it and keep the output.
   Delete any sentence you could not execute. *(M6 — four of the reviewer's
   findings were about our claims, not our code.)*
2b. **For every gate you cite as coverage, show it FAILING** (R-b). Add the
   thing it should catch, watch it go red, put it back. A gate that has never
   been observed failing does not belong in a claim. Read your own captured
   evidence as a stranger would: a line that looks like a failure, or reads
   as a pass without demonstrating the case, is an M6 finding waiting.
3. **Forgery pass.** For each field the projection generates, supply it as
   input and confirm it cannot smuggle a value.
4. **Round-trip pass.** Push a real definition and a real step output through
   the change; diff against the original; confirm only what should have moved
   moved.
5. **Predicate-agreement pass.** Any two functions that answer the same
   question must answer it the same way on the same input.
6. **Corpus review.** Did behaviour change in a range the golden corpus does
   not reach? If so, widen it *before* freezing the next version.
7. **Ask the reviewer's question about ourselves:** *what would I find if I
   were paid to fail this?* Write the answer down and check it.

---

## 5. The limit — and how it has moved

**Written in round 7:** architectural verdicts and disclosure judgements are
what detectors cannot produce, so the target is not zero rounds.

**Still true, but no longer the binding constraint.** Rounds 8, 9 and 10
produced **no architectural findings and no projection defects**. Every
finding was M6 (a claim of ours that was not true) or M7 (a fix of ours that
broke something else) — and both are classes our own protocol is supposed to
catch. The limit that actually binds today is not the reviewer's judgement;
it is our discipline about our own claims.

What genuinely remains outside self-detection:

- **Architectural verdicts** — "shape cannot establish provenance", "your
  conclusion is too broad", the corrected B1 trigger. The two most valuable
  things this review produced, and neither came from a test.
- **Whether a disclosure is appropriate** — releasing `parse_ok` is a product
  decision, not a property.
- **Whether our reasoning is inverted.** Round 10's template finding came from
  a premise we had stated correctly and concluded backwards. A detector
  cannot catch a wrong inference from a right fact; a reviewer can.

## 5b. Rounds 8–10, and what they cost us

The narratives are compressed to their lessons; the findings themselves are
rows in §2's tables.

| Round | Findings | What it taught |
|---|---|---|
| 8 | M6 ×2, M7 ×1 | Two were classes **already in this ledger** with a detector written in prose and never run. → **R-a**. |
| 9 | M7 ×2, M6 ×1 | The reference model had been wrong three rounds running, always as "a position we missed". → **R-c**: enumerate the space. |
| 10 | M7 ×2, M6 ×1 | A "schema-derived" gate that derived nothing; and our own round-9 reasoning inverted (`{…}` is a template form — in exactly ONE field). → **R-b**: a gate must be seen to fail. |

**The one time a detector paid for itself unprompted:** the M2 forgery probe,
written from this document and run the same hour, failed six ways and found a
defect no reviewer had reported — the projector version STAMPS were declared
inside the projected output, where a supplied
`projector_version: "AKIAIOSFODNN7EXAMPLE"` survived as projection metadata.
Nothing read them from the JSON; every consumer uses the row column. Removed;
projector v6. That is the pattern working: generalise a reported class over
every field and kind, and it finds the instance nobody reported.

**And the honest counterweight:** in rounds 8, 9 and 10 the self-audit caught
things *before* sending (the delimited-rewrite over-reach, `prior_steps`, the
id collision, a misleading probe line in our own gate capture) — but the
reviewer still found three each time, and every one was a class we had named.

## 6. Convergence record

Tracked so "we are learning" stays measurable rather than asserted.

| Round | Findings | Mechanisms | Projection defects |
|---|---|---|---|
| 1–3 | ~20 | M1/M2/M3/M5 | many |
| 4 + re-review | 11 | M1/M2/M3 | many |
| 4-confirm | 4 | 2 × M6 | 0 (dispositions + corrections) |
| 5 | 6 | M1/M2/M3/M4/M5/M7 | several |
| 6 | 3 + the §4.4 architecture | M3/M4/M7 | 1 |
| 7 | 5 | M1/M2/M3/M4/M7 | several |
| 8 | 3 | M6 ×2, M7 | **0** |
| 9 | 3 | M7 ×2, M6 | **0** |
| 10 | 3 | M7 ×2, M6 | **0** |
| 11 | 2 | M3, M6 | **0** |

**The trend that matters:** volume down to two, and **four consecutive
rounds with no defect in the thing under review**. The work has moved to the
tooling around it — which is real progress and also the indictment, because
M6 and M7 are precisely what §4 exists to catch before sending.

**Round 11 is the sharpest version of that.** Both findings landed on rows
in §3 that were already ✅. Not absent detectors — detectors whose reach was
narrower than the rule they claimed. That is a different failure from rounds
8–10 and it is the one R-d now exists for: we had stopped asking *what does
this gate actually touch?* once it went green.

**Target for the next epic: ≤5 rounds and zero M6.** On the evidence, M6 is
the one that decides whether that is achievable: it has appeared in five
rounds, every time as a name that was the only true part of a claim.

### When to stop reviewing

Worth stating so the line is not extended out of habit: this epic should end
when a round returns **no finding that a detector in §3 could have caught**.
Rounds 8–11 do not meet that bar — every finding was a named class. That is
the test, not round count.

**Disposition of the F1/F5 projector line (2026-09-18): CLOSED, and not
because it met the bar.** It did not. It closes because the bar is the wrong
instrument for what is left: the projector itself has been defect-free for
four consecutive rounds, and every remaining finding is in *our evidence
about it*, which more external rounds on the same artifact cannot fix — only
detectors can. The reviewer independently reached the same place, recommending
audit vaulting as the next substantive review. Existing projection regression
checks are retained; the next external round is scoped to audit vaulting and
catalog snapshots.
