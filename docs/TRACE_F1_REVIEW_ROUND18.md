# F1/F5 trace review — round 18 sidecar

**Scope: the round-17 finding, fixed — and one observation about a
cross-surface vocabulary we chose not to change unilaterally.**

| | |
|---|---|
| Commit | `66f87a9` (`66f87a96a54db4d1e12543bad363196c4871b3df`) |
| Tree | `cc4171af051caf31d32cc0d2b3e54f11798f9d19` |
| Aggregate source hash | `9b1b321894326e7b21328ada505cb07d1617cfd3fc872011ce8e1f0a8804703d` |
| Archive | `docs/archives/trace-f1-review-r18-66f87a9.tar.gz` |
| Manifest / gates | `docs/archives/CODE_MANIFEST_R18.txt` · `GATE_OUTPUT_R18.txt` |
| Design record | `docs/TRACE_AUDIT_VAULT_DESIGN.md` |

Every claim executed before it was written. Four controls, all fired.

---

## 1. The finding

Both halves, as required:

- **The completeness check now covers the recovered error.** It looked only
  at `merged` (the step output). `merge_error` returns the stored marker
  when its vault row is absent — deliberately, so the caller can report
  `partial` — so a missing error record left `complete` true and the
  response claimed a full release while `error` was still the marker. A
  timeout was reported correctly because it RAISES; an absent record does
  not.
- **`error` joins this surface's access-kind declarations**, so the
  decision can name it as withheld.

Regression checks both the response (`raw_included: false`) and the
recorded outcome (not `released`, and `error` present in
`withheld_kinds`).

**This was our own half-wiring from last round:** we wired the recovered
error into the response and not into the decision that reports it. The
rule we drew from it — enumerate a recovered value's CONSUMERS (response
field, completeness computation, kind declaration, audit outcome), not just
its sources — is what we then applied to this round's change before
packaging.

---

## 2. What the pre-package review checked

**Consumers of every recovered value in the handler**, per the rule above:
`merged` reaches `presented`, the completeness check, `raw_tcs` and the
kind declaration; `merged_error` reaches the response field, the
completeness check and the kind declaration. No fifth consumer exists.

**The predicate's behaviour on every error shape**, because
`has_redaction_marker` is now load-bearing for release decisions and a
successful step has `error=None`. Captured in `GATE_OUTPUT_R18.txt`: only
the marker blocks a full release; `None`, `""` and a real error message do
not. A successful step's explain still reports `raw_included: true`, which
an existing test pins.

**Which helpers raise versus return the marker on a missing row.** Our
first classification of this was WRONG — a too-small inspection window
reported every helper as best-effort. Corrected before it reached this
document: the three `merge_*` read-surface overlays are best-effort by
design, `rehydrate_output` and `rehydrate_audit_detail` raise, and
`rehydrate_trigger` raises only when something was actually withheld.
Callers of the best-effort helpers must marker-check; `get_instance` is
covered because it assigns merged values into the structures its check
scans, and `explain_step` now is too.

---

## 3. An observation we did not act on

**A deterministic step's released content is not named by any declared
kind.** `exp_kinds` is `("tool_calls", "output_text", "error")`, and
`get_instance` uses the same vocabulary plus `trigger_payload` and
`recall`. For a deterministic step, what explain releases is the step
`output` dict, whose free-form fields are none of those names.

We did not change it. The vocabulary is shared across surfaces and is
written into existing audit records, so renaming or extending it changes
the meaning of historical entries — a decision that belongs with you rather
than inside a fix for something else. **Is the kind vocabulary intended to
name content classes (`tool_calls`, `output_text`) with a deterministic
step's output simply not decomposed, or should there be a kind for it?**

---

## 4. Verification

- **1,245** backend tests, 19 skipped. Five gates green under one exit code.
- **7 Postgres integration tests** and an **alembic up/down/up rehearsal**.
- **Four controls**, all fired, each syntax-checked before running.
- **Forgery pass** against v10 — input-derived reference, out-of-enum
  classification, `workflow_id` holding an email: all rejected.
- **Three read surfaces agree**; both predicates agree.
- `PROJECTOR_VERSION` **10**, unchanged: this round touched a completeness
  check and a kind declaration, neither of which is projector output.

---

## 5. Unchanged, and still the operator's

1. The **backfill** — 10,110 findings in a capped 2,000-instance sample.
2. **`monitoring/service.py`** — blocked on where instance-less raw lives.
3. **One orphaned vault row** with a NULL `audit_entry_id`.

The two design follow-ups you specified — the per-(action, field) registry
and the subject identity — are recorded with your answers and unblocked,
sequenced registry-first because subject identity needs its classification.
