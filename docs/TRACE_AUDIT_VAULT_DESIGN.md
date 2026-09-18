# Deferred trace work — design notes (blocked on operator decisions)

Two items remain in the reviewer's queue. **Both need persistence-schema
changes**, so they are one decision, not two — kept in one document for
that reason.

---

## DECIDED (operator, 2026-09-18)

| | Decision |
|---|---|
| **1. Addressing** | **Option A** — a new nullable `audit_entry_id` column plus `RawTraceKind.AUDIT_DETAIL`. Taken as accepted on the recommendation; say otherwise and it changes. |
| **2. Scope** | **Option A** — vault **exactly those details that LOSE something to projection**: `project_audit_detail_at_rest(action, detail) != detail`. The predicate IS the projection, so the rule cannot drift from what projection actually does. |
| **Timing** | **Hold the migration until round 11 returns.** Nothing is applied to the production DB while a package is out and a return might touch the same files. |

**Build order when the hold lifts**, unchanged from below: migration →
chokepoint reorder (construct entry → vault raw durable-or-fail → project →
append) → rehydration by audit entry → **only then** tighten at-rest
filtering to match the read path. The first test to write is the
multiplicity case — N tool calls in one step attempt must produce N
recoverable rows — because that is the one the existing key would have
silently broken.

**Deliberately NOT started early.** The column does not exist yet, and the
deployed code is the checked-out `main`, so writing code against a missing
column would break production the moment the service reloaded. There is no
useful half of this to land.

## BUILT 2026-09-18 — Part 1 mechanism complete, at-rest switch still open

Landed: `RawTraceKind.AUDIT_DETAIL`, `RawTrace.audit_entry_id` (+ Alembic
`0012`, index `ix_raw_traces_audit_entry`), `audit_idempotency_key` as a
separate key space, `RawTraceVault.record_audit_detail` (durable by default),
and the reordered `_audit` chokepoint: **mint entry id → vault raw
durable-or-fail → project → append**. 14 tests in
`tests/test_audit_detail_vaulting.py`, one per criterion below.

**What is NOT done, stated plainly: the at-rest projection is still the
lenient denylist.** The over-retention this design opens with is therefore
still present. What has changed is that it is now safe to close — everything
the tightening will remove is already in the vault, so the switch destroys
nothing. That switch is the reviewable change, because it also decides what
operators can see without a grant.

**Measured on production before building** (4,000-row random sample of the
64,973-row `audit_log`): **70.3%** of entries lose something to the final
policy — ~45,700 rows and ~22 MB if the whole history were vaulted, though
nothing is backfilled and only new writes vault. `step_started` and
`step_skipped` vault at 0%; `step_completed`, `workflow_*`, `memory_*` and
`tool_call` at ~100%.

**The over-vaulting is deliberate.** `_AUDIT_DETAIL` is scoped to governance
entries ("content-free by design") and does not yet classify engine-execution
fields, so details holding only operational metadata are vaulted whole. Over-
vaulting costs rows; under-vaulting costs the record permanently. Refining
`_AUDIT_DETAIL` to classify the engine fields is the follow-up — and it is a
security-visible change, because it WIDENS what a grant-less reader sees.

**Known coverage gap, pinned by a test rather than hidden.** Only the engine
chokepoint vaults. Of the ten modules that append audit entries, eight write
operator/governance metadata about a human action; `monitoring/service.py`
writes `alert_*` entries that are engine-derived and would lose something
100% of the time. Routing it through the chokepoint is follow-up work;
`test_C3_the_known_gap_is_recorded_not_forgotten` keeps it from going quiet.

---

**Hold lifted 2026-09-18: round 11 returned.** Two bounded P2 corrections,
both outside this design (scaffold grammar, an equivalence test), both fixed.
No new defect in the F1/F5 projection primitive.

### Round 11 confirmed both options — with one qualifier that reorders the build

The reviewer favours the same two: an explicit `audit_entry_id`, and vaulting
the details that lose information through projection. The qualifier:

> That predicate must use the **final action-aware storage policy**.

This is not a restatement, it names a trap. The build order below tightens
at-rest filtering **last**. If the vaulting predicate is evaluated against
today's lenient `project_audit_detail_at_rest`, every detail that currently
passes through *unchanged* is judged "loses nothing" and is **not vaulted** —
and the moment the tightening lands, those same details begin losing
information with no vault row behind them. That is precisely the forensic
destruction this design exists to prevent, arriving one commit later and
looking like an unrelated bug.

**So the predicate and the final policy must land together.** Concretely: the
tightened, action-aware `project_audit_detail_at_rest` is written FIRST and the
predicate calls it, even though the tightened projection is only switched ON at
the end. The predicate must never be a snapshot of what projection happens to
do this week — it has to be a call into what it will do.

### What the next review must establish

Recorded now as acceptance criteria, so they are built to rather than
discovered:

1. **Stable identity across a retried write, distinct identity across
   entries.** Re-driving the same audit write must address the same vault row
   (no orphan accumulation); two different audit entries from one step attempt
   must never collide. This is the multiplicity case — the first test to write.
