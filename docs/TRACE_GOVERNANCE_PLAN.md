# Trace Governance — Design

Status: **proposed — architecture accepted; TG1/TG2 approvable, TG3
implementation-blocked.** Internal design review folded (7 conditions).
External design review v1 folded ("adopt architecture, revise before
build" — 2 blocking + 8 amendments). External design review v2 folded
(2026-08-01, "architecture accepted with amendments; TG3 remains
implementation-blocked" — 4 blocking + 7 corrections). Not built; build is
trigger-gated (§0), and TG3a–d carry named preconditions (§9).

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

## 0. Why now, the trigger, and the contracts

**Trigger to BUILD:** before the first external organization is
provisioned (same gate as `ROLES_PLAN` §8 and `THREAT_MODEL` §5a). Until
then the host operator and Administrators are trusted (§5a), so raw traces
in the operational store are an accepted single-operator risk.

### 0.1 Three contracts, three adversaries (external review v1 F1, v2 F1)

A `raw_traces` table **in the same database, under the same superuser, is
not a security boundary against the host/DB operator.** And — v2's
correction — even KMS-held keys "unavailable to the DB role" do **not** by
themselves stop a *host* operator who can assume the engine's runtime
credentials, invoke KMS through the engine identity, read process memory
after decrypt, or replace the executable. So the design names three
contracts against three explicit adversaries; each cut states which it
satisfies, and the external-org gate must name which it *requires*:

- **Contract A — application-level governance (TG1 … TG3c).** Host and DB
  operator remain **trusted** (unchanged from §5a). Protects against
  ordinary users/Org Admins, accidental new API/CLI/export surfaces,
  application-authz defects, and operational queries against ordinary
  tables. A same-database vault is sufficient for A; no stronger claim.
- **Contract B1 — database-operator resistance.** Database dumps, backups,
  and replicas contain **no decryptable plaintext** without a separately
  controlled key. Mechanism: envelope encryption with keys held outside the
  DB role. Honest claim under B1 alone: *"raw is protected from direct
  database access, but the infrastructure operator running the engine
  remains trusted."*
- **Contract B2 — infrastructure-host resistance.** The host operator
  **cannot impersonate or inspect the decrypting runtime.** Requires a
  different trust boundary — an external vault service under separate
  authority, confidential-computing / attested execution, customer-mediated
  decryption, or equivalent the host admin cannot simply impersonate. B2 is
  a distinct threat-model + infrastructure design (TG3d), not "B1 plus a
  config flag."

**The external-org gate must name the contract it requires.** If the first
external customer accepts an infra-operator-trusted posture, B1 is the gate
and B2 is deferred with a stated residual. If not, B2 is the gate. Either
way, `THREAT_MODEL` §5a is amended in the commit that lands the chosen
boundary — never before — and TG1–TG3c make **no** operator-untrust claim.

### 0.2 The Contract-B mechanism is chosen BEFORE TG3a (external review v2 F2)

TG3a–c's schema (DB foreign keys, cascades, DB-local raw IDs, a shared
transaction coupling the operational row and its raw payload) assumes a
same-database vault and does **not** transfer to a separate vault
service/DB (no cross-DB FKs; cascades become application workflows; the two
writes can't share an ordinary transaction; `raw_refs` become opaque
external IDs; backup/deletion/migration all change; durable-or-fail needs a
distributed-commit protocol). So the vault mechanism is a **schema-shaping
decision made before TG3a is finalized**, not an afterthought at TG3d.

To avoid a retrofit either way, TG3a is built against an **abstract vault
repository with opaque object IDs**, using a portable
reserve→persist→commit protocol (§4.2) that works for both a same-DB and a
separate-vault backend. The concrete B1/B2 mechanism is chosen when the
gate's required contract is known; the abstraction keeps that choice from
rewriting TG3a/b.

**Why write the spec now:** redact-at-read is a treadmill — every new read
surface is a new leak (the F3 rounds proved it). Storing safe-by-default
and vaulting raw inverts that and ends the finding class.

## 1. What "raw trace" means (the asset being governed)

Sensitive content, today inline across the operational tables:

- **Tool call input/result** — `StepExecution.output.tool_calls[]`.
- **Trigger payloads** — `WorkflowInstance.trigger_payload` (raw inbound
  email; **arbitrary webhook body**; …).
- **Recalled correspondent history** — the `recall` field.
- **Free-form model output** — see §1.1's taint rule (NOT just tool-bearing
  steps).
