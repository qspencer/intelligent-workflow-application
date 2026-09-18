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
| **M3** | **A rule enforced at one site, not all sites** | 3,5,6,7 | The rule was right; the coverage was partial. Entry-point-only withholding, two projector-version constants, completeness and compatibility detectors disagreeing, at-rest vs read-path asymmetry. |
| **M4** | **Version / compatibility discipline** | 5,6,7 | Output changed, the version did not, and valid older records read as *tampering*. Twice by hand. |
| **M5** | **Totality gaps on hostile input** | 3,5,6 | `TypeError` on an unhashable name, a non-total marker predicate, null handling. |
| **M6** | **Our EVIDENCE was wrong** | 4,5,6,7 | Claims in guides and commits that execution did not support; a masked exit code; a whole CI job never run locally; tests that asserted a thing they did not exercise. |
| **M7** | **Over-correction breaking a legitimate consumer** | 6,7 | A fix that traded a disclosure defect for a data-loss one — substring rewriting that corrupted config, key-dropping that would have broken grant-holder rehydration. |

**M6 deserves separate emphasis.** Three of the returns contained a finding
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

### Self-inflicted, found by us mid-remediation (worth counting)

Authorized-producer list written from memory (`record_invoice` for
`record_invoice_extraction`); `package.json` and `pyproject.toml` committed with
conflict markers; a brittle test pinning a version literal; **the golden guard
passing on the first real change it should have caught** (corpus too narrow).

---

## 3. Detectors — what we run before a package leaves

The rule: **a class that has returned twice gets a detector, not a fix.**

| For | Detector | State |
|---|---|---|
| M1 | **Provenance sweep.** For every field a projection RETAINS, name its producer in the ownership table. No entry = build failure. | ✅ built (`_OWNERSHIP`, totality test) |
| M1 | **"Who wrote this?" pass.** For each retained field ask the question in words, not in a regex. Model-chosen and definition-derived values are guilty until declared. | manual; the §4 checklist |
| M2 | **Forgery probe.** For every field WE generate, feed it back as hostile INPUT and assert it cannot carry a value. Prefer booleans; bound anything numeric. | partly built — generalise it |
| M3 | **One-rule-one-owner sweep.** Grep every call site of a rule; assert predicates that must agree DO agree; fail on duplicated constants. | partly built |
| M4 | **Golden version guard.** Output change without a version bump fails the build. | ✅ built — *and its corpus must be widened with every new behaviour* |
| M5 | **Totality fuzz.** Every entry point, every asset kind, hostile values incl. unhashable types, at depth. | ✅ built (generative properties) |
| M6 | **Claims register.** Every factual assertion in a guide, commit or capture must have an executed command behind it, pasted. No claim about a test without running that case. | **process — §4** |
| M7 | **Round-trip probe.** Take a REAL artifact (a shipped workflow definition, a real step output) through the change and diff. Legitimate data must survive. | partly built — generalise it |

---

## 4. The pre-package protocol (our own "round 0")

Run before any package leaves. Roughly an hour; the returns cost days.

1. **Gates, all five, standalone.** `ruff`, `format`, `mypy`, `pytest`,
   `pip-audit`. Read each exit code before any pipe. *(A red CI ran unnoticed
   for several pushes because the local set had four of five.)*
2. **Execute every claim you are about to write.** Open the sidecar and, for
   each factual sentence, run the command that proves it and keep the output.
   Delete any sentence you could not execute. *(Four of the reviewer's findings
   were about our claims, not our code.)*
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

## 5. What we should NOT expect to self-detect

Honesty about the limits keeps the protocol credible:

- **Architectural verdicts.** "Shape cannot establish provenance" and "your
  conclusion is too broad" were the two most valuable things the reviewer
  produced, and both were judgement about the design's frame. Detectors do not
  generate those.
- **Deferral-boundary judgement.** The corrected B1 trigger came from someone
  asking who can read a trace, not from a test.
- **Whether a disclosure is appropriate.** Releasing `parse_ok` is a product
  decision.

**So the target is not zero rounds.** It is: arrive at round 1 with M1–M7
already swept, so the rounds we do spend are spent on judgement rather than on
defects we could have executed our way to.

---

## 5b. First result — the M2 detector found a defect the reviewer had not

Written and run the same hour, the generalised M2 probe (every generated
field × every asset kind × a set of values worth smuggling) failed **six
ways** immediately:

```
projector_version:        "AKIAIOSFODNN7EXAMPLE"  -> survived
projection_schema_version: 123456789              -> survived
```

The version STAMPS were declared inside the projected output, so a supplied
value rode through as projection metadata. Checking the consumers before
fixing (M7) showed **nothing ever read them from the JSON** — every consumer
uses the row column — so an in-output copy was pure attack surface *and* a
second source of truth for the stamp, which is precisely the duplication the
original F5 was raised about. Removed; projector **v6**.

It also produced a small lesson of its own: deleting the fields from the
schemas removed the *boundary's* knowledge that a function may not emit them,
so the reserved names now live in their own set — ownership of a NAME is a
property of the projector, not of one asset schema.

**This is the ledger working as intended.** The class was reported four times
in different clothes; generalising it over every field and kind found an
instance nobody had reported yet, before a package went out.

## 5c. Round 8 — classified, and what it says about the detectors

Three findings; **round-7's 1, 2, 3 and 5 closed**, 4 partially.

| Finding | Class | Would a detector have caught it? |
|---|---|---|
| Minting omitted dotted context paths (`evaluation_from`), so a renamed workflow parsed then FAILED — and omitting the key failed too, via the function's default | **M7** | **Yes, and we had the detector and didn't run it.** The ledger's own round-trip probe says "take a REAL artifact through the change and diff". A real definition would have shown it. We tested the helper, not a run. |
| The golden `tool_call` case never invoked `safe_tool_call` — flat record, generic schema, frozen `{"_withheld_keys": true}` | **M6** | **Yes.** The case's NAME was the only true thing about it — the same fig leaf as the key-property test in round 4, one layer up. "Assert the function was called" is now a test. |
| The historical fixture was generated from `4d9f39c` (the remediation written AFTER round 6), not `b67391d` (the archive they hold) — disagreeing on a case and on the schema version | **M6** | **Yes.** We asserted provenance we had not verified. Fixtures now re-derive from the commit they name. |

**All three are M6/M7 — evidence and collateral damage — not new leaks.** The
M1/M2/M3 detectors held: nothing in round 8 was a projection defect. That is
the shape of progress, but two of the three were classes *already in this
ledger with a detector written down and not run.*

**Correction to §3:** a detector that exists in prose is not a detector. The
round-trip probe (M7) and "assert the function was actually called" (M6) are
now executable tests, not protocol steps.

## 6. Convergence record

Tracked so the claim "we are learning" is measurable rather than asserted.

| Round | Findings returned | Of which we could have self-detected |
|---|---|---|
| 1–3 | ~20 | most |
| 4 + re-review | 11 | most |
| 4-confirm | 4 (2 about our evidence) | 2 |
| 5 | 6 | 6 |
| 6 | 3 + the architecture | 3 |
| 7 | 5 | 5 |
| 8 | 3 (all M6/M7 — evidence + collateral, no new leaks) | 3 |

**The trend to watch:** volume is falling, but the proportion we *could* have
caught ourselves is rising — round 5 onward is almost entirely self-detectable.
That is the gap this protocol closes. Target for the next epic: **≤5 rounds,
with no M6 finding at all.**
