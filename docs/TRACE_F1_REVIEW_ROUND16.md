# F1/F5 trace review — round 16 sidecar

**Scope: the two round-15 findings, fixed — plus a third instance of the
same defect that a pre-package pass found on a surface you had not tested.**

| | |
|---|---|
| Commit | `0f89fe0` (`0f89fe06e89aa9e0486f84e7b173a58e7ab0e6d6`) |
| Tree | `3ff50eff60606b72424d2fd69e1039ca87ba6097` |
| Aggregate source hash | `9ad74f1723604bda88d0aa3144ce4ad96d45fec5df6f4df238df64d11a68616b` |
| Archive | `docs/archives/trace-f1-review-r16-0f89fe0.tar.gz` |
| Manifest / gates | `docs/archives/CODE_MANIFEST_R16.txt` · `GATE_OUTPUT_R16.txt` |
| Design record | `docs/TRACE_AUDIT_VAULT_DESIGN.md` |

Every claim was executed before it was written. Every cited gate was
observed FAILING — four controls, in the gate capture.

---

## 1. The two findings

| | Fix | Pinned by |
|---|---|---|
| **F1** instance-detail returns 500 without completing the decision | `get_instance` catches `RawTraceUnavailable` from the merge helpers, completes the decision with the real outcome, and serves the projected response. `merge_error` and `merge_trigger` also got the lookup normalisation `merge_output` already had | `test_instance_detail_lookup_failure_completes_the_release_decision`, `test_instance_detail_undecryptable_trigger_completes_too` |
| **F2** trigger recovery leaves its access record unfinished | `rehydrate_trigger` routes opening through `_opened_or_completed`. All three `rehydrate_*` methods now do; the three `merge_*` helpers correctly do not, because they own no access record | `test_trigger_recovery_completes_its_access_record_on_failure`, which asserts the matching request id receives a `retrieval_failed` completion |

---

## 2. A third instance, on a surface you did not test

**`explain_step` had the same defect as F1.** It calls `merge_output`
between `begin_raw_release` and `commit_raw_release` and caught nothing,
so a lookup timeout or an undecryptable payload became HTTP 500 with the
attempt recorded and no decision.

How it was found is the point. Both your round-15 findings were in code we
had changed the round before, *after* we had introduced a pre-build
"counterpart check" specifically to catch this class. The check had been
answered from memory. Run as an actual command — enumerate the CALLERS of
the recovery helpers, not the helpers — it puts five callers on one screen
and `explain_step` is visibly the one with no handler.

We have written that down as a rule: **the value of the counterpart step is
the list, not the question.**

Also fixed while there: the engine's two callers (`_rehydrate_context`,
`fork`) deliberately do NOT catch — an engine that resumed or forked onto
projected data would be worse than one that fails. That is documented at
`_rehydrate_context`; `fork` relies on the same behaviour without saying
so, which we have left as-is rather than change behaviour we were not
asked about.

---

## 3. What else the pre-package pass found

**A control that did not fire.** Sabotaging `merge_error`'s lookup
normalisation changed nothing, because in the endpoint path
`merge_trigger` raises first and the caller catches — so that helper's
normalisation was never reached by any test. You asked for consistency
across the output, trigger and error helpers; consistency needs a test per
helper when one shadows the others. Now parametrised over all three.

**Two omissions in our own record.** Both follow-ups you named — the
version-aware trigger projection-agreement contract, and the
per-(action, field) ownership registry — were in neither the backlog nor
the design doc, though recording deferred follow-ups is part of our
definition of done. Both are now written up with their shapes; the registry
is marked do-not-start until the three questions in the round-15 sidecar
§1a return, since the projection-side and writer-side readings differ by
roughly a day versus every `_audit` call site.

**A stale claim.** The design doc's v9 paragraph says "19 engine-execution
fields declared", true of v9 and not of v10, which withdrew two. Marked
superseded rather than edited, since it is what the round-14 package
argued.

**The control harness now rejects its own bad sabotage.** One round ago a
control "fired" because removing an `except` left a dangling `try` and
pytest exited 2 on a collection error. The harness `ast.parse`s the
sabotaged file before running, so a syntax break can no longer be mistaken
for a detector working.

---

## 4. Verification

- **1,241** backend tests, 19 skipped. Five gates green under one exit code.
- **7 Postgres integration tests** and an **alembic up/down/up rehearsal**.
- **Four controls**, each observed failing, each syntax-checked first.
- **Forgery pass** against v10: the input-derived reference, an
  out-of-enum classification and a `workflow_id` holding an email are all
  rejected.
- **Three read surfaces agree** — at rest, HTTP read path, websocket frame.
- `PROJECTOR_VERSION` **10**, unchanged: this round touched failure
  handling and call-site guards, none of which is projector output.

---

## 5. Unchanged, and still the operator's

1. The **backfill** — 10,110 findings in a capped 2,000-instance sample.
2. **`monitoring/service.py`** — the one audit writer outside a vaulting
   path; blocked on where instance-less raw should live, not on work.
3. **One orphaned vault row** with a NULL `audit_entry_id`.