- **Error/exception text** — step errors, provider errors, validation
  errors, exception detail can copy prompt or external-system content.
- **The same, duplicated** — in `workflow_instances.context`, the
  `step_completed`/`tool_call` audit `detail`, escalation detail, and any
  cost metadata carrying request tags.

### 1.1 Sensitivity is by TAINT, not by tool-use (external review v2 F4)

The prior rule — treat `output_text` as raw only for a *tool-bearing*
step — is too narrow. A tool-free read-only classifier can receive the raw
email body, recalled history, a webhook body, or a prior step's raw output,
then paraphrase it into free text. Sensitivity is determined by the
**inputs that influenced the step**, not by whether the step called a tool.

**Taint rule:** free-form model output from any step that receives raw or
raw-derived context is itself **raw**, unless it is transformed through an
explicitly approved **safe structured-output contract** (validated
enum/structured fields, safe deterministic status, or a summary
demonstrably generated without raw context). **Taint propagates
downstream** — a tool-bearing step feeding a nominally tool-free step does
not launder the secret.

**Practical first version (TG3a):** **vault ALL free-form agent output and
all error/exception text**, and persist to the operational store only:
validated enum/structured fields, safe deterministic status, and explicitly
projected summaries. This is conservative by construction and avoids
per-step taint tracking in v1.

### 1.2 Per-asset projection via a typed default-deny registry (v1 F4)

`redact_tool_data` no-ops on trigger payloads and recall (the live F4 leak,
§6a). "Keep routing fields" is not generically safe — a Gmail message_id is
meaningless for a webhook whose body may itself be credentials. A **typed
projector registry, keyed by trigger type / asset kind, default-deny**:

| Asset / trigger type | Safe projection |
|---|---|
| `email` trigger | declared email routing only (see §1.3 per-field justification) |
| `webhook` trigger | declared idempotency/routing key only, **only if** justified §1.3; body NEVER preserved |
| `schedule` trigger | schedule metadata only (fire time, cron id) |
| `manual` trigger | safe metadata or empty |
| **unknown trigger type** | **empty / default-deny** |
| tool_calls | `safe_tool_call` (input keys, result status, byte size) |
| free-form model output / errors | vaulted (§1.1); operational store gets structured fields only |
| recall | withheld entirely |
| **unknown raw kind** | **vault-only; never enters operational storage** |

Adding a trigger type or raw kind REQUIRES an explicit safe projection
before its content may enter operational storage.

### 1.3 Every preserved field needs a consumer justification (external review v2 F11)

A preserved routing field is still personal content (an email from-address
is PII; a webhook routing key may be a secret). Each preserved field
carries, in the registry, its: **exact runtime consumer**, **why
rehydration cannot supply it**, **which roles may see it**, its **retention
need**, and **whether a stable opaque identifier would suffice instead**.
Example: `message_id` is justified (a pinned mutation needs it, §4.3);
`from-address` is display/memory-lookup only and is NOT shown to every
Viewer — it is grant-gated unless an opaque lookup id replaces it. Webhook
routing keys get the same scrutiny and default to opaque.

**Memory-introspection reconciliation:** the facts-rendering mode of
`/api/memory/summary/{org}/{account}` requires the grant; its counts mode
stays role-gated.

## 2. Decision 1 — the raw-trace privilege (a scoped GRANT record)

A capability **grant record**, not a role, not a bare boolean:

```
raw_trace_grants
  id
  principal_id
  scope                   -- exactly one of: org_id  |  platform_wide
  granted_by
  granted_at
  expires_at   nullable   -- break-glass; null = standing
  reason
  revoked_by   nullable
  revoked_at   nullable
```