2. **Recovery when the vault write succeeds and the audit append then fails.**
   Durable-or-fail protects the opposite order. This direction leaves a vault
   row with no entry pointing at it; the behaviour must be stated and tested,
   not left to chance.
3. **Coverage of every audit writer and every relevant entry scope.** Per
   ledger rule R-c this is an ENUMERATION derived from the source — every call
   site reaching `_audit`, including instance-level entries where
   `step_attempt_id IS NULL` — with deliberate exclusions written down. A
   detector, not a hand-maintained list. Per R-d it must be shown to reach
   every one of those surfaces, not just the first.

---

# Part 1 — Audit-detail vaulting

**Status: DESIGNED, NOT BUILT.** Two decisions are the operator's, and one of
them changes a production table. Written while round 10 is out.

---

## Why this is next

The reviewer's round-8 finding: at rest, `tool_param_override_blocked` keeps
the model-chosen tool name and the attempted value verbatim, while the READ
path default-denies the same detail. We store strictly more than any ordinary
reader can see.

Investigating the obvious fix — align at-rest with the read path — showed it
is **wrong on its own**: audit details are **not vaulted**. The vault holds
`output`, `tool_calls`, `model_output`, `trigger_payload`, `recall`, `error`
and nothing else. Default-denying at rest would not *withhold* the model's
tool name, it would **destroy** it, permanently, including for the grant
holder — and the record destroyed is exactly the signal
`tool_param_override_blocked` exists to capture.

The reviewer agreed with the ordering: *"Vaulting before stronger at-rest
filtering preserves authorized recovery."* So: vault first, filter second.

---

## The blocker: the vault cannot address an audit entry

`idempotency_key(org_id, instance_id, step_attempt_id, kind)` — **one row per
(instance, step attempt, kind)**, keyed on the immutable step attempt.

A single step attempt emits **many** audit entries: one `tool_call` per tool
call, plus `memory_observed`, `memory_recalled`, `tool_param_override_blocked`
and others across 33 call sites. Keyed as it is today, they would collapse
onto one vault row and all but one raw detail would be lost — the same
data-destruction this work exists to prevent, arriving by a different route.

`RawTrace` has no field that identifies an audit entry:

```
id · org_id · instance_id · step_attempt_id · kind · state · idempotency_key
raw_schema_version · projection_schema_version · projector_version
payload · content_commitment · created_at
```

---

## Decision 1 — how an audit vault row is addressed

| Option | Shape | Cost |
|---|---|---|
| **A. `audit_entry_id` column** (recommended) | New nullable column + `RawTraceKind.AUDIT_DETAIL`; key becomes `(org, instance, step_attempt, kind, audit_entry_id)` | **Alembic migration on the production table.** Honest: the column says what it holds |
| B. Overload `step_attempt_id` | Put the audit entry id in the existing column | No migration, but the column's meaning becomes conditional on `kind` — the kind of implicit contract this review line has repeatedly punished |
| C. Discriminator inside `idempotency_key` | Append the audit id to the key string | No migration, but the row still cannot be *found* by audit entry without parsing a key, and §4.2 calls the key deterministic-and-opaque |

**Recommendation: A — and the evidence now makes it decisive, not a preference.**

**B is not merely inelegant, it is WRONG.** `step_attempt_id is None` already
carries meaning: *this is an instance-level row* (the trigger payload). An
instance-level audit entry — `workflow_started`, a trigger-time failure — has
no step attempt, so overloading the column would put an audit id where `None`
is the signal, and destroy the one distinction that column makes. There is no
spelling of B that survives that.

**C** fails the standard the reviewer set for the catalog digest in the same
breath: a record you cannot FIND by its natural key is not addressed, it is
merely stored. §4.2 also treats the key as deterministic and opaque, so
parsing an id back out of it would make the key a schema.

**A works, and the pieces are already there:**

- `AuditEntry.id` is a `default_factory` uuid assigned at CONSTRUCTION, before
  any I/O — so the address exists at vault time for free.
- It is immutable, which is the property §4.2 requires of `step_attempt_id`
  and the reason that column was chosen over the attempt NUMBER.
- Instance-level entries keep working: `step_attempt_id` stays `None` and
  `audit_entry_id` carries the address. The two columns answer two questions.
- Idempotency comes free. Step outputs need "a retry re-addresses the same
  object"; audit entries are each distinct, so the entry id IS the identity.

## The ordering this implies, and why it is already settled

Today the chokepoint projects and *then* constructs the entry, so the id does
not exist when it is needed. The fix is three lines, and the pattern to follow
is the step-output path's, which is explicit about the hazard:

> vault the raw **BEFORE** persisting — under the flip the vault write is
> durable-or-fail, because *a lost write must fail the step, not silently drop
> the raw*.

So: **construct the entry (id assigned, no I/O) → vault the raw by that id,
durable-or-fail → project → append.** An orphaned vault row (raw kept, audit
append failed) is the safe direction and the step fails loudly; the reverse —
audit written, raw gone — is exactly what the existing comment refuses. And
because `_audit` is a single chokepoint, no writer can bypass it.

