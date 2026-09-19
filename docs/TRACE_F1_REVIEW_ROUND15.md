# F1/F5 trace review — round 15 sidecar

**Scope: the four round-14 findings, fixed — and two of your own answers we
have deliberately NOT built, put back to you as questions (§1).**

| | |
|---|---|
| Commit | `80db6ef` (`80db6ef3c6f794cf436b4aeb59e27e3535f44ca1`) |
| Tree | `8fe879eca881e4c8081a3f2426e0bd708d858305` |
| Aggregate source hash | `8581e3224b28f23619ea0ec368b29258ae8bd09ba46da0c649b632697f847e6f` |
| Archive | `docs/archives/trace-f1-review-r15-80db6ef.tar.gz` |
| Manifest / gates | `docs/archives/CODE_MANIFEST_R15.txt` · `GATE_OUTPUT_R15.txt` |
| Design record | `docs/TRACE_AUDIT_VAULT_DESIGN.md` |

Every claim was executed before it was written. Every cited gate was
observed FAILING — six controls, in the gate capture.

---

## 1. Two of your answers we have not built, and why we are asking first

You answered four questions in round 14. Two were acted on directly; two
imply design work we would rather scope with you than guess at.

### 1a. The per-(action, field) registry with typed constructors

Your answer to Q2 was *"yes, couple it with checks at the writers: canonical
IDs from records, closed enums for classifications, and measurements from
the components that compute them. A small action-specific schema registry
and typed constructors should suffice."*

**We built the closed-enum half.** `author` and `derived_from` validate
against `Literal["user","third_party","system"]` rather than being accepted
for looking like short tokens. Fields whose source we could establish —
canonical ids from records, hashes and counts computed by the emitting
component — were retained; `evidence_ref` and `event_type` were withdrawn.

**We did NOT build the registry or the typed constructors**, and the honest
reason is that we do not know how far you intend them to go. Specifically:

1. **Is the registry a projection-side table** (`(action, field) -> node`,
   consulted before the flat schema), **or a writer-side type** that makes
   an unclassified field impossible to emit in the first place? The first is
   a day's work and catches disclosure. The second catches disclosure AND
   mislabelling, but touches every `_audit` call site — 33 in the engine
   plus nine other writers.
2. **`audit_detail` is the only one of five asset kinds without ownership
   typing.** Extending `Owner` to it would give the existing totality test
   — unclassified field fails the build — for free. Is that the shape you
   mean, or is ownership labelling the thing you said is insufficient on its
   own?
3. **What establishes "canonical from a record"** in a checkable way? We can
   assert a value equals a field of a loaded row at emit time. That is real
   but invasive. A weaker version — the constructor takes the row, not the
   string — is cheaper and catches the common error.

### 1b. An opaque subject identity for `user_id`

Your answer to Q4 was to withhold the raw email by default but provide *"a
useful subject identity through ordinary role-based permissions — an opaque
internal subject ID for correlation, with a directory-resolved display
identity for authorized operators."*

**`user_id` is still withheld and nothing was built.** We agree with the
direction and stopped because the design has a question inside it:

1. **Which id?** We already have `users.id`, a per-tenant surrogate. Using
   it directly makes audit rows correlatable across an org — which is the
   point — but it is also a stable handle to a person, so "opaque" only
   means "not an email".
2. **Who resolves the display identity, and is the resolution itself
   audited?** A directory lookup by an authorized operator is an access to
   personal data; we would expect it to be recorded, which makes it a new
   surface with its own release semantics rather than a formatting concern.
3. **Does this replace the actor/subject asymmetry we invented?** You noted
   that "person acted upon" is not by itself the right rule and that
   audience and permitted use matter for both. If subject and actor should
   be treated alike, then `actor_id` — which today legitimately holds an
   operator email on every audit row — is the larger exposure, and the
   change is bigger than `user_id`.

**We would rather get this wrong on paper than in a migration.**

---

## 2. The four round-14 findings

