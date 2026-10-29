# G29 review — response and errata

**Re:** `TRACE_G29_REVIEW_RESULTS.md` (2026-10-29), package `trace-g29-review-0ecfa72`.
**Asks for:** nothing. This closes the loop on your required changes and corrects
figures in the package you reviewed. No ruling is requested.

## 1. Disposition accepted

Disposition (a), withdrawal, is accepted, and (b) is recorded as the direction if
G29 is ever reopened. Your six required changes stand as the reopening conditions
in `docs/NEXT_STEPS.md` G29.

| # | Required change | Status |
|---|---|---|
| 1 | Enforce the withdrawal in source | **Done** — `111542f` |
| 2 | Approval boundary a scaffold cannot satisfy | Open; see §3 |
| 3 | Row-bound, authenticated policy identity (after F3/F4) | Open; F3/F4 still unbuilt |
| 4 | Explicit shape; scalar enums first | Open; reopening condition |
| 5 | One validator at authoring and projection | Open; reopening condition |
| 6 | Test the consumer contract, not same-argument idempotence | **Done** — `7a06183`, `62353e8` |

**Change 1.** You were right that the withdrawal was configuration-only. The model
validator now refuses any non-null `released_fields`. We derived the paths that
persist a definition from the source rather than recalling them, which found one
we would have missed: the create endpoint's **clone** branch used `model_copy`,
which skips validators. It now re-validates. Your scaffold probe, reproduced
verbatim, returns 422 and persists nothing.

**Change 6.** Your phrase "same-argument idempotence" describes a detector we
already had. `test_every_golden_case_is_a_projection_fixed_point` has existed
since R5, and its docstring names the consumers G29 broke. It stayed green
because its second pass inherited the case's vocabulary, which no consumer holds.
It now re-projects with none. Separately, every projection call site is derived
from source and must declare its role (write / read / verify / migrate / audit).

## 2. Errata in the package you reviewed

The archive at `0ecfa72` is unchanged and remains the record of what you were
sent. These are its errors:

| Guide said | Correct | How we know |
|---|---|---|
| list channel "~3^32 ≈ **53 bits**" (§4) | **51.3 bits** | Computed. 3^32 alone is 50.7 |
| "all **75** of that day's v23 rows" (§6) | **78** | Its own `GATE_OUTPUT_G29.txt` |
| "**11** advisories … `pillow` → **8**" (§8) | **42**, of which pillow **39** | Untruncated re-audit of the pre-fix pins |
| two packages "only surfaced after the first was fixed" (§8) | All three were present in the first audit | Same |
| the fixed-point detector "is deliberately not yet built" (§8) | It existed and G29 defeated it | §1, change 6 |

**The 53-bit figure affects your write-up.** Your P1 says "the guide's approximately
53-bit example … reproduces." The behaviour you reproduced is real, and your P1
stands unchanged. The *magnitude* was typed rather than computed, and the
reproduction script printed it as a string literal, so re-running the script
could not have caught it. It now computes the value.

**The audit counts came from our capture tool.** It kept `tail -12` of each gate.
pip-audit sorts its table by package name, so the header, the summary line and
every certifi and graphql-core row were cut. The 8 pillow rows the package shows
are the *last* 8 of the full listing, in the same order. The producing output is
now in `docs/archives/AUDIT_EVIDENCE_G29.txt`, and the capture script keeps full
output.

**Fixes to the process, not just these figures:**
- `tests/test_review_figures.py` checks every digit in a live guide against
  something that produces it, or requires a written reason why it isn't a
  measurement.
- Nineteen sabotage controls are in `docs/review-artifacts/g{29,30}_controls.py`.
  Two of them failed on first run, and in both cases the *test* was wrong.

## 3. Your question about other privileged fields

You asked us to check whether the scaffold concern reaches other privileged
definition fields. It does, and the gap was wider than the scaffold.

- **Production ran with no system capability layer.** A scaffolded workflow's
  boundary was the intersection of two layers the model wrote. A definition can
  only narrow the layers above it, so with nothing above it, it could declare
  itself unrestricted.
- **Two of the five capability attributes were never enforced.** `allowed_hosts`
  and `max_tokens_per_call` were declared, resolved and displayed, and the trace
  projector's schema was their only reader.

**Done in G30 phase 1 (`b3c387a`, deployed):**
- A tracked system layer, derived from what production uses. In governed terms
  that is one tool, so everything else needs an operator change in a reviewed
  commit.
- A strict loader. A missing or unknown key is refused, because an omitted
  attribute means "unrestricted" and a typo would otherwise fail open.
- `max_tokens_per_call` clamps each model call's output tokens.
- `allowed_hosts` is enforced in the browser, the one surface where an agent
  chooses a destination.

**The residual is measured, not asserted.** A server redirect during a page
navigation sends one request before anything in-process can see it; its content
never reaches the agent. A real-browser test pins this.

**Not done — phase 2.** Deterministic functions other than `pdf_extract` do their
file I/O outside the capability system. We would like your review of G30 once
that is closed, so you see one finished control rather than two halves.

An archive at the current head is available on request if you want to verify
any of this from source.
