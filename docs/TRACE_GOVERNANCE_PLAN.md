# Trace Governance — Design

Status: **proposed — architecture approved, revise before build.** Internal
design review folded (adopt-with-conditions, 7 conditions). External design
review folded (2026-08-01, "adopt the architecture; revise before build" —
2 blocking findings + 8 amendments, all folded below). Not built; build is
trigger-gated (§0) and gated further on the revisions the external review
required.

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

## 0. Why now, the trigger, and the two honest contracts

**Trigger to BUILD:** before the first external organization is
provisioned (same gate as `ROLES_PLAN` §8's multi-tenancy hardening and
`THREAT_MODEL` §5a's host-administrator disclosure). Until then the host
operator and Administrators are trusted (THREAT_MODEL §5a), so raw traces
in the operational store are an accepted single-operator risk.

### 0.1 The two contracts (external review blocking finding 1)

A `raw_traces` table **in the same database, under the same superuser, is
not a security boundary against the host/DB operator.** Anyone who can read
`step_executions` can read `raw_traces`; anyone with DB credentials
bypasses the application privilege and audit path entirely. Table
separation defends against *accidental application disclosure and
application-layer authz defects* — not against the operator. Claiming the
DB operator is "no longer trusted" while leaving plaintext in a sibling
table (and deferring encryption) is internally inconsistent. This design
therefore commits to BOTH contracts, staged, and is explicit about which
one each cut satisfies:

- **Contract A — application-level governance (TG1 … TG3c).** The host and
  DB operator remain **trusted** (unchanged from `THREAT_MODEL` §5a). Under
  Contract A the vault protects against: ordinary users and Org Admins;
  accidental new API/CLI/export surfaces; application-layer authorization
  defects; and operational queries against the ordinary tables. A
  same-database vault table is sufficient FOR CONTRACT A and no stronger
  claim is made.
- **Contract B — host/DB operator does NOT get raw access (TG3d).** This is
  the actual external-organization boundary. It requires one of: a separate
  vault service/database with **separate credentials**; envelope /
  per-organization encryption whose keys are unavailable to the general
  DB/host operator (KMS-held, engine-only decrypt identity); a narrowly
  privileged runtime identity for rehydration and raw reads; and vault
  access logs outside the accessor's deletion authority. A separate schema
  or table under the same superuser is NOT Contract B.

**Consequence, stated plainly:** TG1+TG2 (privilege + audit) and even
TG3a–c (same-DB vault + rehydration + backfill) deliver Contract A only.
**No external organization is provisioned until Contract B (TG3d) is in
effect.** The threat-model doc (`THREAT_MODEL` §5a) is amended in the same
commit that lands TG3d, not before — we do not claim operator-untrust until
the boundary that earns it exists.

**Why write the spec now, ahead of the build:** the F3 read-surface fix
took four review rounds because *redact-at-read is a treadmill* — every new
read surface is a new place to leak (tool_call entry → step
outputs/context → echoed `output_text` → the list endpoint). The
storage-separation design below **inverts** that: the operational path
stores *safe by default* and raw goes to a vault, which retires the
read-surface projection as the primary control and ends that finding class.

## 1. What "raw trace" means (the asset being governed)

The sensitive content, today stored inline across the operational tables:

- **Tool call input/result** — `StepExecution.output.tool_calls[].{input,
  result}` (mail recipients/subjects/bodies, file contents, browser text,
  external error detail).
- **Model free-text that may echo the above** — `output.output_text` of a
  tool-bearing agentic step (the round-3 finding).
- **Trigger payloads** — `WorkflowInstance.trigger_payload` (the raw
  inbound message for email; the **arbitrary body** of a webhook; etc.).
- **Recalled correspondent history** — the `recall` field on an agentic
  step's output (veracium edges/episodes derived from third-party mail).
- **The same, duplicated in audit** — `step_completed` / `tool_call`
  audit `detail`.

### 1.1 Per-asset projection, via a typed default-deny registry (external review finding 4)

`redact_tool_data` sanitizes only `tool_calls` + `output_text`-when-a-tool-
was-used; it is a **no-op** on trigger payloads and recall (the live F4 leak,
§6a). Projection is therefore per-asset — but "keep routing fields" is **not
generically implementable**, because a routing field for Gmail (message_id)
is meaningless for a webhook whose body may itself be credentials or
customer records. The build uses a **typed projector registry keyed by
trigger type / asset kind, default-deny**:

| Asset / trigger type | Safe projection |
|---|---|
| `email` trigger | declared email routing only: message_id, thread_id, from-address |
| `webhook` trigger | declared idempotency/routing key only (per config); body NEVER preserved |
| `schedule` trigger | schedule metadata only (fire time, cron id) |
| `manual` trigger | safe metadata or empty |
| **unknown trigger type** | **empty / default-deny** |
| tool_calls | `safe_tool_call` (input keys, result status, byte size) |
| output_text (tool-bearing step) | withheld |
| recall | withheld entirely |
| **unknown raw kind** | **vault-only; never enters operational storage** |

**Adding a new trigger type or raw kind REQUIRES adding an explicit safe
projection before its content may enter operational storage.** Unknown
fields are never preserved because they "look like identifiers." The
read-time projection shipped today (§6a) is the interim Gmail-shaped
version and is superseded by this registry at TG3a.

**Memory-introspection reconciliation:** `/api/memory/summary/{org}/{account}`
renders the same class of raw correspondent content under a *role* gate
(`_MEMORY_ADMIN_ROLES`). After this design, raw correspondent content has
ONE privilege — the grant — so the introspection mode that renders facts
also requires the grant; its counts/summary mode stays role-gated.

## 2. Decision 1 — the raw-trace privilege (a scoped GRANT record)

**Chosen: a capability grant, NOT a new role, and NOT a bare Boolean column
(external review finding 5).** The four-role model (`ROLES_PLAN`) stays
fixed. A user-column boolean loses scope and history the lifecycle needs
(which org? granted by whom, when, why? survives org transfer? revoked on
deactivation? self-granted?). Raw-trace access is a **grant record**:

```
raw_trace_grants
  id
  principal_id            -- the user
  scope                   -- org_id, or explicit platform_wide
  granted_by
  granted_at
  expires_at   nullable   -- break-glass elevation (may be null = standing)
  reason
  revoked_by   nullable
  revoked_at   nullable
```

Default: **no grant for anyone, including Administrators and Org Admins.**
Rationale unchanged — the reviewer required a privilege *distinct from
ordinary administration*, which "Administrator implies raw" (today's
`ADMIN_TIER` shortcut) is not. A grant record is distinct by construction,
composes with any role, is itself auditable, is scope-aware, and carries
break-glass expiry without a fifth role (which would collide with
`ROLES_PLAN` §8's deferred Auditor axis).

**Grant lifecycle — test-pinned in the first build:**
- Grant and revoke are **audited** (`raw_grant_granted` / `raw_grant_revoked`,
  actor + principal + scope + reason).
- **Revoked on user deactivation and on organization transfer** (an org-
  scoped grant does not survive a move; sessions revoked already, §AUTH).
- **No silent self-escalation.** A grant to oneself is blocked — it requires
  a *different* Administrator as grantor.
- **Revocation is effective on the next request AND the next WebSocket
  event** (checked per-request/per-event, not cached for the session).

**Residual, stated (external review finding 5, threat point):** with a
*single* Administrator, self-grant-prohibition is only procedural — two
Administrators are needed for real separation. The single-admin deployment
therefore does NOT meet the separation bar; **dual control (two distinct
Administrators, or external-organization approval) is a Contract-B / TG3d
precondition**, recorded here so onboarding cannot quietly rely on one
operator granting themselves raw access.

**Replace `ADMIN_TIER` at ALL FOUR consult sites** — the audit projection,
the instance endpoint, `explain`, AND `ws.py`'s **independent** `raw_reader`
check (`ws.py:130`, which the WS handler can satisfy from the already-loaded
user row). The role-based shortcut retires. After this lands NO ONE is
auto-granted — an intended operational break, not a regression.

**Scope:** the grant always composes with org scoping — a granted user
still only reaches their org's traces (cross-org 404). A granted
Administrator reaching another org is `org_bypass`-audited *and*
raw-access-audited (§3).

## 3. Decision 2 — audited raw access

**Every read that returns a raw trace emits a `raw_trace_accessed` audit
entry** (in addition to any `org_bypass`), capturing actor, the
instance/step reached, and the surface — **no content hash** (that would
re-create the low-entropy oracle `safe_tool_call` deliberately dropped).

### 3.1 Cardinality — defined precisely (external review finding 6)

"Per request per instance" and "exactly one entry" conflict for
multi-instance responses. The event unit is:

- **HTTP detail / explain:** one event per request per instance.
- **Multi-instance response** (an audit list spanning instances, a future
  multi-instance page): one event **per returned instance**, or one request
  event carrying a bounded instance list/count + the query scope.
- **WebSocket:** one event **per raw-bearing delivered event** — NOT one per
  connection. If the access-audit append fails for a given event, that event
  is **projected** (degraded) and delivery continues; the connection is
  never closed or corrupted by an audit failure.
- **Export / batch job:** one event for the job + scoped item counts.

### 3.2 Ordering — audit precedes release

1. Authorize scope + grant.
2. Determine which raw objects *would* be returned.
3. Successfully append the access audit.
4. Only then fetch/merge and release the raw payload.

Fetching the payload before the audit succeeds weakens fail-closed.
**Fail-closed:** if the append fails, raw content is withheld (the read
degrades to projected). This is a read-time single append, independent of
the write-path durability contract (§4.2).

**Where it lives / caveats:** the ordinary audit log (access-metadata, not
content). Audit-of-the-auditors (a distinct "who watched the watchers"
privilege) is deferred; and because deletion cascades audit, a granted
Administrator could erase their own `raw_trace_accessed` trail — the
tamper-evident-audit gate (`THREAT_MODEL` §8) is the eventual answer, noted
not solved. Under Contract B, vault access logs must live outside the
accessor's deletion authority (§0.1).

## 4. Decision 3 — storage-level trace separation (the inversion)

Raw payloads move to a `raw_traces` vault; the operational tables store only
the safe projection (§1.1) plus a reference.

### 4.1 Schema — tied to persisted execution identity (external review finding 3)

```
raw_traces
  id
  org_id
  instance_id            FK -> workflow_instances (cascade per §5)
  step_execution_id      FK -> step_executions, nullable   -- NOT a logical step_id string
  attempt                                                   -- from the persisted step-execution attempt
  kind                   -- tool_calls | output_text | trigger_payload | recall (+ future, default vault-only)
  payload                JSONB
  created_at
```

- **Uniqueness:** ordinary Postgres NULL semantics do NOT collide, so a
  nullable-`step_execution_id` unique key would let duplicate trigger rows
  through. Use a **partial unique index for instance-level rows**
  (`step_execution_id IS NULL` → unique on `(instance_id, kind)`) and a
  separate unique on `(step_execution_id, kind)` for step rows (or
  `NULLS NOT DISTINCT` where available, or split instance-trace and
  step-trace tables). **At most one row per kind per persisted step
  attempt.**
- **Multi-reference:** a step has up to three raw kinds (tool_calls,
  output_text, recall); a single `raw_ref` cannot represent three
  independent rows. The safe record carries a **`raw_refs` map (kind → id)**
  or a deterministic lookup by `(step_execution_id, kind)` — not one scalar
  ref.
- **`attempt` provenance:** `attempt` comes from the persisted step
  execution; the build must confirm it is durably recorded *before* a raw
  row references it. Raw rows from an **abandoned attempt never rehydrate
  into a later attempt** (§4.3).
- **Integrity validation:** a referenced row must match the same org,
  instance, and attempt as the operational row that points at it.
- `org_id` is carried directly for cheap per-tenant retention/deletion.

### 4.2 Durability — required raw is committed or the step fails (external review blocking finding 2)

The first draft said a raw-write failure "logs and never fails the run."
That is irreconcilable with rehydration: a step could report durably
COMPLETED while its raw context is lost, leaving resume/fork unable to
reconstruct the original context (silent redacted execution, or a later
unexplained failure, or an instance shown resumable that is not).

**Execution-critical raw material — trigger payload and prior step outputs
(exactly the data §4.3 rehydrates) — must be durable before the step or
instance is considered committed.** The contract:

> safe operational row + its required raw trace commit together, or neither
> is considered complete.

Implemented as a shared transaction where possible. If a shared transaction
is not yet available, a required-raw write failure MUST: mark the step (or
instance) FAILED/PAUSED, record a specific persistence error, **never report
the step as durably completed**, and prevent any resume/fork claim that
cannot be honored. Optional *diagnostic-only* material may be
best-effort — but trigger data and prior step outputs are execution-critical
under this design's own rehydration argument and are NOT best-effort.

### 4.3 Rehydration integrity (external review finding 7)

Within a run, `context.steps`/`context.trigger` hold full output in memory.
Resume rebuilds `context.steps` from persisted `instance.context`; fork from
persisted `step.output` (`executor.py:222-226, 266-316`). If those are the
projected-safe copies, a resumed/forked step gets redacted prior-steps and
conditional-edge inputs, and a `pin_params` routing field resolved from
`trigger.*` (fail-closed, `executor.py:1094-1112`) would kill a resumed
`apply` step. **Therefore resume/fork MUST rehydrate the full context from
`raw_traces`.** "Fetch and merge" is not enough — the integrity rules:

- **Missing** raw trace → explicit failure, never silent projected execution.
- **Malformed** raw trace → explicit failure.
- Org / instance / step / **attempt mismatch** → security error.
- Safe and raw values **disagree** → raw-integrity failure (not arbitrary
  precedence).
- **Fork copies or immutably binds** the required raw context into its own
  trace set, so deleting the source instance later does not break the fork.
- **Retry** selects exactly the intended prior attempt; abandoned-attempt
  rows never rehydrate.

Tests cover missing, corrupt, mismatched, and deleted-source traces — not
only the happy-path equivalence criterion.

### 4.4 Read path

An ordinary read returns the operational row as-is (already safe). A
privileged, audited raw read fetches + merges the vault payload (§3
ordering). Read-time projection becomes belt-and-suspenders, not the primary
control.

## 5. Lifecycle — deletion, retention, backups, resource limits

### 5.1 Deletion & retention (external review finding 9)

The vault is part of the asset lifecycle. The build defines behavior for:
instance deletion, definition force-deletion, user/org deletion, compliance
erasure, retention expiry, a fork depending on source traces (§4.3 binds a
copy), and an access-audit entry referring to deleted traces. A **default
retention window and deletion contract are required** (a *shorter* window is
a deferrable product choice; *having* one is not). Under Contract B, backups
and read replicas carry the vault too and MUST follow the same key/access
policy — otherwise the boundary leaks through a backup.

### 5.2 Resource limits (external review finding 10)

Raw file reads, browser output, mail bodies, recall blocks, and model
responses are tenant-controlled and can be large; unbounded JSONB is a
storage/memory-pressure path. The build defines: max bytes per trace kind;
max raw bytes per step and per workflow; truncation-or-rejection behavior
(explicitly represented — **a truncated trace is NOT sufficient for exact
resume/fork**, so truncation of execution-critical material forces
reject/fail per §4.2); compression policy; usage metering; and retention for
oversized/failed traces.

## 6. What this deliberately does NOT do (first build)

- Column-level encryption / separate credentials are **Contract B / TG3d**,
  not TG1–TG3c — but they are the actual external-org gate, not an optional
  extra (§0.1).
- Audit-of-raw-access read restriction — `raw_trace_accessed` entries are
  Administrator-readable; a "who watched the watchers" privilege is deferred.
- Changing the `tool_calls` projection algorithm — `safe_tool_call` is
  unchanged; it moves from read-primary to write-primary + read-backstop.
  The *new* projections are the typed trigger/recall ones (§1.1).

### 6a. Live gap already fixed (internal review F4)

The `/workflow-instances/{id}` **detail** endpoint returned the raw
`trigger_payload` and `recall` to a same-org Viewer, because
`redact_tool_data` no-ops on those keys. Closed in the commit that
introduced this doc: an interim (Gmail-shaped) `safe_trigger_payload` +
recall withhold, applied at the read surfaces via the existing recursion.
The typed default-deny registry (§1.1) supersedes the interim projector at
TG3a. Criterion 1 names the detail endpoint explicitly.

## 7. Interaction with existing designs

- **THREAT_MODEL §5a** — narrowing "Administrators/host are trusted with raw
  content" happens **only at TG3d** (Contract B); the doc is amended in that
  commit, not before. TG1–TG3c make NO operator-untrust claim.
- **ROLES_PLAN §8** — the grant is orthogonal to the deferred Auditor role
  and per-action permissions; consumes neither trigger.
- **EXECUTION_SEMANTICS §3** — §4.2 STRENGTHENS the persistence contract for
  execution-critical raw material (durable-or-fail), and updates that doc in
  the same commit; the read-time access audit (§3) is a separate fail-closed
  append.
- **AUTH_PLAN** — grants are administered through the admin-gated `/api/users`
  surface (new grant records + guards); deactivation/transfer revoke.

## 8. Test-pinned acceptance criteria (for the build, not now)

1. Below-grant (any role, incl. Administrator/Org Admin) recovers no raw
   tool payload / echoed `output_text` / **trigger payload** / **recall**
   from any read surface — explicitly including `/workflow-instances/{id}`
   detail, not only list/audit.
2. A grant-holder recovers raw content; every such read emits exactly one
   `raw_trace_accessed` per the §3.1 cardinality, with no content hash.
3. A failed `raw_trace_accessed` append degrades the read to projected
   (fail-closed); on WS it projects that one event without closing the
   connection.
4. The operational tables (`step_executions.output`,
   `workflow_instances.trigger_payload`, `audit_log.detail`, `recall`)
   contain the safe projection only — asserted against the repo after a run
   with hostile sentinels.
5. Cross-org raw read by a granted Administrator emits BOTH `org_bypass` and
   `raw_trace_accessed`.
6. Migration `0006` moves inline raw into the vault and leaves operational
   rows safe; the pre-0006 fixture includes a raw trigger BODY and a recall
   episode (not only tool_calls).
7. Resume AND fork of a tool-bearing step reproduce straight-through
   behavior: rehydrated context carries full prior output; a pinned `apply`
   step's `message_id` resolves; conditional edges evaluate identically.
8. **Infrastructure boundary matches the contract (§0.1):** either the DB
   operator is explicitly documented as trusted (Contract A), or direct DB
   access cannot reveal plaintext raw (Contract B). The external-org gate
   requires the latter.
9. **A required-raw write failure cannot leave a step durably COMPLETED but
   unrecoverable** (§4.2) — the step is FAILED/PAUSED with a persistence
   error instead.
10. **Unknown trigger types and unknown raw kinds use empty/default-deny**
    projection (§1.1); adding a type without a projector keeps its content
    out of operational storage.
11. **Grant lifecycle test-pinned:** grant/revoke audited; revoked on
    deactivation + org transfer; self-grant blocked; revocation effective on
    next request and next WS event.
12. **Missing / malformed / cross-org / wrong-attempt / wrong-step raw
    references fail closed during resume and fork** (§4.3).
13. **A fork remains resumable after the source instance is removed** per the
    §5.1 deletion contract (fork binds its own copy).
14. **Migration completion proves zero raw content in every operational
    table** — a hostile-sentinel, structural (not key-name) zero-raw verifier.
15. **Raw trace size and per-run quotas are enforced** (§5.2); truncation is
    explicit and never counts as sufficient for exact resume/fork.
16. **Every raw-capable surface is mechanically inventoried** — HTTP,
    exports, CLIs, logs, exception/error reporting, WebSocket, memory
    introspection, and future debug endpoints.

## 9. Cut plan (when triggered) — TG3 decomposed (external review)

| Cut | Contents | Contract |
|---|---|---|
| **TG1** | scoped `raw_trace_grants` records + grant/revoke audit + immediate (deactivation/transfer) revocation + all read-site `ADMIN_TIER`→grant replacement (incl. WS); fold memory facts-mode under the grant; interim trigger/recall read projection (§6a already shipped) | A |
| **TG2** | `raw_trace_accessed` audit semantics (§3.1 cardinality) + HTTP and WebSocket fail-closed behavior + audit-before-release ordering | A |
| **TG3a** | typed default-deny projector registry (§1.1) + raw-vault repository + schema (§4.1); **new writes only** | A |
| **TG3b** | engine resume/fork rehydration + integrity checks + durable-or-fail write contract (§4.2/§4.3) | A |
| **TG3c** | legacy backfill + mixed-version cutover protocol + zero-raw verifier (§8.14) | A |
| **TG3d** | **external-org boundary:** separate vault/credentials OR envelope encryption with operator-inaccessible keys + narrow runtime identity + out-of-reach access logs + dual-control grant; THREAT_MODEL §5a amended | **B** |

**TG3c is not the finish line.** TG1–TG3c deliver Contract A. **No external
organization is provisioned until TG3d (Contract B) is in effect** — that is
the cut that makes the vault an actual boundary rather than an application-
level convenience. A "migration + backfill + verifier + supported-version
deployment" checklist gates *TG3c*; the *external-org* gate additionally
requires TG3d. Estimate revised upward from the original ~2–3 days: TG1–TG3c
~4–5 days; TG3d depends on the chosen boundary mechanism and is scoped
separately when triggered.
