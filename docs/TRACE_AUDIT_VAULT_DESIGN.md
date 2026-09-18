# Audit-detail vaulting — design note (blocked on two decisions)

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

**Recommendation: A.** B and C both encode a fact in a place that does not
declare it, which is the failure mode of this entire review series.

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