## Migration shape

```sql
ALTER TABLE raw_trace ADD COLUMN audit_entry_id TEXT NULL;
-- idempotency_key already carries the discriminator; the column makes the
-- row FINDABLE by audit entry, which is the part C cannot do.
CREATE INDEX ix_raw_trace_audit_entry ON raw_trace (org_id, audit_entry_id);
```

Additive and nullable, so every existing row stays valid and rollback is
dropping an unused column. Applied to the running DB in the same action as
the commit, with the service stopped, per the standing rule.

## Decision 2 — which audit details get vaulted

Vaulting every audit detail would roughly double audit storage and vault a
great deal of content-free governance metadata.

| Option | Behaviour |
|---|---|
| **A. Only details that LOSE something to projection** (recommended) | Vault iff `project_audit_detail_at_rest(action, detail) != detail`. Self-limiting, and exactly the set whose recovery would otherwise be destroyed |
| B. Vault every audit detail | Simple, uniform, and mostly stores metadata that was never withheld |
| C. Vault an explicit action allowlist | Needs maintaining, and a missed action silently destroys raw — the drift class the ledger already tracks |

**Recommendation: A**, with the predicate being the projection itself, so the
rule cannot drift from what projection actually does.

---

## What gets built once decided

1. `RawTraceKind.AUDIT_DETAIL` + the addressing from decision 1 (+ migration).
2. The `_audit` chokepoint vaults the raw detail **before** projecting it —
   one chokepoint, so no writer can bypass it, which is the property that
   took three rounds to establish for step outputs.
3. Rehydration by audit entry, so a grant holder recovers what a reader cannot.
4. **Only then** at-rest filtering tightens to match the read path — the
   round-8 finding — because by then the raw survives somewhere.
5. Tests: the multiplicity case (N tool calls in one step attempt produce N
   recoverable rows) is the one that would have been silently broken by the
   existing key.

## Why it is not built yet

Decision 1 changes a production table, and the standing rule is that a
migration is applied to the running DB in the same action as its commit. That
is an operator's call, not a thing to slip in while a review is out — and the
multiplicity problem means guessing wrong would silently destroy exactly the
records this is meant to preserve.


---

# Part 2 — The catalog snapshot / digest

**Status: DESIGNED, NOT BUILT.** Also needs schema.

## Correction to an earlier claim of mine

I said this item "needs no schema change". **That was wrong**, and checking it
before repeating it is the point of the claims rule. It needs two pieces of
persistence.

## What it is for

Exactly one thing: deciding whether a tool NAME may be shown in a projected
tool-call record (`_resolved_tool_name`). Since round 5 removed the
process-wide catalog, **no caller supplies one, so no tool name is ever
shown** — a real operability cost carried deliberately until this lands.

**Note the contract it belongs to.** Unlike audit-at-rest, this is
**Contract A** (what an ordinary reader sees), not B1. It is not gated behind
B1's trigger; it is simply unbuilt.

## The reviewer's specification

> identify a retained, immutable snapshot whose contents are verified during
> reconstruction; unavailable history should produce an explicit unsupported
> result. A digest without the corresponding snapshot is insufficient.

## Why it needs schema

1. **The snapshot must be retained.** A catalog is a sorted list of tool
   names (~30 strings), but it is **deployment-specific**: the per-account
   tools (`email_label_apply__qspencer_gmail_com`) are wired at boot from
   `.secrets/gmail/<account>/`, so it changes when accounts change. It cannot
   be a constant, and a file on disk is neither per-org nor durable. →
   `catalog_snapshots(digest PK, names, created_at)`.
2. **The digest must be recorded with the projection.** Its natural home is
   the ROW, beside `projector_version`. It deliberately cannot go inside the
   projected output: we just REMOVED the version stamps from there because
   nothing read them and a supplied value survived as projection metadata.
   Putting a new stamp back into the same place would re-open exactly that.
   → a column.

## Reconstruction, per the specification

- Resolve against the snapshot the recorded digest names.
- **Verify the snapshot hashes to that digest** before trusting it — a digest
  whose snapshot has been edited is not a digest.
- A digest with no retained snapshot → **`unsupported`**, audited, degraded,
  exactly as an unknown projector version behaves. Never `mismatch`, and
  never a silent fallback to "show the name anyway".

## Decision 3 — do we take schema changes for deferred-contract work?

Both parts need a migration against the running Postgres, applied in the same
action as the commit. The question is one question:

| | |
|---|---|
| **Yes** | Build both. Audit vaulting first (it preserves recovery), then this. Two migrations, applied to the live DB, with the service stopped for each. |
| **Not yet** | Both stay designed-and-unbuilt. The costs continue: audit details keep storing more than any reader can see, and **no tool name is shown to anyone**, which is the operability price of round 5's fix. |

**Recommendation: yes, but after the review line settles.** Neither item
reopens the ownership architecture, so they do not conflict with the open
round — but a production migration is a poor thing to do while a package is
out and a return might touch the same files.