| | Fix | Pinned by |
|---|---|---|
| **F1** (P1) `evidence_ref` exposed input-derived content | classified by SOURCE: withdrawn (context-resolved via `ObservationSpec.ref_from`), as was `event_type` (free YAML string). Classifications became closed enums. Projector **v10** | `test_an_input_derived_reference_is_NOT_released_without_a_grant` — the full path: workflow → learned-memory service → engine → audit storage → HTTP |
| **F2** (P2) lookup exceptions leave records unfinished | all three post-`_begin` lookups complete the access record; `merge_output`, which owns no record, converts to `RawTraceUnavailable` so the boundary catches it | `test_a_repository_LOOKUP_failure_is_a_retrieval_outcome` — tested separately from missing rows and decryption failures, as you asked |
| **F3** (P2) mixed response misreports | inline-complete entries counted in `recovered`; mixed now reports `partial`; a row that WAS released no longer carries a failure explanation | `test_a_MIXED_response_reports_partial_and_explains_per_row` |
| **F4** (P2) Postgres insert races | `ON CONFLICT DO NOTHING` makes the insert the atomic step, then compare-and-return | `test_CONCURRENT_audit_appends_of_one_entry_do_not_race` |

**F4 now has the execution you could not run.** Eight overlapping retries of
one logical write raise **three `UniqueViolationError`s** against the old
check-then-insert and **none** against the atomic version. Your finding was
established from the transaction sequence; it is now established from
Postgres.

**On F1's "construct public references from authoritative records where
possible":** one already exists and needed no new field.
`workflow_instance_id` is a COLUMN on the audit entry, engine-minted, so it
is not subject to detail projection and gives a grant-less reader canonical
correlation without the input-derived reference. Pinned so it cannot quietly
move into the detail.

---

## 3. What our own protocol caught this round

**A repo method with no test at all.** `reseal` — the compatibility path
that keeps pre-binding vault rows readable — went in during round 13 and was
never tested in either repository. Only the migration tool called it, and
only against production. If it silently did nothing, the migration would
report success while rows stayed unopenable, which is exactly the failure
its first version had. Found by a new protocol **step 0** (below), now
tested in-memory and against Postgres.

**We named the mechanism behind your last three returns.** Eight findings
across rounds 12–14 were one thing, which we had been fixing instance by
instance: **a path built from one end** — vaulting without recovery, the
flip without key init, attempt without completion, retry-before-commit
without retry-after, two of three endpoints. We have called it M9 and made
its detector a question asked BEFORE building, because every detector we
write enumerates over things that EXIST and an unbuilt counterpart is in no
enumeration. That is why our tooling kept missing this class.

**Two commits went out with the gate red** (`c0c4f03`, `1b0bfb2`), both
because a commit was chained to the wrong command while the failing gate's
output was read by eye. There is now one script with one exit code, and —
recorded because it is the same shape as your findings — we then failed to
use it correctly on the very next commit.

---

## 4. Verification

- **1,234** backend tests, 19 skipped. Five gates green, exit codes captured
  before any pipe.
- **7 Postgres integration tests** and an **alembic up/down/up rehearsal**,
  both in the capture.
- **Six controls**, each sabotaged with the defect it claims to catch.
- **Forgery pass**: nine probes against v10's classification — token-shaped
  `evidence_ref`, out-of-enum classifications, a case-variant enum value, a
  `workflow_id` holding an email, an oversized hash, a negative amount, a
  string where a count belongs. All rejected.
- **All three read surfaces agree** — at rest, HTTP read path, and the
  websocket frame produce identical projections for the same detail.
- `PROJECTOR_VERSION` **10**, golden frozen with verified provenance.

---

## 5. Still the operator's, unchanged

1. The **backfill** — 10,110 findings in a capped 2,000-instance sample.
2. **`monitoring/service.py`** — the one audit writer outside a vaulting
   path; its alerts are frequently instance-less and the vault is
   instance-scoped, so closing it needs a decision about where
   instance-less raw belongs.
3. **One orphaned vault row** with a NULL `audit_entry_id`.