Default: no grant for anyone, including Administrators/Org Admins. Distinct
from ordinary administration by construction; composes with any role;
itself auditable; scope-aware; break-glass-capable — without a fifth role.

**DB constraints (external review v2 F7):** exactly one of `org_id` /
`platform_wide` (check constraint); **no duplicate active grant** for the
same principal + scope (partial unique index where `revoked_at IS NULL`);
expired grants are inactive; expiry is enforced on the **next HTTP request
AND next WS event**; expiration is itself visible in grant history (audited).

**Grant lifecycle — test-pinned:** grant/revoke audited
(`raw_grant_granted`/`raw_grant_revoked`); **revoked on user deactivation
and org transfer**; **no silent self-escalation** (a self-grant requires a
*different* Administrator as grantor); revocation effective next
request + next WS event.

**Administrator bypass is scope-constrained (external review v2 F7).** An
Administrator holding a grant for org A must NOT read raw while bypassing
into org B. The exact rule:

> raw access allowed only when base resource authorization succeeds AND an
> active grant covers the target organization.

Cross-org raw therefore requires a **platform-wide** grant or an explicit
grant for the **target** org — an org-A grant does not travel. Platform-wide
grants are a distinct, separately-audited authorization (who may issue one
is named in TG1).

**Residual (single-admin):** with one Administrator, self-grant-prohibition
is only procedural. **Dual control (two distinct Administrators, or
external-org approval) is a TG3d / gate precondition** — a single-operator
deployment does not meet the separation bar.

**Replace `ADMIN_TIER` at all four consult sites** — audit projection,
instance endpoint, `explain`, and `ws.py`'s independent `raw_reader`
(`ws.py:130`, satisfiable from the already-loaded user row). After this
lands no one is auto-granted (intended break).

## 3. Decision 2 — audited raw access

Every read that returns raw emits an audit entry — **no content hash**
(low-entropy oracle). Two refinements from v2:

### 3.1 Outcome semantics — attempted vs returned (external review v2 F9)

Auditing *before* fetch is correct, but after the append succeeds the
fetch/decrypt/integrity-check/response can still fail — a bare
`raw_trace_accessed` would then claim access that did not occur. Use a
**`raw_trace_access_attempted` entry before retrieval + a completion
status** (`outcome = returned | failed`), or one immutable event carrying
`outcome`. And when release degrades to projected, the response/event says
so **explicitly**: `raw_included: false`, `redaction_reason:
access_audit_unavailable` (or `retrieval_failed`, …). Silent degradation
must not let an operator mistake redacted data for complete evidence.

### 3.2 Cardinality — frozen (external review v1 F6, v2 F9)

- **HTTP detail / explain:** one event per request per instance.
- **Multi-instance response:** **one event per returned instance** (chosen
  contract — not "or a bounded list"; the per-instance record is the
  forensic unit and bounds naturally to the page size).
- **WebSocket:** one event per raw-bearing **delivered event**; an
  access-audit failure projects *that* event and continues — never closes
  the connection.
- **Export / batch:** one job event + scoped item counts.

### 3.3 Ordering & fail-closed

Authorize scope + grant → determine objects → append attempt-audit →
fetch/merge → mark returned. Append failure withholds raw (degrades to
projected, §3.1). Read-time single append; independent of the write-path
durability contract (§4.2).

Caveats: audit-of-the-auditors deferred; deletion cascades audit (a granted
Administrator could erase their own trail — `THREAT_MODEL` §8 tamper-evident
gate is the eventual answer). Under B2, vault access logs live outside the
accessor's deletion authority.

## 4. Decision 3 — storage-level trace separation (the inversion)

### 4.1 Execution identity is frozen FIRST, then the vault key (external review v2 F6)

The vault key must match a frozen execution-identity model. Decision:
**one immutable step-attempt row per attempt** (a retry creates a new
attempt row, never mutates the prior). The vault then keys on the persisted
attempt identity, not a logical `step_id` string:

```
raw_traces
  id                     -- opaque object id (portable across vault backends, §0.2)
  org_id
  instance_id            (cascade per §5)
  step_attempt_id        nullable   -- FK to the immutable step-attempt row
  kind                   -- tool_calls | model_output | error | trigger_payload | recall | (future → vault-only)
  payload                -- JSONB (Contract A) / ciphertext+AEAD (B1/B2)
  projector_version                 -- §4.3
  created_at
```

