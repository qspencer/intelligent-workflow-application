# F1/F5 trace review — round 14 sidecar

**Scope: the six round-13 findings, fixed — plus three more of the same
classes we found ourselves, and one question we would like answered (§1).**

| | |
|---|---|
| Commit | `da2d179` (`da2d17989795a2f2ab6d7a9ff08c104ec9e8516c`) |
| Tree | `a45f22b0f07376ce5ece05ca0d5ab9a2e1e534e6` |
| Aggregate source hash | `9532e3354a54f81713d65baa271b26505e4b6ca005ad6cbf805006d58a4b55e2` |
| Archive | `docs/archives/trace-f1-review-r14-da2d179.tar.gz` |
| Manifest / gates | `docs/archives/CODE_MANIFEST_R14.txt` · `GATE_OUTPUT_R14.txt` |
| Design record | `docs/TRACE_AUDIT_VAULT_DESIGN.md` |

Every claim here was executed before it was written. Every gate cited as
coverage was observed FAILING — eight controls, in the gate capture.

---

## 1. The at-rest widening — why we did it, and where it disagrees with you

**We would like your read on this specifically.** It is a posture change we
made deliberately, and your round-13 closing comment points at a weakness in
how we made it. We would rather argue it out than quietly keep it.

### Sequence, so the disagreement is not mistaken for disregard

The widening was recommended, decided by the operator, and built **before**
your round-13 verdict arrived. Your position — *"I favor restoring narrowly
defined operational metadata through action-specific schemas. Establish its
source before allowing it through; identifier shape or numeric type alone is
insufficient"* — reached us after v9 had shipped. So this is not work done
against your guidance; it is work your guidance now calls into question.

### What the problem was

The v8 tightening made at rest equal the read path. Measured on a 4,000-row
production sample, **39% of audit entries then withheld every field**.
`_AUDIT_DETAIL` had been written for governance entries (grants, release
decisions) and had never classified the engine-EXECUTION fields at all, so
their absence was an oversight rather than a decision.

### The argument that actually decided it

Not "operators want the trail back". This:

```
instance surface, no grant : {'workflow_id': 'dmarc-ingest', ...}
audit surface,    no grant : {'_withheld_keys': True}
```

The **same reader**, denied a value on one surface that they are handed on
another. Withholding `workflow_id` on the audit surface protects nothing —
they fetch it next door — and costs the trail. `instance_id` likewise.

The secondary argument is about the grant itself: at 39% fully-withheld,
operators reach for raw-trace grants as routine. A grant is the break-glass
control, and making it the daily path erodes it while burying genuine access
in noise. We read that as an argument *for* widening, on security grounds
rather than convenience.

### What we built

19 fields declared as `_TOKEN` / `_COUNT` / `_AMOUNT` — **never `_ID`**,
because `_ID` admits `@` for operator-identity paths and that was the v8
defect you found. Measured effect: fully-withheld **39% → 1%**, and the
vault rate **70.3% → 61%** because fewer details lose anything.

Deliberately still withheld, each for a stated reason: `user_id` (an email
in 416/416 production cases, and unlike `actor_id` it names the person
*acted upon*), `trigger` and `output` (nested schemas a flat declaration
would bypass), and `emitter` (absent from the sample, so no evidence it is
safe — undeclared means withheld).

### Where you are right, in our own vocabulary

**We classified by SHAPE. You are saying classify by SOURCE.** That is
mechanism **M1** in our findings ledger — *"shape used where SOURCE was the
question"* — the first class we ever recorded, and we walked back into it.

The concrete evidence that you are pointing at something real, rather than a
style preference:

- `audit_detail` is **the only one of five asset kinds with no ownership
  typing.** `step_output`, `step_row`, `instance` and `context` each have a
  total `Owner` table (ENGINE / CONFIG / BUSINESS / PROJECTION) with a
  totality test that fails the build on an unclassified field. `audit_detail`
  has none, so nothing forced us to state a producer per field.
- Our declarations are **flat across actions**, while projection already
  dispatches per action for `tool_call`. So `model` is declared once and
  trusted identically whatever wrote it. Today every `model` we can see is
  engine-chosen — but that is an observation about current data, not a
  constraint, and a validator that accepts a 120-char token cannot tell an
  engine-chosen model id from an attacker-chosen one of the same shape.

A shape bound is a real bound — a `_TOKEN` cannot carry a mail body or an
email address — but it bounds **damage**, not **provenance**. You are asking
for provenance, and we do not currently have the machinery to state it for
this asset kind.

### What we think the fix looks like, if you agree

Extend ownership typing to `audit_detail` and declare per **(action, field)**
rather than per field, so the existing totality test covers it and an
unclassified field fails the build. That is the same shape as the four kinds
that already have it, so it is a known quantity rather than a new mechanism.

### The questions

1. **Is the cross-surface argument sufficient on its own** for the subset
   already released to the same grant-less reader elsewhere — `workflow_id`,
   `instance_id` — independent of provenance? Our reasoning is that
   withholding a value the reader can fetch from another endpoint is
   theatre, and theatre in a security boundary is worse than nothing because
   it reads as protection.
2. **For the remainder, is per-(action, field) ownership typing the right
   instrument**, or is there a lighter one that still establishes source?
