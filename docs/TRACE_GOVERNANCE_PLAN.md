# Trace Governance — Design

Status: **architecture FROZEN (external review v4); narrow v5 + v6 fold the
implementable-semantics corrections; no cut yet authorized.** Internal
review folded (7 conditions). External reviews v1–v4 folded (v4 froze the
architecture); v5 folded six representability fixes; v6 folds six
implementable-semantics fixes (grant approval modes, arbitrary-prose removal,
system audit-before-decrypt, cross-system fencing, manifest finalization,
grant-expiry transition). **The TG2 human release path is design-approved
(external review v6);** TG2's system-access sub-path (§3.2) and TG1 remain
blocked pending the v6 folds below; TG3 stays gated. Not built; build is
trigger-gated (§0).

**Design status: SETTLED as of v6 (2026-08-01).** Six review rounds took the
findings from conceptual redesign (v1–v2) to Postgres partial-index semantics
(v6) — the design has converged. This is the **terminal design revision**:
the plan is not reopened for further speculative review rounds. Remaining
precision is pinned by **tests at build time** (the §8 criteria are the
contract), not by more refinement of an unbuilt spec.

**Build status: TG1 BUILT (2026-08-01).** The raw-trace privilege is now a
scoped, audited, revocable grant distinct from administration (Contract A):
`raw_trace_grants` state-machine table + Alembic 0006; `RawTraceGrantService`
(request/approve/revoke/expiry, two approval modes, self-escalation +
approver-distinct + duplicate-active guards); `ADMIN_TIER` retired in favor
of `_raw_reader_for_org` at every read surface (instance detail, explain,
both audit endpoints, WS); Administrator-gated grants API; revoke-on-
deactivation/org-transfer. Criteria 1, 2, 5, 11 (and 21/25/32/33's lifecycle
content) test-pinned across `test_raw_trace_grants{,_api}.py`,
`test_audit_redaction.py`, `test_org_isolation.py`. **The intended
operational break is live: an Administrator without a grant reads no raw.**

**Build status: TG2-human BUILT (2026-08-01).** The release-boundary audit
(§3.1): `api/raw_trace_audit.py::decide_raw_release` emits the append-only
`raw_trace_access_attempted` + `raw_trace_release_decided` pair (one
correlation id) at every raw surface — instance detail, explain, both audit
endpoints, WS (one pair per raw-bearing delivered frame) — before any raw
byte leaves; fail-closed to projected + `redaction_reason:
access_audit_unavailable` on any audit-append failure; a below-grant read
emits no access event. Detail/explain responses carry `raw_included`.
Criteria 2/3/28 test-pinned (`test_raw_trace_release_audit.py`). Scoped to
pre-TG3 (raw inline/atomic → outcomes `released` | degraded-`projected`;
`partial`/`retrieval_failed`/`integrity_failed` + delivery-observed arrive
with the vault).

**Prerequisite DONE (2026-08-01): the immutable-attempt model** is written
into `EXECUTION_SEMANTICS` §3a (normative) with an explicit first-class
`attempt` number (Alembic 0007) — the step-attempt id the vault keys on
(§4.1) is now a guaranteed identity.

**Build status: TG3a BUILT (2026-08-01).** The raw-trace vault +
write-time projector, as a **dark dual-write**: `raw_traces` table (Alembic
0008) + `RawTraceVaultRepo` (abstract, opaque ids, idempotent `put`); the
projector (`trace_vault.py`, §1.2/§1.4 default-deny — tool_calls / free-form
model output / recall / errors are raw, structured fields are not) + the
collision-free idempotency key on the immutable step-attempt (F2); the engine
vaults the trigger payload + each attempt's raw output/error keyed on the
step-attempt id, while the operational store keeps its inline copy
AUTHORITATIVE (a vault-write failure is logged, never raised). Additive and
non-behavioral for readers. `test_raw_trace_vault.py`. **Next: TG3b** — the
flip: rehydration from the vault + operational store goes safe-only +
durable-or-fail + the finalized dependency manifest (§5.1) + system-access
audit (§3.2). Trigger to ship to an external org is unchanged (§0) and still
requires the chosen B-contract (TG3d).

The coupled design for the three F3 pre-external-organization gates the
external code review left open after the read-surface redaction closed
(rounds 1–4, HEAD `4de5d58`):

1. A **raw-trace privilege distinct from ordinary administration.**
2. **Audited raw access** — every raw-trace read is itself recorded.
3. **Storage-level trace separation** — raw payloads live apart from the
   operational record, so the ordinary tables never contain them.

These are one design, not three: the privilege gates the vault, the audit
records access to it, and the storage split is what makes the vault a
distinct thing to gate and audit. This document decides the shape; it does
NOT authorize the build (trigger below).

### 0.3 The five frozen contracts (external review v3)

The reviewer reduced the remaining design work to five contracts; each is
now frozen at the cited section and gates the named cut:

