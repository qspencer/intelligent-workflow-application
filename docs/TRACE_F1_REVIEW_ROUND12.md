# F1/F5 trace review — round 12 sidecar

**Scope: audit-detail vaulting + the at-rest audit tightening.** This is the
work you named as the next substantive review, now built and running in
production.

| | |
|---|---|
| Commit | `b60e9da` (`b60e9dabc012a2dc16ecf8761a14acc4c7b84e32`) |
| Tree | `01f0fda12b4f2fe5c4ae0b36b8f96d162dbb2377` |
| Aggregate source hash | `72f372e35c2124a7ce3348b0ef79e4ab583643f7fb9a6c53973e0232fc82b2d9` |
| Archive | `docs/archives/trace-f1-review-r12-b60e9da.tar.gz` |
| Manifest / gates | `docs/archives/CODE_MANIFEST_R12.txt` · `GATE_OUTPUT_R12.txt` |
| Design record | `docs/TRACE_AUDIT_VAULT_DESIGN.md` (Part 1; Part 2 still unbuilt) |

Every claim below was executed before it was written; the commands are in the
gate capture. Where a claim is about a gate, the gate was observed FAILING.

---

## 1. What shipped

**Audit-detail vaulting.** `RawTraceKind.AUDIT_DETAIL`, a nullable
`RawTrace.audit_entry_id` (Alembic `0012`, index
`ix_raw_traces_audit_entry`), `audit_idempotency_key` as a separate key
space, `RawTraceVault.record_audit_detail` (durable by default), and the
reordered `_audit` chokepoint: **mint entry id → vault raw durable-or-fail →
project → append.**

The existing vault key is one row per `(org, instance, step_attempt, kind)`,
but one step attempt emits many audit entries, so reusing it would have
collapsed them silently. `step_attempt_id IS NULL` also already means
"instance-level", so overloading it would have destroyed that distinction.

**The at-rest tightening.** `project_audit_detail_at_rest` was a denylist —
anything unlisted passed through — so at rest held strictly MORE than a
grant-less reader could see, the model-chosen tool name among it. It is now
the read path, and the two names delegate to one function.

Safe only because vaulting was scoped by the FINAL policy first: everything
the tightening removes was already vaulted, so it withholds rather than
destroys. Your round-11 qualifier was load-bearing and we got it wrong once
before getting it right (§3).

---

## 2. Your three criteria

| Criterion | Where it is pinned |
|---|---|
| Stable identity on retry, distinct per entry | `test_C1_redriving_the_SAME_entry_id_readdresses_the_same_row`, `test_C1_two_entries_with_IDENTICAL_detail_do_not_collide`, `test_C1_many_entries_in_ONE_step_attempt_each_get_their_own_vault_row` (the multiplicity case), `test_C1_the_audit_key_is_a_SEPARATE_SPACE_from_the_step_attempt_key` |
| Recovery when the vault write succeeds and the append fails | `test_C2_a_vault_row_whose_append_FAILED_is_still_recoverable` — the orphan is the deliberate trade and the test says so; plus `test_C2_a_failed_vault_write_FAILS_the_audit_rather_than_dropping_raw` for the other order |
| Coverage of every audit writer and entry scope | `test_C3_every_audit_writer_is_classified` — AST enumeration over `src/`, all 10 writers classified, a new one fails the build. Cross-checked against an independent `grep`: both find the same 10. |

Two criteria beyond yours, because they are the actual safety property:
`test_INVARIANT_no_audit_entry_loses_content_without_a_vault_row` (over a
real run) and `test_at_rest_never_holds_more_than_the_read_path_releases`.

---

## 3. Disclosures — five defects we introduced, all found by us

Stated plainly because a reviewer who discovers them unaided stops trusting
the rest of this document.