3. **In the meantime, should v9 stand or be reverted to v8?** Standing means
   ~1% fully-withheld on shape-bounded fields with source unestablished.
   Reverting means 39% fully-withheld and the cross-surface inconsistency
   returns. We lean toward standing, because every declared field is bounded
   such that content cannot survive it and the destructive direction is
   already closed — but that is exactly the judgement we are unsure of, so
   we would rather ask than assume.
4. **Is `user_id` the right call?** We withheld it because it names the
   person acted upon. That costs user-management audit its subject at a
   glance, recoverable only with a grant.

We are not looking for agreement. If the answer is that shape-bounded
declaration is not acceptable at any width without source, we would rather
learn that now than ship two more rounds on top of it.

---

## 2. The six findings

| | Fix | Pinned by |
|---|---|---|
| **F1** (P1) escalation recovery | `/api/escalations` routes through recovery; `raw_included` is per ROW from what was actually recovered | `test_the_ESCALATION_endpoint_recovers_for_a_grant_holder` |
| **F1b** inventory misses dict routes | the detector keys on the BEHAVIOUR that makes recovery necessary — reading the audit log — not on the return type | `test_every_endpoint_returning_audit_entries_goes_through_recovery` |
| **F2** (P2) WS records before retrieving | attempt → retrieve → record, with the kinds actually returned | `test_the_WS_release_record_matches_what_was_DELIVERED` |
| **F3** (P2) optional escalation vaulting | refuses before appending when projection would remove information and it cannot preserve it | `test_the_escalation_tool_REFUSES_rather_than_discarding` |
| **F4** (P2) undecryptable → HTTP 500 | `TraceCipherError` normalised to a retrieval outcome **and** the access record completes | `test_an_undecryptable_payload_…`, `test_an_undecryptable_record_completes_the_release_audit` |
| **F5** (P2) recovery ignores version | shares the one version gate with the step-output path; entry stamp checked against the vault row's | `test_an_unsupported_recorded_version_is_reported_not_claimed` |
| **F6** (P2) retry after commit | append idempotent on entry id in BOTH repos, refusing a reuse with different content | `test_C1_a_retry_after_the_append_COMMITTED_…`, `test_C1_reusing_an_id_…`, + a Postgres-gated test |

**On F4, the half we missed first.** Normalising `TraceCipherError` stopped
the 500. But `_payload_of` raising between `_begin` and `_complete` still
left the system-access record OPEN — the reader gets nothing and the log
never says why, which is worse than either failure alone. Found by
re-reading your requirement ("complete the release audit") against what we
had actually built, rather than against what we had intended.

**On F6, why a Postgres test.** The in-memory repo cannot exhibit an
unconditional-INSERT bug; it round-trips objects. That is ledger mechanism
M8, so the detector goes outside the double.

---

## 3. Three more of the same classes, found before packaging

Your last two returns were both really about **half-built paths**, so we
went looking rather than only fixing what was named.

1. **`explain_step` showed a grant holder a REDACTED tool name.** It does
   two-phase release correctly, but took `name` from the audit detail, which
   `safe_tool_call` redacts — while the rehydrated tool call beside it held
   the real one. F1's shape on a fourth surface.
2. **`resolve_escalation` needed an argued exclusion**, not a silent pass.
   It matches on `original_id` (a declared field) and returns only a status,
   so no detail reaches the caller. Written down in the detector.
3. **`rehydrate_trigger` performs no projection-agreement check at all.**
   F5's version gate therefore does not apply to it — there is nothing to
   get wrong about versions when nothing is compared. This is pre-existing
   rather than something this round introduced, and we are flagging it
   rather than fixing it blind: **should triggers have agreement checking,
   or is the `_redacted`-marker path deliberate?**

---

## 4. Verification

- **1,221** backend tests pass, 17 skipped. Five gates green, exit codes
  read before any pipe.
- **Postgres-gated suite** (5 passed) and an **alembic up/down/up rehearsal**
  of `0013`, both in the capture — round 12 noted migration execution was
  not independently verifiable from the archive.
- **Eight controls**, each sabotaged with the defect it claims to catch and
  observed failing.
- **Production round-trip** under v9 with encryption on. Note the result:
  **zero audit-detail vault rows**, because after the widening every detail
  in that run is a projection fixed point. We verified that is correctness
  and not silent destruction — each stored detail re-projects to itself and
  `audit_detail_has_raw` is false for all of them.
- `PROJECTOR_VERSION` holds at **9**. This round changed recovery paths,
  append idempotency, cipher error handling and version gating — none of
  which is projector output. The golden guard agreeing is the evidence for
  that, not the assumption.

---

## 5. Still open, and the operator's call

1. **The backfill** — `verify_zero_raw` reports 10,110 findings in a capped
   2,000-instance sample. One-way; not started.
2. **`monitoring/service.py`** — the one audit writer outside a vaulting
   path, pinned by a test so it cannot go quiet. Its alerts are frequently
   instance-less, and the vault is instance-scoped, so closing it needs a
   decision about where instance-less raw belongs.
3. **One orphaned vault row** with a NULL `audit_entry_id` from the earlier
   mapping outage.