| # | Contract | Frozen at | Gates |
|---|---|---|---|
| 1 | What qualifies as safe structured output | §1.4 | TG3a |
| 2 | Which raw belongs to the durable execution snapshot | §5.1 | TG3b/c |
| 3 | Vault transaction + crash-recovery state machine | §4.2 | TG3a/b (external-vault form) |
| 4 | Platform-wide grant authorization | §2.1 | TG1 |
| 5 | The append-only raw-access audit event model | §3.1 | TG2 |

## 0. Why now, the trigger, and the contracts

**Trigger to BUILD:** before the first external organization is
provisioned (same gate as `ROLES_PLAN` §8 and `THREAT_MODEL` §5a). Until
then the host operator and Administrators are trusted (§5a).

### 0.1 Three contracts, three adversaries (approved)

- **Contract A — application-level governance (TG1 … TG3c).** Host + DB
  operator trusted. A same-DB vault suffices; protects against ordinary
  users/Org Admins, accidental new surfaces, application-authz defects,
  operational queries.
- **Contract B1 — database-operator resistance.** DB dumps/backups/replicas
  carry no decryptable plaintext without a separately-held key. Honest
  claim: infra operator running the engine remains trusted.
- **Contract B2 — infrastructure-host resistance.** Host operator cannot
  impersonate or inspect the decrypting runtime — external vault under
  separate authority / attested execution / customer-mediated decrypt. Its
  own threat-model + infra design (TG3d).

**Decision (2026-08-01): B2 is the destination contract; delivered
B1-first.** The gate's ultimate requirement is B2 (the operator running the
platform cannot obtain a tenant's raw mail) — that is the C6 trust-wedge
differentiator and where regulated buyers are heading, and B1-terminal would
stop one step short of it. But B2's mechanism (attested/enclaved runtime or
external decrypt authority) is speculative infra spend before a first
external org, so the build ships **B1 first** (envelope encryption, keys
outside the DB role) and pulls B2 forward when a customer contract requires
it. This is only non-throwaway because of §0.2's vault abstraction and the
§4.1 encryption fields: the B1→B2 delta is the *decrypt-runtime* boundary,
not the data model.

**Load-bearing invariant, decided now:** the first encrypted write uses
**per-organization keys** (customer-managed-capable / BYOK-ready), never a
single platform-wide key. A platform key would make the B1→B2 upgrade a full
re-encryption of the most sensitive table; per-org keys make it a
key-custody + runtime change. `key_id` (§4.1) is per-row and per-org from the
first ciphertext.

**Interim residual, stated:** under B1 the infrastructure operator remains
trusted with plaintext. Acceptable pre-first-external-org; an external org is
never provisioned until the gate's *named* contract is in effect (the plan
forbids shipping the residual to a customer who wasn't told). `THREAT_MODEL`
§5a is amended only in the commit that lands each boundary (B1, then B2).

### 0.2 The vault abstraction precedes schema lock-in (approved)

TG3a is built against an **abstract vault repository with opaque object
IDs** + the reserve→persist→commit state machine (§4.2), so the same-DB vs
separate-vault choice does not rewrite TG3a/b.

## 1. What "raw trace" means (the asset being governed)

- Tool call input/result (`StepExecution.output.tool_calls[]`).
- Trigger payloads (`WorkflowInstance.trigger_payload`; raw email; arbitrary
  webhook body).
- Recalled correspondent history (`recall`).
- Free-form model output — sensitive by **taint** (§1.1), not tool-use.
- Error/exception text (step/provider/validation/exception).
- Duplicates: `workflow_instances.context`, `step_completed`/`tool_call`
  audit `detail`, escalation detail, cost metadata carrying request tags.

### 1.1 Sensitivity is by TAINT (approved)

Free-form model output from any step receiving raw or raw-derived context
is **raw**, unless transformed through a **safe structured-output contract**
(§1.4). Taint propagates downstream. First build vaults ALL free-form output
+ error text and persists only §1.4-approved fields.

### 1.2 Typed default-deny projector registry (approved)

Keyed by trigger type / asset kind; unknown trigger types and unknown raw
kinds are empty/default-deny and vault-only. The registry **governs model
output and errors too** (§1.4), not only trigger types.

### 1.3 Per-field consumer justification (approved)

Each preserved field carries: exact runtime consumer, why rehydration can't
supply it, which roles may see it, retention need, whether an opaque id
suffices. `message_id` justified (pinned mutation); `from-address` is
grant-gated / opaque-by-default; webhook routing keys default to opaque.

### 1.4 CONTRACT 1 — safe structured output (external review v3 F1) — FROZEN

Schema validation alone is NOT safety: `{"summary": "...SSN..."}`,
`{"reason": "the email says ..."}`, `{"error_detail": "provider returned
the full document ..."}` all validate. A field is safe-to-persist only via a
**registered, versioned projection** — not merely a validated type. Each
persistable field declares: allowlisted name + type, max size, whether
arbitrary strings are permitted (default no), exact source, whether it may
carry user/third-party content, rendering + authorization policy.
**Unknown fields default-deny (vault).** The first-build rule:

```
Persistable WITHOUT the vault (operational store):
  - closed enums
  - booleans
  - bounded numeric values
  - opaque internal IDs
  - deterministic status codes
  - explicitly approved bounded metadata

Vault by default (NOT in operational store):
  - free-form strings
  - model explanations / summaries
  - error & exception messages
  - external identifiers not individually approved
  - arbitrary lists / maps
```