**3.1 An outage that reached production.** `audit_entry_id` was added to the
model, the table and the migration but NOT to the Postgres INSERT or row
mapper. It stored NULL, read back None, `vault_fingerprint` compared unequal
on the first write, and every audit vault put raised `VaultConflict` —
failing live workflow runs for 28 seconds (first `VaultConflict` 21:30:13Z, fixed process up 21:30:41Z). **No unit test could catch it:**
in-memory repositories round-trip the pydantic object, so column mapping is
never exercised. Logged as a new ledger mechanism (M8, "a test double that
cannot exhibit the failure mode"); the detector is an AST enumeration plus a
Postgres-gated round trip, deliberately outside the double.

**3.2 A security control enforced at one entry point and absent at seven.**
`tools/fire.py` never set `trace_safe_only`, nor did five other tools that
build a `WorkflowEngine`, and `escalation.py` carried its own copy of the
env-var spelling. `fire.py` honours `DATABASE_URL`, so **every hand-run
workflow against production wrote unprojected raw** into the tables the
service keeps projected. One reader now, and the detector that enforces it
immediately found the seventh site we had missed by hand.

**3.3 The vaulting gate was written against the wrong policy** — the lenient
at-rest diff rather than the final one — so it vaulted nothing for the
motivating case. Precisely the trap your round-11 qualifier named. Caught by
the new tests, not by reasoning.

**3.4 The safety invariant was vacuous and passed with the feature switched
off.** It compared `project(entry.detail) != entry.detail`, but
`entry.detail` is already projected, hence a fixed point by construction. It
looked like the strongest test in the file. Only running the control exposed
it.

**3.5 Round-12 pre-package found three more** (§4).

---

## 4. What the pre-package protocol found, after all of the above

**Forgery pass.** The five fields declared by the tightening used `_ID`,
whose regex admits `@` because it exists for operator-identity paths. The
rule stated at `_OPAQUE_ID_RE` is that routing ids use `_short_token`. So
`from_step_id: "victim@example.com"` survived at rest **and to grant-less
readers**. Step ids come from the definition, which a scaffolded or imported
workflow does not fully control. Fixed to `_TOKEN`; `PROJECTOR_VERSION`
7 → 8, because v7 is already stamped on 26 step rows and 73 vault rows in
production and a released version's output is immutable.

**Corpus review.** Undeclaring `original_id` left the golden guard GREEN —
the corpus never reached `escalation_resolved`, the fork trail or a
connector, so the guard could not see a regression in the only fields the
tightening released. Four cases added, one holding an `@`. Underneath it, a
hole in the guard itself: it iterates the FIXTURE, so a corpus case that is
never frozen is silently never evaluated.

**Provenance.** The golden generator stamped HEAD, but the usual order is
bump → regenerate → commit, so HEAD is the commit BEFORE the bump. The
authenticity test skipped the CURRENT version, so the wrong stamp stayed
latent until the next bump: **v7 named a commit declaring 6, and v8 had it
too.** The generator now verifies its own stamp and writes `UNVERIFIED`
rather than a plausible commit that cannot reproduce the fixture; the
authenticity test no longer skips the current version. All six fixtures
v3–v8 now verify against the commits they name.

---

## 5. Decisions we did NOT make, and want your view on

**5.1 The widening.** The tightening declared five fields and only five —
those whose absence breaks function (`original_id`, which the escalations
API filters on) or that were already test-pinned as the operator trail.
Measured on production: **39% of audit entries now withhold every field at
rest**; `workflow_id`, `instance_id`, `user_id`, `cost_usd`, `model`,
`steps`, alert thresholds and the memory counts moved to grant-only.

Most of those are the same ownership class as fields `_AUDIT_DETAIL` already
declares — `_ID`-shaped ids beside `org_id`/`grant_id`, counts beside
`era`/`attempt`, tokens beside `content_hash`. The schema was written for
governance entries and never classified the engine-execution fields, so their
absence reads as oversight rather than judgement. Declaring them is one line
each and would restore the at-rest trail — but it widens what a grant-less
reader sees. **Is the conservative position right, or are we degrading the
audit surface for no security gain?**

**5.2 A known coverage gap, pinned rather than hidden.** Only the engine
chokepoint vaults. `monitoring/service.py` writes `alert_*` entries outside
it and on production data would lose something 100% of the time.
`test_C3_the_known_gap_is_recorded_not_forgotten` keeps it from going quiet.

**5.3 The backfill.** `verify_zero_raw` reports 10,110 findings in a capped
2,000-instance production sample — mostly the pre-flip backlog, but it kept
growing because of 3.2. One-way and operator-run; not started.

**5.4 One orphaned vault row** with a NULL `audit_entry_id`, written during
the 3.1 window. Unaddressable and unreferenced.

---

## 6. Numbers

- **1,195** backend tests pass; 16 skipped. Five gates green, exit codes read
  before any pipe. Postgres-gated suite run separately and included.
- **70.3%** of a 4,000-row production audit sample is vaulted (~45,700 rows,
  ~22 MB if the full history were vaulted; nothing is backfilled).
- Round-trip against production under v8: run completed, steps stamped v8,
  every audit entry either a projection fixed point or holding a vault row.

---

## 7. The question we would most like answered

Not "is the projection correct" — four rounds have said it is. It is
**§5.1**: we have made at rest equal to the read path, and discovered that
the read path was never designed to carry engine-execution metadata. Either
the audit surface should be widened for both, or 39%-withheld is the correct
posture and operators should reach for a grant. We could argue it either way,
which is why we are asking rather than deciding.
