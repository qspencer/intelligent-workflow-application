# Trace Governance — Design

Status: **architecture APPROVED; five contracts frozen below; no cut yet
authorized.** Internal review folded (7 conditions). External reviews v1
("adopt architecture, revise before build"), v2 ("architecture accepted;
TG3 blocked"), and v3 ("architecture approved; freeze five contracts before
any cut is authorized") all folded. Not built; build is trigger-gated (§0).
TG1 authorizes after the grant-authorization freeze (§2.1); TG2 after the
audit-event freeze (§3.1); TG3 after the structured-output (§1.4), execution-
snapshot (§5.1), and vault-state-machine (§4.2) freezes.

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

The external-org gate **names** which it requires (B1-with-residual or B2 —
a business/threat decision, not silently made here). `THREAT_MODEL` §5a is
amended only in the commit that lands the chosen boundary.

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

## 2. Decision 1 — the raw-trace privilege (scoped GRANT record) (approved)

```
raw_trace_grants
  id
  principal_id
  scope                   -- exactly one of: org_id | platform_wide
  granted_by
  granted_at
  expires_at   nullable
  reason
  revoked_by   nullable
  revoked_at   nullable
```

Default: no grant for anyone. DB constraints: exactly one of
`org_id`/`platform_wide`; no duplicate active grant per principal+scope
(partial unique where `revoked_at IS NULL`); expired grants inactive;
expiry + revocation enforced next HTTP request AND next WS event; expiry
visible in grant history (audited). Lifecycle: grant/revoke/expiry audited;
revoked on deactivation + org transfer; no silent self-escalation.

**Administrator bypass is scope-constrained:** raw allowed only when base
resource authz succeeds AND an active grant covers the **target** org. An
org-A grant does NOT travel to org B; cross-org raw needs a platform-wide or
target-org grant.

Replace `ADMIN_TIER` at all four consult sites (audit projection, instance,
`explain`, `ws.py:130`'s independent `raw_reader`).

### 2.1 CONTRACT 4 — platform-wide grant authorization (external review v3 F4) — FROZEN

```
Org-scoped grant:
  issued by a DIFFERENT Administrator than the recipient
  audited (grantor, recipient, org, reason)

Platform-wide grant:
  requires TWO distinct Administrators (issuer + approver)
  the recipient may NOT be issuer or approver
  mandatory expiry + reason (no standing platform-wide grants)
  UNAVAILABLE in a single-Administrator deployment
  each step (issue, approve) separately audited

External-organization approval (alternative to the 2nd Administrator, when
the gate's contract requires the tenant to authorize raw access):
  the approving identity + evidence are MACHINE-RECORDED (a stored approval
  artifact referenced by the grant), never procedural prose.
```

This closes the "who may issue one is named in TG1" placeholder. A single-
Administrator deployment therefore cannot mint a platform-wide (cross-org)
raw grant at all — consistent with the §0.1 dual-control precondition.

## 3. Decision 2 — audited raw access

No content hash (low-entropy oracle). Ordering: authorize scope+grant →
determine objects → append the ATTEMPT event → fetch/merge → append the
COMPLETION event. Append failure of the attempt event withholds raw.

### 3.1 CONTRACT 5 — append-only two-event audit model (external review v3 F5) — FROZEN

One model, append-only (no event mutation):

```
raw_trace_access_attempted        -- committed BEFORE vault retrieval
  request_id                      -- shared correlation id
  actor
  instance
  surface                         -- detail | explain | audit | ws | export
  intended_kinds

raw_trace_access_completed        -- appended AFTER retrieval resolves
  request_id                      -- same correlation id
  outcome ∈ { returned, projected, partial,
              retrieval_failed, integrity_failed, response_cancelled }
  returned_kinds
```

- **Cardinality:** HTTP detail/explain — one attempt+completion pair per
  request per instance. Multi-instance response — one pair **per returned
  instance**. WebSocket — one pair per raw-bearing **delivered event**, each
  with its own correlation id; an audit failure projects that one event and
  never closes the connection. Export/batch — one job pair + scoped counts.
- **Partial retrieval:** choose **explicit partial** — the response carries
  `raw_included: partial` + a machine-readable list of returned vs withheld
  kinds; `outcome=partial`. Silent merge-some/withhold-others is forbidden.
  (All-or-nothing is the acceptable stricter alternative; the build picks
  explicit-partial to preserve forensic usefulness.)
- **Degradation is explicit** in the response/event: `raw_included:false`,
  `redaction_reason: access_audit_unavailable | retrieval_failed |
  integrity_failed`.
- The completion outcome distinguishes: raw fetched but **delivery failed**
  (`response_cancelled` with returned_kinds), raw **returned to app code but
  client disconnected**, and **audit-unavailable → no fetch occurred**
  (`projected`, no attempt-success).

Caveats: audit-of-the-auditors deferred; under B2, vault access logs live
outside the accessor's deletion authority.

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
  idempotency_key        -- deterministic: hash(instance_id, attempt, kind)
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
STORED/COMMITTED collapse into it. Separate-vault (B1/B2): the five-step
protocol, with these **recovery rules** (the part v3 required):

- **Idempotency key is deterministic** `hash(instance_id, attempt, kind)`, so
  a retry after an uncertain write re-addresses the *same* object — never a
  duplicate.
- **The reconciler checks operational references before deleting anything.**
  A **REFERENCED object is recoverably promoted to COMMITTED after a crash,
  never classified as an orphan** merely because the final marker wasn't
  written. Only RESERVED/STORED objects with **no** operational reference are
  ABORTED, and only after a safety window.
- **Crash after REFERENCED, before COMMITTED:** recovery promotes to
  COMMITTED (the operational row proves the reference).
- **Vault says STORED but the operational write outcome is unknown:**
  treated as STORED-unreferenced; the operational write is retried
  idempotently; the object is ABORTED only if the operational row provably
  does not (and will not) reference it.
- **One attempt cannot claim another attempt's object** — the idempotency
  key includes `attempt`, and rehydration validates
  `(org, instance, attempt, kind)` match (§4.3).

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

A separate **diagnostic copy** (full tool I/O, model output, errors NOT in
the snapshot) may have a shorter window — but **deleting the diagnostic copy
must not delete the recovery representation**. When an instance's execution
snapshot expires, the instance becomes **explicitly non-resumable /
non-forkable** (surfaced, not a silent failure).

Tests: expire diagnostic traces, resume + fork, prove identical behavior;
and prove an instance is explicitly non-resumable once its snapshot expires
(criteria 19, plus 7).

### 5.2 Fork lineage is a persisted, queryable structure (external review v3 F8)

Compliance erasure "follows every forked descendant" is enforceable only
with persisted lineage — never by scanning opaque raw payloads. Define:

- `source_instance_id` (immutable lineage edge) on every forked instance;
  transitive descendants indexed for erasure traversal.
- Cross-org fork behavior (whether a fork may cross orgs, and how erasure
  authority follows).
- Copied raw objects retain a **source-subject reference** so erasure of the
  subject reaches the copies.
- Erasure **proves** every descendant was covered (a traversal that returns
  the covered set).
- A descendant that added independent evidence after the fork: its own
  additions follow its own subject, not the source's.

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
2. Grant-holder recovers raw; each read emits the §3.1 pair with an outcome
   and no content hash.
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
21. **(Contract 4)** Platform-wide grants follow the frozen approval rule
    (two distinct Administrators, recipient excluded, mandatory expiry,
    unavailable single-admin); org-scoped grants require a different grantor.
22. **(Contract 5)** Audit distinguishes attempted / returned / projected /
    partial / retrieval_failed / integrity_failed / response_cancelled under
    one shared correlation id; partial access is explicit, never a silent merge.
23. **(Correction 6)** The stored safe projection and the raw record both
    identify the exact projector + raw + projection schema versions used.
24. **(Correction 8)** Compliance erasure traverses a persisted multi-
    generation fork-lineage fixture and returns the covered descendant set.

## 9. Cut plan — architecture approved; freezes gate authorization

| Cut | Contents | Contract | Authorization |
|---|---|---|---|
| **TG1** | scoped grants + DB constraints (§2) + **platform-wide grant authorization (§2.1)** + target-org bypass rule + read-site `ADMIN_TIER`→grant (incl. WS) + memory facts-mode under grant | A | **authorizable now (freeze 4 done)** |
| **TG2** | raw-access audit: **frozen two-event model (§3.1)** + cardinality + explicit partial/degradation + audit-before-release, HTTP + WS fail-closed | A | **authorizable now (freeze 5 done)** |
| **TG3a** | typed registry (§1.2) + **safe-output contract (§1.4)** governing model output/errors + vault-all-free-form + abstract vault repo/opaque IDs (§0.2) + schema (§4.1); **DARK DUAL-WRITE** (vault raw, keep inline) | A | after freeze 1 + 3 + the EXECUTION_SEMANTICS attempt model |
| **TG3b** | rehydration + integrity + projector versioning (§4.3) + durable-or-fail state machine (§4.2); only then flip operational to safe-only | A | after freeze 2 + 3 |
| **TG3c** | backfill + mixed-version cutover + zero-raw verifier + retention/deletion + fork lineage (§5.1/§5.2) | A | after freeze 2 |
| **TG3d** | the chosen B1/B2 boundary as its own threat-model + infra design; dual-control grant; THREAT_MODEL §5a amended | B1/B2 | own design doc |

**Sequencing invariant:** TG3a must not flip the operational store to
safe-only before TG3b (else an instance born between them resumes/forks
wrong) — hence the dark dual-write, or TG3a+TG3b ship atomically.

**No external organization is provisioned until the gate's required contract
(§0.1) — up to and including TG3d — is in effect**, not merely TG1–TG3c.
Estimate: TG1+TG2 ~2 days; TG3a–c ~5–7 days; TG3d scoped separately.