A structured output containing an arbitrary secret string stays vaulted
unless **every** field belongs to an approved safe-output projector
(acceptance criterion 18). The safe-output projector is versioned like every
other projector (§4.3).

**Memory-introspection:** facts-mode requires the grant; counts-mode
role-gated.

## 2. Decision 1 — the raw-trace privilege (grant-as-STATE-MACHINE)

The v4 flat record could not represent a two-person approval (no pending
state, no distinct approver, no rejected/cancelled request, no tenant-
approval artifact, no atomic activation). The grant is therefore a **state
machine** (external review v4 F1):

```
raw_trace_grants
  id
  principal_id
  scope                    -- exactly one of: org_id | platform_wide
  state                    -- pending | active | rejected | cancelled | revoked | expired
  approval_mode            -- dual_administrator | tenant_authorized   (§2.1)
  requested_by
  requested_at
  approved_by   nullable   -- required iff dual_administrator
  approved_at   nullable
  external_approval_ref nullable   -- required iff tenant_authorized; FK to a structured approval record
  expires_at    nullable   -- mandatory (non-null) for platform-wide
  reason_code              -- CLOSED enum, NOT free text
  ticket_ref    nullable   -- OPAQUE internal ticket id (NOT free-form prose — v6 F2)
  revoked_by    nullable
  revoked_at    nullable
```

`reason_note` is REMOVED (external review v6 F2): a "bounded string with a
no-raw-content policy" is not an enforcement boundary — an Administrator can
paste a message body into it, reopening the exact free-form-string path
Contract 1 (§1.4) closes. Governance rationale is now a closed `reason_code`
plus an opaque internal `ticket_ref`; any evidence that itself carries
customer content stays in the separately-governed approval record, never an
ordinary column.

**Legal transitions (external review v6 F1):**

```
pending → active      pending → rejected      pending → cancelled
active  → revoked      active  → expired
```

Default: no grant for anyone. DB constraints: exactly one of
`org_id`/`platform_wide`; **uniqueness applies to ACTIVE grants only** — a
partial unique index on `(principal_id, scope) WHERE state = 'active'`.
Lifecycle: request/approve/revoke/expiry each separately audited; revoked on
deactivation + org transfer; no silent self-escalation. **Activation is an
atomic compare-and-set** (`pending → active`) that validates the approval
artifact, expiry, and the distinctness/exclusion rules of the chosen
`approval_mode` (§2.1) in ONE operation (criterion 25).

**Expiry is a durable DB transition, not a dynamic index predicate
(external review v6 F6).** An `active` row whose `expires_at` has passed
still matches the `WHERE state = 'active'` partial index — Postgres cannot
use `now()` as a reliable partial-index predicate — so an expired-but-not-
transitioned row would block a replacement grant. Therefore, before
activating a new grant for the same `(principal, scope)`, one atomic step:
lock the existing active row; if `expires_at <= now` transition it to
`expired` + emit the expiry audit; then activate the new grant. A background
sweeper gives timely visibility, but the **request-time expiry check remains
authoritative**.

**Administrator bypass is scope-constrained:** raw allowed only when base
resource authz succeeds AND an active grant covers the **target** org. An
org-A grant does NOT travel to org B; cross-org raw needs a platform-wide or
target-org grant.