- **Uniqueness:** `(step_attempt_id, kind)` unique for step rows; a
  **partial unique index** on `(instance_id, kind) WHERE step_attempt_id IS
  NULL` for instance-level rows (ordinary NULL semantics don't collide, so
  the partial index is required). **At most one row per kind per attempt.**
- **Attempt isolation (v2 F6):** attempt N never rehydrates attempt N−1's
  raw except through an explicitly defined prior-attempt context rule.
  Retry test pins this.
- **Multi-reference:** the safe record carries a `raw_refs` map (kind →
  opaque id), never one scalar ref (a step has up to three kinds).
- **Integrity validation:** a referenced row matches the same org, instance,
  and attempt as the operational row pointing at it.

### 4.2 Durability — committed or the step fails (external review v1 F2), portable protocol (v2 F2)

"Logs and never fails the run" is irreconcilable with rehydration. The
contract: **safe operational row + its required raw trace commit together,
or neither is complete.** Execution-critical raw = trigger payload + prior
step outputs (exactly what §4.3 rehydrates).

Same-DB (Contract A): a shared transaction. Separate-vault (B2, or the
portable abstraction §0.2): a **reserve → persist → commit** protocol —
(1) reserve a stable instance/step-attempt identity; (2) persist the raw
object **durably and idempotently** to the vault; (3) commit the operational
row referencing that opaque id; (4) mark the raw object committed;
(5) reconcile/delete orphaned raw objects. A failure before (3) marks the
step FAILED/PAUSED with a specific persistence error and **never reports it
durably COMPLETED**, so no unhonorable resume/fork claim exists. Diagnostic-
only material may be best-effort; execution-critical material may not.

### 4.3 Rehydration integrity (external review v1 F7), with an exact disagreement predicate (v2 F8)

Resume rebuilds `context.steps` from persisted `instance.context`; fork
from persisted `step.output`. These must rehydrate the **full** context
from the vault (projected-only context breaks pins, conditions, and
downstream prompts). Integrity rules:

- **Missing** raw → explicit failure (never silent projected execution).
- **Malformed** raw → explicit failure.
- org/instance/step/**attempt mismatch** → security error.
- **Disagreement predicate (exact):** `project(kind, raw_payload,
  projector_version) != stored_safe_projection` — NOT raw==safe (they are
  intentionally different). Requires a **versioned projector id**, canonical
  serialization, and raw+projection **schema versions**, so a projector
  code change doesn't make old rows look corrupt.
- **Contract B binding:** raw payloads use **authenticated encryption**
  binding ciphertext to `org_id, instance_id, step_attempt_id, kind,
  schema_version` — metadata comparison alone does not stop substitution by
  an actor able to edit vault rows.
- **Fork copies / immutably binds** the required raw into its own trace set,
  so deleting the source later cannot break the fork.
- **Retry** selects exactly the intended attempt; abandoned-attempt rows
  never rehydrate.

Tests cover missing, corrupt, mismatched, wrong-attempt, and deleted-source.

### 4.4 Read path

Ordinary read returns the operational row as-is (already safe). A
privileged, audited raw read fetches + merges the vault payload (§3
ordering). Read-time projection is belt-and-suspenders.

## 5. Lifecycle — deletion, retention, backups, resource limits

### 5.1 Retention is COUPLED to resume/fork (external review v1 F9, v2 F10)

Execution-critical traces **cannot expire while the instance is advertised
resumable or forkable.** The contract chooses one: (a) retain execution
traces ≥ the full supported resume/fork period; or (b) expiry marks affected
instances non-resumable/non-forkable; or (c) a retained immutable
**execution snapshot** for recovery is separate from shorter-lived
diagnostic raw. Chosen default: **(c)** — a minimal execution snapshot
(trigger + prior outputs) kept for the resume/fork window; diagnostic raw
(full tool I/O, model output, errors) on a shorter window.

**Deletion:** instance deletion, definition force-delete, user/org delete,
and **compliance erasure** all cascade to the vault — and erasure **follows
every forked descendant** that copied the raw (§4.3). Fork copy amplifies
sensitive data: copies count against retention and quota, and an
access-audit entry referring to deleted traces is retained as metadata
(the content is gone, the fact-of-access is not). Under B1/B2, **backups and
replicas** follow the same key/access policy or the boundary leaks through a
backup.

Default window, terminal-instance policy, and deletion behavior are
**design decisions fixed before TG3c**, not left to the build.

### 5.2 Resource limits (external review v1 F10)

Max bytes per kind; max raw bytes per step and per workflow;
truncation-or-rejection (explicit — **a truncated execution-critical trace
forces reject/fail per §4.2**, since it cannot support exact resume/fork);
compression; usage metering; retention for oversized/failed traces.

## 6. What this deliberately does NOT do (first build)

- Contract B2 (host-operator resistance) is its **own** threat-model +
  infra design (TG3d); not TG1–TG3c.
- Audit-of-raw-access read restriction — deferred.
- Per-step taint *tracking* — v1 vaults all free-form output/errors (§1.1)
  instead of tracking taint per field.

### 6a. Live gap already fixed (internal review F4)

The `/workflow-instances/{id}` detail endpoint returned raw
`trigger_payload` + `recall` to a same-org Viewer (`redact_tool_data`
no-ops on those keys). Closed in the commit that introduced this doc via an
interim Gmail-shaped `safe_trigger_payload` + recall withhold; the typed
default-deny registry (§1.2) supersedes it at TG3a.

## 7. Interaction with existing designs

- **THREAT_MODEL §5a** — narrowed only when the chosen boundary (B1 or B2)
  lands; amended in that commit, not before. TG1–TG3c make no untrust claim.
- **ROLES_PLAN §8** — grant orthogonal to the deferred Auditor role and
  per-action permissions.
- **EXECUTION_SEMANTICS §3** — §4.2 strengthens persistence for
  execution-critical raw (durable-or-fail); updated in the same commit; the
  execution-identity model (§4.1, one immutable row per attempt) is a change
  to that doc and lands with it.
- **AUTH_PLAN** — grants administered through admin-gated `/api/users`;
  deactivation/transfer revoke.

## 8. Test-pinned acceptance criteria (for the build, not now)

1. Below-grant recovers no raw tool payload / **any free-form model
   output** / **trigger payload** / **recall** / **error text** from any read
   surface, incl. `/workflow-instances/{id}` detail.
2. A grant-holder recovers raw; every read emits per-§3.2 cardinality with
   an outcome status and no content hash.
3. A failed access-audit degrades to projected with an explicit
   `raw_included:false` + reason; on WS it projects that one event without
   closing the connection.
4. **The operational tables contain the safe projection only** — asserted
   with hostile sentinels against `step_executions.output`,
   `workflow_instances.trigger_payload`, **`workflow_instances.context`**,
   `audit_log.detail`, `recall`, **step/instance error text**, **escalation
   detail**, and **cost metadata**. The zero-raw verifier derives its
   table/column inventory **mechanically from a maintained asset map**, not
   a hand list.
5. Cross-org raw by an Administrator requires a **platform-wide or
   target-org grant** (an org-A grant does NOT travel), and emits BOTH
   `org_bypass` and the raw-access audit.
6. Migration `0006` moves inline raw to the vault; pre-0006 fixture includes
   a raw trigger BODY, a recall episode, and a raw-echoing model output.
7. Resume AND fork of a raw-influenced step reproduce straight-through
   behavior; a pinned `apply` step's `message_id` resolves; conditions
   evaluate identically.
8. **Boundary matches the named contract (§0.1):** Contract A documents the
   operator as trusted; B1 proves DB dumps/backups carry no decryptable
   plaintext; B2 (if the gate requires it) proves the host runtime cannot be
   impersonated/inspected. The external-org gate names which it requires.
9. A required-raw write failure leaves the step FAILED/PAUSED with a
   persistence error — never durably COMPLETED-but-unrecoverable.
10. Unknown trigger types and unknown raw kinds use empty/default-deny; a new
    type without a projector keeps its content out of operational storage.
11. Grant lifecycle: grant/revoke/expiry audited; revoked on
    deactivation + org transfer; self-grant blocked; **duplicate active
    grant rejected**; **exactly one of org_id/platform_wide**; revocation +
    expiry effective on next request and next WS event.
12. Missing / malformed / cross-org / **wrong-attempt** / wrong-step raw
    references fail closed during resume and fork; **attempt N cannot read
    attempt N−1's raw** except via the explicit prior-attempt rule.
13. A fork remains resumable after the source instance is removed (fork binds
    its own copy); compliance erasure follows forked descendants.
14. Migration completion proves zero raw in **every** operational table
    (asset-map-driven, hostile-sentinel, structural — not key-name).
15. Raw size and per-run quotas enforced; truncation is explicit and never
    sufficient for exact resume/fork.
16. Every raw-capable surface mechanically inventoried — HTTP, exports,
    CLIs, logs, exception/error reporting, WebSocket, memory introspection,
    future debug endpoints.
17. **Projector versioning:** a projector-logic change does not make a
    pre-change raw row read as corrupt (disagreement predicate is
    version-aware, §4.3).

## 9. Cut plan — TG1/TG2 approvable, TG3 gated

| Cut | Contents | Contract | Status |
|---|---|---|---|
| **TG1** | scoped `raw_trace_grants` + DB constraints (§2) + grant/revoke/expiry audit + deactivation/transfer revocation + **target-org-coverage rule for Administrator bypass** + platform-wide-grant authorization + read-site `ADMIN_TIER`→grant replacement (incl. WS) + memory facts-mode under grant; interim §6a projection already shipped | A | **approvable after §2 amendments** |
| **TG2** | raw-access audit: frozen cardinality (§3.2) + attempted-vs-returned outcome (§3.1) + explicit projected-degradation flags + audit-before-release ordering, HTTP + WS fail-closed | A | **approvable after §3 amendments** |
| **TG3a** | typed default-deny registry (§1.2) + §1.1 vault-all-free-form/errors + **abstract vault repository w/ opaque IDs** (§0.2) + schema (§4.1); **DARK DUAL-WRITE** — vault the raw AND keep the current inline representation; operational store does NOT switch to safe-only yet | A | **deferred (preconditions below)** |
| **TG3b** | resume/fork rehydration + integrity + projector versioning (§4.3) + durable-or-fail write contract (§4.2); **only after this passes does the operational write switch to safe-only** | A | **deferred** |
| **TG3c** | legacy backfill + mixed-version cutover + zero-raw verifier (§8.14) + retention/deletion policy fixed (§5.1) | A | **deferred** |
| **TG3d** | the chosen Contract-B boundary (B1 envelope-encryption OR B2 external-vault/attested-execution) as its **own threat-model + infra design**; dual-control grant; `THREAT_MODEL` §5a amended | **B1/B2** | **deferred; own design doc** |

**Sequencing invariants (external review v2 F3):** TG3a must NOT flip the
operational store to safe-only before TG3b — an instance created after a
safe-only TG3a but before TG3b would run straight through yet resume/fork
incorrectly. TG3a is therefore a **dark dual-write** (raw to vault, inline
retained); the operational write switches to safe-only only after TG3b's
rehydration + durability pass. TG3a and TG3b may instead ship atomically.

**TG3a–c preconditions (external review v2):** the Contract-B mechanism is
chosen (§0.2); TG3a is dual-write or atomic with TG3b; the §1.1 sensitivity
model covers all raw-influenced outputs + errors; `workflow_instances.context`
and every duplicate location are in the zero-raw inventory; attempt identity
(§4.1) and projector-version integrity (§4.3) are resolved; retention is
coupled to resume/fork (§5.1). **No external organization is provisioned
until the gate's required contract (§0.1) — up to and including TG3d — is in
effect**, not merely TG1–TG3c. Estimate: TG1+TG2 ~2 days; TG3a–c ~5–7 days;
TG3d scoped separately with its own doc.