Replace `ADMIN_TIER` at all four consult sites (audit projection, instance,
`explain`, `ws.py:130`'s independent `raw_reader`).

### 2.1 CONTRACT 4 — platform-wide grant authorization (external review v3 F4, v6 F1) — FROZEN

A platform-wide grant activates through EXACTLY ONE of two explicit modes
(v5 left both paths in the schema without saying which fields each requires):

```
approval_mode = dual_administrator | tenant_authorized

dual_administrator:
  approved_by REQUIRED;  external_approval_ref NULL
  requester, approver, recipient ALL distinct
  UNAVAILABLE in a single-Administrator deployment

tenant_authorized:              -- the gate's contract requires the tenant to authorize
  external_approval_ref REQUIRED;  approved_by NULL (or a defined internal activator)
  requester and recipient distinct
  the approving TENANT identity is not the recipient
  the approval artifact's scope EXACTLY matches the grant scope

Both modes: mandatory expiry + reason_code; each step separately audited.
```

Org-scoped grant: issued by a DIFFERENT Administrator than the recipient;
audited. The activation compare-and-set (§2) validates the approval artifact,
expiry, and the chosen mode's distinctness/exclusion predicate in ONE
operation — exactly one mode must be satisfied. A single-Administrator
deployment cannot mint a platform-wide grant via `dual_administrator`, and
`tenant_authorized` is available only when a real external tenant exists to
approve — consistent with the §0.1 dual-control precondition.

**Governance metadata must not become a raw-data leak (external review v4
amendment 7).** An Administrator could paste a message body or customer name
into a free-form justification. So: `reason_code` is a **closed enum**;
`reason_note` is a short bounded note carrying an explicit no-raw-content
policy; the `external_approval_ref` points at a **structured approval
record** (signer identity, scope, timestamps, signature/reference metadata —
NOT an arbitrary blob), and any supporting evidence that itself contains
customer content lives in a separately-governed attachment location, not an
ordinary column.

## 3. Decision 2 — audited raw access

No content hash (low-entropy oracle). The audited boundary is **release**
(bytes leaving the authorization boundary), NOT client receipt — the system
can only prove it released bytes to its transport, never that a remote client
consumed them (external review v4 F4).

### 3.1 CONTRACT 5 — release-boundary audit model (external review v3 F5, v4 F4) — FROZEN

Append-only (no event mutation). The **second** event is the security
guarantee and **commits before any raw byte is released**:

```
raw_trace_access_attempted        -- committed BEFORE vault retrieval
  request_id                      -- shared correlation id
  actor  instance  surface        -- detail | explain | audit | ws | export
  intended_kinds

raw_trace_release_decided         -- committed BEFORE any raw bytes are released
  request_id                      -- same correlation id
  outcome ∈ { released, partial, projected,
              retrieval_failed, integrity_failed }
  released_kinds

raw_trace_delivery_observed       -- OPTIONAL, best-effort telemetry only
  request_id
  outcome ∈ { send_completed, transport_cancelled, unknown }
```

`raw_trace_release_decided` is the authorization-boundary record and MUST
commit before the raw crosses the response boundary: for WebSockets, before
the raw-bearing frame is sent; for HTTP streaming, before any raw content
begins streaming. `raw_trace_delivery_observed` is operational telemetry and
**must NOT be described as proof of client receipt**.

- **Cardinality:** HTTP detail/explain — one attempt+release pair per request
  per instance. Multi-instance — one pair **per returned instance**.
  WebSocket — one pair per raw-bearing frame, each with its own correlation
  id; an audit failure projects that one frame and never closes the
  connection. Export/batch — one job pair + scoped counts.
- **Partial retrieval:** **explicit partial** — `raw_included: partial` + a
  machine-readable returned-vs-withheld kind list; `outcome=partial`. Silent
  merge-some/withhold-others is forbidden.
- **Degradation is explicit:** `raw_included:false`, `redaction_reason:
  access_audit_unavailable | retrieval_failed | integrity_failed`.

Caveats: audit-of-the-auditors deferred; under B2, access logs live outside
the accessor's deletion authority.

### 3.2 CONTRACT 5b — internal engine vault access is audit-BEFORE-decrypt and fail-closed (external review v4 F5, v6 F3) — FROZEN

Resume, retry, fork, migration, and erasure fetch (and under B1/B2 decrypt)
raw content on the engine's own path — not a human read, but still access to
the protected asset. v5 recorded it AFTER the fact; v6 requires the same
before-access, fail-closed discipline as the human release path, else a
defective/compromised path could touch plaintext and then fail to record it.
Two events, mirroring §3.1:

```
raw_trace_system_access_attempted     -- committed BEFORE any fetch/decrypt
  workload_identity                   -- the narrow runtime capability, not a user
  purpose ∈ { execute, resume, fork, retry, migration, erasure }
  org_id  instance_id  step_attempt_id  kinds  correlation_id

raw_trace_system_access_completed
  correlation_id
  outcome ∈ { succeeded, retrieval_failed, integrity_failed,
              authorization_failed, operation_failed }
```

Fail-closed rules: **no plaintext enters application memory before the
attempted-access record commits.** If that append fails: a resume/retry/fork
**fails or pauses explicitly**; migration and erasure **report incomplete
work**, never silently skip. The attempted record MUST prove the caller held
the narrow engine capability and its purpose matched an allowed operation.
(Alternative accepted: the attempted append mints a short-lived vault access
permit the fetch must present.) Under B1/B2, both the workload-identity
authorization AND the attempted-access record live **outside the ordinary
application DB role**. This §3.2 sub-path is what keeps the *bundled* TG2
blocked; the human release path (§3.1) is independently design-approved.

## 4. Decision 3 — storage-level trace separation

### 4.1 Execution identity frozen; schema carries all version fields (external review v3 F6/F7)

**One immutable step-attempt row per attempt** (a retry creates a new row).
The vault keys on that persisted attempt identity.

```
raw_traces
  id                     -- opaque object id (portable, §0.2)
  org_id
  instance_id            (cascade per §5)
  step_attempt_id        nullable   -- FK to the immutable step-attempt row
  kind                   -- tool_calls | model_output | error | trigger_payload | recall | (future→vault-only)
  state                  -- §4.2 state machine
  idempotency_key        -- step-level:     hash(org_id, instance_id, step_attempt_id, kind)
                         -- instance-level: hash(org_id, instance_id, "instance", kind)
  raw_schema_version
  projection_schema_version
  projector_version
  -- encrypted form (B1/B2) additionally:
  enc_alg_version   key_id   nonce   ciphertext_format_version
  payload                -- JSONB (A) | ciphertext (B1/B2), AEAD-bound to
                         --   (org_id, instance_id, step_attempt_id, kind, raw_schema_version)
  created_at
```

- **Uniqueness:** `(step_attempt_id, kind)` for step rows; a **partial
  unique index** on `(instance_id, kind) WHERE step_attempt_id IS NULL` for
  instance-level rows. At most one row per kind per attempt.
- **The safe operational row also records `projection_schema_version` +
  `projector_version`** used to create it — otherwise the disagreement check
  (§4.3) can't select the correct historical projector.
- `raw_refs` map (kind → opaque id) on the safe record; never a scalar ref.
- Attempt isolation: attempt N never rehydrates N−1's raw except via an
  explicit prior-attempt rule.

**Execution-model change (goes in `EXECUTION_SEMANTICS`, external review v3
F7):** the immutable-attempt decision changes more than the vault. That doc
must define: which row is the current logical step state; retry ordering;
how APIs display multiple attempts; which attempt a downstream step
consumes; whether a cancelled attempt may hold raw; how pause/resume/crash
select the active attempt; whether cost/audit attach to the attempt or the
logical step; and migration from today's mutable step rows. TG3b does not
land until that model is written.

### 4.2 CONTRACT 3 — vault transaction + crash-recovery state machine (external review v3 F3) — FROZEN

States and the only legal transitions:

```
RESERVED   -- identity reserved (idempotency_key minted); no payload yet
STORED     -- raw payload durably + idempotently written to the vault
REFERENCED -- operational row committed, pointing at this object
COMMITTED  -- final marker; object is authoritative
ABORTED    -- reconciled away (never REFERENCED)

RESERVED → STORED → REFERENCED → COMMITTED
RESERVED → ABORTED           (nothing referenced it)
STORED   → ABORTED           (no operational reference exists)
```

Same-DB (Contract A): the REFERENCED+payload write share one transaction;
`RESERVED → STORED → REFERENCED → COMMITTED` collapse into a **single
explicit atomic transition** for that backend (external review v6 F4 — the
"only legal transitions" list gains this same-DB direct edge rather than
pretending the intermediate states persist separately). Separate-vault
(B1/B2): the protocol below, with these **recovery rules**:

**Cross-system fencing/intent protocol (external review v6 F4).** For a
separate vault + operational DB, "the reference-check and `→ ABORTED` are one
atomic decision" is NOT obtainable from an ordinary transaction — a vault
lease alone can't stop a delayed writer from later committing a reference to
an object the reconciler just aborted. The frozen fencing mechanism:

```
1. Operational DB: create an authoritative raw_reference_intent
     (intent_id, fencing_generation, (org,instance,step_attempt,kind), state)
2. Vault: persist the object carrying the SAME intent_id + fencing_generation
3. Operational DB: commit the reference AND consume the intent atomically
     — the writer first re-verifies its intent + vault generation are current
4. Reconciler may ABORT an object ONLY when ALL hold:
     no committed reference exists
     AND no live intent exists
     AND the intent is terminal or expired
     AND the reconciler holds the current fencing_generation
```

Conservative alternative (acceptable): never abort an uncertain object until
an authoritative operational-side **tombstone** says no future reference can
be committed for that identity.

**Referenced-but-not-committed reads (external review v6 F4).** The normal
crash state `vault=STORED, operational row durably REFERENCES it, COMMITTED
marker absent` must NOT read as missing/corrupt. A resume/fork arriving
before the reconciler either does an **idempotent lazy promotion** to
COMMITTED, or recognizes the valid operational reference and treats the
object as recoverable while scheduling promotion.

- **Idempotency key is deterministic and collision-free** (external review v4
  F2): step-level = `hash(org_id, instance_id, step_attempt_id, kind)` —
  keyed on the *immutable* `step_attempt_id`, NOT `hash(instance_id, attempt,
  kind)`, which collides when two different steps in one instance are both on
  attempt 1 with the same kind. Instance-level (trigger payload, no step
  attempt) = `hash(org_id, instance_id, "instance", kind)`, a separate space.
  A retry after an uncertain write re-addresses the *same* object.
- **The reconciler checks operational references before deleting anything.**
  A **REFERENCED object is recoverably promoted to COMMITTED after a crash,
  never classified as an orphan** merely because the final marker wasn't
  written. Only RESERVED/STORED objects with **no** operational reference are
  ABORTED, and only after a safety window.
- **Concurrency (external review v4 amendment 9):** every transition is a
  **conditional compare-and-set / version check**, never a blind update, and
  the reconciler holds a **reconciliation lease** (or equivalent ownership).
  The operational-reference check and the `→ ABORTED` transition are ONE
  atomic decision, so a reconciler cannot abort a STORED object while a live
  writer is committing its operational reference (criterion 31).
- **Crash after REFERENCED, before COMMITTED:** recovery promotes to
  COMMITTED (the operational row proves the reference).
- **Vault says STORED but the operational write outcome is unknown:**
  treated as STORED-unreferenced; the operational write is retried
  idempotently; the object is ABORTED only if the operational row provably
  does not (and will not) reference it.
- **One attempt cannot claim another attempt's object** — the key is on
  `step_attempt_id`, and rehydration validates `(org, instance,
  step_attempt_id, kind)` match (§4.3).

Execution-critical raw (trigger + prior outputs, §5.1) is durable-or-fail: a
failure before REFERENCED marks the step FAILED/PAUSED with a persistence
error and never reports it durably COMPLETED. Diagnostic-only material may be
best-effort.

### 4.3 Rehydration integrity + exact disagreement predicate (approved)

Resume/fork rehydrate the FULL context from the vault. Rules: missing →
fail; malformed → fail; org/instance/step/attempt mismatch → security error;
**disagreement** = `project(kind, raw_payload, projector_version) !=
stored_safe_projection` using the versioned projector + canonical
serialization + the schema versions recorded on both rows (§4.1); Contract-B
records are AEAD-bound so a vault-row edit is detected. Fork copies/immutably
binds its required raw. Retry selects the intended attempt; abandoned-attempt
rows never rehydrate. Tests: missing, corrupt, mismatched, wrong-attempt,
deleted-source.

### 4.4 Read path

Ordinary read returns the safe operational row as-is; a privileged audited
raw read fetches+merges (§3 ordering). Read-time projection is
belt-and-suspenders.

## 5. Lifecycle

### 5.1 CONTRACT 2 — the execution snapshot is defined by the dependency graph (external review v3 F2) — FROZEN

Retention CANNOT be classified globally by raw kind — the same `tool_calls`
or `model_output` value is execution-critical in a workflow that pins/branches
on it and diagnostic in one that doesn't. The **execution snapshot** is
therefore derived from the actual persisted context dependency graph:

```
Execution snapshot (retained ≥ the full supported resume/fork window) =
  every raw value that contributed to context.trigger
  + every raw value present in context.steps
  + any prior-attempt value reachable by retry logic
  + any value consumed by a condition, pin, prompt, or result mapping
```

**The dependency graph is PERSISTED at execution time, not inferred later
(external review v4 F3).** A retrospective scan of serialized `context` JSON
is inadequate — especially once operational context holds projections rather
than raw values. The engine records an explicit manifest:

```
raw_trace_dependencies
  consumer_instance_id
  consumer_step_attempt_id  nullable
  raw_object_id
  purpose ∈ { trigger, prompt, condition, pin, result_mapping,
              prior_step, retry_context, fork_snapshot }
  required_for_recovery      -- boolean
  created_at
```

The **recovery snapshot is exactly the set of `required_for_recovery`
objects** — never a later inference. The engine writes the dependency edge
**before** the consuming step is reported durably complete (same
durable-or-fail discipline as §4.2).

**The manifest is FINALIZED into an immutable recovery claim (external
review v6 F5)** — persisting the edges is not enough if they can be added,
deleted, duplicated, or read half-written. A step-attempt carries:

```
  snapshot_generation
  snapshot_finalized_at
  snapshot_manifest_hash
```

Rules: dependency rows are **append-only**; a uniqueness rule forbids
duplicate equivalent edges; every referenced raw object must be in a
recoverable state at finalization; the **step-attempt completion transaction
records the finalized manifest generation** (so recovery reads a frozen
generation, never a moving query); a **fork binds a specific finalized
generation**; retention operates **only on finalized manifests**; erasure may
remove objects but must **atomically** flip affected instances to
`recovery_state = incomplete` (or an erasure-terminal state); any change to
recovery availability is audited.

A separate **diagnostic copy** (full tool I/O, model output, errors NOT in
the manifest) may have a shorter window — but **deleting it must not delete
the recovery representation**.

**Snapshot-expiry is an explicit operational state (external review v4
amendment 8)**, not something discovered when a user attempts a resume:

```
  resume_available_until        fork_available_until
  recovery_state ∈ { available, expired, incomplete, corrupt }
```

The API returns a defined status/error per `recovery_state`; a change in
recoverability emits an audit event; a paused instance may itself expire;
operators are warned before expiry. Tests: expire diagnostic traces, resume +
fork, prove identical behavior; prove an instance is explicitly non-resumable
(surfaced state, not a resume-time surprise) once its snapshot expires
(criteria 19, 27, plus 7).

### 5.2 Fork lineage is a persisted, queryable structure (external review v3 F8)

Compliance erasure "follows every forked descendant" is enforceable only
with persisted lineage — never by scanning opaque raw payloads. Define:

- `source_instance_id` (immutable lineage edge) on every forked instance;
  transitive descendants indexed for erasure traversal.
- Copied raw objects retain a **source-subject reference** so erasure of the
  subject reaches the copies.
- Erasure **proves** every descendant was covered (a traversal that returns
  the covered set).
- A descendant that added independent evidence after the fork: its own
  additions follow its own subject, not the source's.

**Cross-organization fork is PROHIBITED in the first build (external review
v4 F6).** Under per-org encryption it would require re-encrypting the copied
raw under the destination org's key, entangle two orgs' authorization
policies, risk granting the destination content its members were never
authorized to see, and embed a cross-tenant data-transfer system inside the
first trace-governance implementation. A cross-org fork returns a deliberate
authorization error; if the capability is ever needed it goes through an
explicit export/import workflow with independent tenant authorization — out
of scope here (criterion 30).

### 5.3 Resource limits (approved)

Max bytes per kind; per step + per workflow; truncation-or-rejection
(explicit — a truncated execution-critical trace forces reject/fail);
compression; metering; retention for oversized/failed.

## 6. Deliberately NOT in the first build

Contract B2 (host-operator resistance) is its own threat-model + infra
design (TG3d). Audit-of-raw-access read restriction deferred. Per-step taint
*tracking* deferred — v1 vaults all free-form output/errors.

### 6a. Live gap already fixed (internal review F4)

The detail endpoint returned raw `trigger_payload` + `recall` to a same-org
Viewer; closed via an interim projection in the commit that introduced this
doc. The typed registry (§1.2/§1.4) supersedes it at TG3a.

## 7. Interaction with existing designs

- **THREAT_MODEL §5a** — narrowed only when the chosen boundary lands.
- **ROLES_PLAN §8** — grant orthogonal to the deferred Auditor role.
- **EXECUTION_SEMANTICS §3** — §4.2 strengthens persistence (durable-or-fail);
  the immutable-attempt execution model (§4.1) is written there before TG3b.
- **AUTH_PLAN** — grants administered via admin-gated `/api/users`.

## 8. Test-pinned acceptance criteria

1. Below-grant recovers no raw tool payload / free-form model output /
   trigger payload / recall / error text from any read surface (incl. detail).
2. Grant-holder recovers raw; each read emits the §3.1 attempt+release pair
   with an outcome and no content hash.
3. Failed access-audit degrades to projected with explicit
   `raw_included:false` + reason; WS projects that one event without closing.
4. Operational tables carry the safe projection only — hostile-sentinel
   assertions over `step_executions.output`, `workflow_instances.trigger_payload`,
   `workflow_instances.context`, `audit_log.detail`, `recall`, step/instance
   error text, escalation detail, cost metadata; the verifier's inventory is
   asset-map-driven, not hand-listed.
5. Cross-org raw by an Administrator requires a platform-wide or target-org
   grant (org-A grant does not travel) and emits `org_bypass` + raw-access
   audit.
6. Migration `0006` vaults inline raw; fixture includes a raw trigger body, a
   recall episode, and a raw-echoing model output.
7. Resume AND fork of a raw-influenced step reproduce straight-through
   behavior; a pinned `apply` step's `message_id` resolves.
8. Boundary matches the named contract (A trusted-documented / B1 no
   decryptable plaintext in dumps / B2 host runtime not impersonable).
9. Required-raw write failure → step FAILED/PAUSED with persistence error,
   never durably COMPLETED-but-unrecoverable.
10. Unknown trigger types and unknown raw kinds default-deny to vault.
11. Grant lifecycle: grant/revoke/expiry audited; revoked on deactivation +
    org transfer; self-grant blocked; duplicate active grant rejected; exactly
    one of org/platform_wide; revocation + expiry effective next request + WS.
12. Missing/malformed/cross-org/wrong-attempt/wrong-step raw references fail
    closed on resume + fork; attempt N cannot read N−1's raw.
13. Fork remains resumable after the source instance is removed; compliance
    erasure follows forked descendants.
14. Migration completion proves zero raw in every operational table
    (asset-map-driven, hostile-sentinel, structural).
15. Raw size + per-run quotas enforced; truncation explicit, never sufficient
    for exact resume/fork.
16. Every raw-capable surface mechanically inventoried (HTTP, exports, CLIs,
    logs, exception reporting, WS, memory introspection, future debug).
17. Projector-version change does not make a pre-change raw row read corrupt.
18. **(Contract 1)** A structured output containing an arbitrary secret string
    stays vaulted unless every field belongs to an approved safe-output
    projector; unknown fields default-deny.
19. **(Contract 2)** Expiring diagnostic traces does not break resume/fork —
    every context dependency remains in the execution snapshot; an instance is
    explicitly non-resumable once its snapshot expires.
20. **(Contract 3)** Crash recovery is tested after each RESERVED / STORED /
    REFERENCED / COMMITTED boundary; no REFERENCED object is deleted as an
    orphan; a retry re-addresses the same object; one attempt cannot claim
    another's object.
21. **(Contract 4)** Platform-wide grants activate via EXACTLY ONE
    `approval_mode` — `dual_administrator` (approver required, distinct from
    requester + recipient) or `tenant_authorized` (approval artifact required,
    scope-matched, approver ≠ recipient); org-scoped grants require a
    different grantor.
22. **(Contract 5)** Audit distinguishes released / partial / projected /
    retrieval_failed / integrity_failed under one shared correlation id;
    partial access is explicit, never a silent merge.
23. **(Correction 6)** The stored safe projection and the raw record both
    identify the exact projector + raw + projection schema versions used.
24. **(Correction 8)** Compliance erasure traverses a persisted multi-
    generation fork-lineage fixture and returns the covered descendant set.
25. **(v4 F1)** A platform-wide grant cannot become active without a persisted
    pending request AND a distinct persisted approval; concurrent or duplicate
    approval cannot bypass recipient exclusion (atomic compare-and-set).
26. **(v4 F2)** Two different step attempts in one instance with the same
    attempt number and same raw kind receive DISTINCT idempotency identities;
    the instance-level trigger identity is a separate space.
27. **(v4 F3)** Every recovery-required raw object has a persisted
    `raw_trace_dependencies` edge, and the stored snapshot manifest exactly
    matches the objects used by straight-through execution.
28. **(v4 F4)** No raw byte crosses the HTTP or WebSocket release boundary
    unless the immutable `raw_trace_release_decided` event has committed;
    `raw_trace_delivery_observed` is telemetry, never asserted as receipt.
29. **(v4 F5, v6 F3)** Engine resume / retry / fork / migration / erasure
    vault access commits a `raw_trace_system_access_attempted` record BEFORE
    any fetch/decrypt; if that append fails, no plaintext is read and the
    operation fails/pauses (or reports incomplete for migration/erasure).
30. **(v4 F6)** A cross-organization fork is explicitly rejected with an
    authorization error (first build).
31. **(v4 amendment 9)** A reconciliation race cannot abort a STORED object
    while a valid operational reference is being committed (conditional
    transition + reconciliation lease).
32. **(v6 F1)** A grant with neither/both approval paths satisfied cannot
    activate; `cancelled` is a reachable state; a `tenant_authorized` grant
    whose approval-artifact scope ≠ grant scope is rejected.
33. **(v6 F2)** No free-form governance prose is persisted in a grant row — a
    sentinel secret placed where `reason_note` used to be cannot be recovered
    from the grant surface.
34. **(v6 F4)** A delayed writer CANNOT commit an operational reference to an
    object the reconciler aborted (fencing generation); a
    `STORED`+`REFERENCED`+no-`COMMITTED` object reads as recoverable (lazy
    promotion), never missing/corrupt.
35. **(v6 F5)** A fork binds a specific FINALIZED manifest generation; adding
    a dependency edge after finalization does not change the bound snapshot;
    retention runs only on finalized manifests; erasure atomically sets
    `recovery_state=incomplete` and audits it.
36. **(v6 F6)** An `active` grant past `expires_at` is transitioned to
    `expired` (with audit) during the activation of a replacement for the same
    `(principal, scope)`, and does not block that replacement.

## 9. Cut plan — architecture approved; freezes gate authorization

| Cut | Contents | Contract | Authorization |
|---|---|---|---|
| **TG1** | grant **state machine** (§2) + two `approval_mode`s (§2.1) + closed reason_code/opaque ticket_ref + expiry transition (§2/F6) + target-org bypass + read-site `ADMIN_TIER`→grant (incl. WS) + grants admin API + revoke-on-deactivation/transfer | A | **BUILT 2026-08-01** (memory facts-mode-under-grant deferred to the memory endpoint's own cut) |
| **TG2 (human release)** | raw-access audit: **release-boundary model (§3.1)** + cardinality + explicit degradation, HTTP + WS release-before-send | A | **BUILT 2026-08-01** (`partial`/vault outcomes arrive with TG3) |
| **TG2 (system access)** | `raw_trace_system_access_attempted`-before-decrypt + fail-closed (§3.2) | A | **authorizable (v6 audit-before-decrypt folded — pending re-review)** |
| **TG3a** | typed registry (§1.2) + safe-output contract (§1.4) + vault-all-free-form + abstract vault repo/opaque IDs (§0.2) + schema (§4.1) + collision-free key; **DARK DUAL-WRITE** | A | **BUILT 2026-08-01** (same-DB; cross-system fencing §4.2 lands with the separate-vault B form at TG3d) |
| **TG3b** | rehydration + integrity + versioning (§4.3) + state machine w/ concurrency + fencing (§4.2) + **finalized dependency manifest (§5.1)** + **system-access audit (§3.2)**; only then flip operational to safe-only | A | after the finalized manifest + attempt model |
| **TG3c** | backfill + mixed-version cutover + zero-raw verifier + retention/expiry state (§5.1) + fork lineage + **cross-org-fork prohibition (§5.2)** | A | after §5.2 rule (done) |
| **TG3d-1** | B1 boundary: per-org envelope encryption (keys outside the DB role, BYOK-ready `key_id`), AEAD-bound ciphertext, narrow decrypt identity; dual-control grant; THREAT_MODEL §5a amended (DB-operator-resistant, infra-operator-trusted residual) | **B1** | own design doc; the shippable external-org gate for a B1-accepting customer |
| **TG3d-2** | B2 boundary: move the decrypt identity into an attested/enclaved runtime OR external/customer-mediated key release; THREAT_MODEL §5a amended (host-operator-resistant) | **B2** | own threat-model + infra design; pulled forward when a customer contract requires operator-non-decrypt |

**Sequencing invariant:** TG3a must not flip the operational store to
safe-only before TG3b (else an instance born between them resumes/forks
wrong) — hence the dark dual-write, or TG3a+TG3b ship atomically.

**No external organization is provisioned until the gate's required contract
(§0.1) — up to and including TG3d — is in effect**, not merely TG1–TG3c.
Estimate: TG1+TG2 ~2 days; TG3a–c ~5–7 days; TG3d scoped separately.
