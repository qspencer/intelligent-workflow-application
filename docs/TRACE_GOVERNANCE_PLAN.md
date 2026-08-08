# Trace Governance — Design

> ## ⚠️ EXTERNAL CODE RE-REVIEW FAILED AGAIN (2026-08-03) — structural rework required
>
> The `2cfacfc` re-review passed the F1–F10 regression suite but reproduced
> **new, deeper bypasses in the same areas** — Contract A and B1 are still NOT
> delivered. The reviewer's central, correct point: the first-round fixes patched
> *surfaces and field-names*, not the boundaries the design promises. **Do not
> re-claim "zero-raw at rest" / "DB-operator-resistant" and do not onboard an
> external tenant.**
>
> **Progress:** contained bugs fixed (re-review F3 memory-audit-discarded, F4
> sealed-without-key fail-open, F1 forged `_redacted` marker, F6 immediate-path
> mode + `ticket_ref` validation). **P1 is BUILT** (`f63f48b`) — the projector's
> key-allowlist is now a field→validator registry (§1.4 CONTRACT 1), so
> per-workflow business vocabularies over-redact. **§1.4a is NOT build-authorized:**
> **three** external design reviews have run (2026-08-03 internal, 08-04, 08-05).
> Each approved the direction and deferred authorization; each found real gaps —
> the declaration-only codebook bypass, a wrong channel-capacity claim, and then
> authorization *identity/lifecycle* (a revoked approval could be resurrected by
> re-approving identical declaration bytes). All are folded: the approval is now a
> first-class object whose `approval_id` + generation binds every produced row,
> with an atomic CAS lifecycle, hard revocation, destination-scoped projection and
> concretely frozen hashing. **It awaits external re-approval, and P1's
> over-redaction STANDS until then.**
> **P2 is BUILT** (explain / escalations / dry-run / run-batch now route through
> the grant + release audit + projector). **P4 is BUILT** (grant + vault CAS).
> **P3a is BUILT** (persisted projection stamp + the §4.3 agreement predicate).
> All four primitives are now built; P3b (§5.1 manifest) stays deferred, and
> **an external code re-review is required before re-claiming either contract.**
>
> The core findings need **four shared primitives, not more per-surface patches**
> — and a design review established these are *build-conformance to the v6-frozen
> contracts the build diverged from*, not a v7 design round (see
> `docs/NEXT_STEPS.md` G-Trace-Review-2): (1) a TYPED projector registry that
> validates values, never trusts markers; (2) a total raw-surface inventory
> applied to HTTP/WS/audit/dry-run/escalation/memory; (3) a rehydration validator
> driven by persisted dependencies + exact re-projection agreement (not marker
> scans); (4) DB compare-and-set for grant + vault lifecycle. This is a design +
> build effort, not a quick fold.

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
non-behavioral for readers. `test_raw_trace_vault.py`.

**Build status: TG3b PART 1 BUILT (2026-08-01) — the read-back foundation.**
`trace_rehydrate.py::RawTraceRehydrator` reconstructs a full step output /
trigger payload from the vault (§4.3), with the system-access audit (§3.2):
every engine-side vault read commits `raw_trace_system_access_attempted`
BEFORE any fetch and `_completed` after, under the narrow "engine" workload
identity, FAIL-CLOSED (an unrecordable access takes no fetch; a
projected-but-missing kind raises `retrieval_failed`). Validated against the
still-present inline copy (`test_raw_trace_rehydrate.py`) — proving the vault
is a faithful read-back source, which is the flip's precondition. Additive;
nothing flips yet.

**TG3b PART 2 BUILT (2026-08-01) — the safe-only flip (flag-gated).** With
`trace_safe_only` ON, the operational store persists ONLY the safe projection
(instance / step.output / context / trigger / step_completed audit are
zero-raw at rest) and the raw lives in the vault; run() vaults the raw trigger
DURABLY, `_run_step_once` vaults the raw output before persisting the
projection (durable-or-fail), `_mark_instance` persists the projected context,
and resume + fork rehydrate the trigger + step outputs from the vault
(system-audited, fail-closed, keyed on the latest COMPLETED step-attempt; fork
re-binds its own copy, §4.3). Default OFF = dark dual-write (full suite
untouched); the default flips at the gate. Projection extracted to a domain
`trace_projection` module first (engine must not import `api`).
`test_trace_safe_only_flip.py`.

**TG3b PART 3 BUILT (2026-08-01) — read-surface raw-merge.** Under the flip,
the instance-detail read re-merges a grant-holder's raw from the vault
(`RawTraceRehydrator.merge_output`/`merge_trigger`, no extra system-audit —
the TG2 release path already audited); a no-op under the default.
`test_trace_safe_only_read_merge.py`.

**TG3c BUILT (2026-08-02) — backfill + zero-raw verifier.**
`trace_migration.py`: the verifier (`find_raw_in_operational_store`) is
STRUCTURAL (a record has raw iff projecting it changes it — the projection is
now idempotent) and asset-map-driven; the backfill vaults inline raw durably +
projects the rows in place, idempotent. `tools/trace_migration.py` CLI
(`verify` = the gate, exit 1 on any raw; `backfill`). `test_trace_migration.py`.

**TG3d-1 BUILT (2026-08-02) — Contract B1 (per-org envelope encryption).**
`trace_cipher.py`: vault payloads are sealed with AES-256-GCM under a per-org
HKDF-derived data key, AEAD-bound to (org, instance, attempt, kind, schema) so
a substituted/edited/wrong-org row fails to open; activated by
`WORKFLOW_PLATFORM_TRACE_MASTER_KEY` (else plaintext Contract-A vault). A DB
dump holds ciphertext only; keys live outside the DB role. `RawTraceVault`
seals on write, `RawTraceRehydrator` decrypts on read — no caller changes.
`test_trace_cipher.py`.

**Gate-wiring BUILT (2026-08-02):** per-call `tool_call` audit-at-rest
projection under the flip (explain rebuilds a grant-holder's per-call raw
from the vaulted step output); **dual-control enforcement**
(`WORKFLOW_PLATFORM_RAW_GRANT_DUAL_CONTROL` — no raw grant activates on a
single Administrator, even org-scoped); and the **`THREAT_MODEL` §5a
amendment** narrowing operator trust (Administrator ≠ raw reader; DB/backup
operator ≠ raw reader under B1; honest infra-operator-trusted residual).

**Validated end-to-end on the live backend (2026-08-02):** a two-external-org
dry-run — isolation + switching, full flip (zero-raw operational store),
per-org encrypted vault, grant-gated + AEAD-bound read-back, and a 1,340-
instance historical backfill (execution-state tables now zero-raw; only
append-only pre-flip `tool_call` audit remains, read-protected).

**Secret-manager gate DONE (2026-08-02):** the vault master key is sourced
from AWS Secrets Manager at startup (`WORKFLOW_PLATFORM_TRACE_MASTER_KEY_SECRET`,
`main.py::_resolve_trace_master_key`, installed off-disk), decoupled from the
global secret backend. An external CODE review is queued
(`docs/TRACE_CODE_REVIEW_GUIDE.md`).

**Remaining (deferred, waiting on an actual external tenant):** (1) **TG3d-2
(Contract B2 — host-operator resistance)** — attested/enclaved runtime or
external key release; its own infrastructure design, NOT a code module; the
biggest security gap. (2) The **finalized dependency manifest (§5.1,
retention/erasure)**. (3) The other §5a release gates (backup encryption,
credential rotation, G22 inventory, intra-org boundary, resource limits) — the
trace + secret-manager boundaries are two of them, now satisfied; these are not.

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
| 1 | What qualifies as safe structured output | §1.4 + §1.4a (per-workflow declaration) | TG3a |
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
role-gated.### 1.4a Per-workflow DECLASSIFICATION APPROVAL (fills a §1.4 silence)

*Status: written 2026-08-03; internal review adopt-with-conditions (folded);
external design review 2026-08-04 (folded); **external design RE-review
2026-08-05 — revised direction APPROVED, deferred for the authorization-lifecycle
amendment now folded below.** **Nothing here is built.** P1's over-redaction of
per-workflow vocabularies STANDS until §1.4a is build-authorized.*

§1.4 froze *what* may persist un-vaulted (closed enums, bounded numbers, bools,
opaque ids) but never said **who declares a business field as one**, so the build
hardcoded `category` / `attention` / `relevance_bucket` platform-global —
vocabularies that are per-workflow and which the platform cannot validate. This
section fills that silence. It is an **amendment**, not a reopen of the FROZEN
§1.4.

**The reframe.** This is **not a schema**. It is a **first-class, atomically
managed AUTHORIZATION object** that declassifies raw-derived information below
the raw-trace boundary. The YAML key is therefore **`declassify:`** and every
operator-facing surface says *declassification approval* — never "safe output",
so nobody mistakes finite validation for confidentiality.

#### 1.4a.1 Why declaration-only approval is unsafe — the codebook attack

Approving only the declaration does not protect the boundary, because the
*prompt* decides the emitted value:

1. Approvers authorize 8 scalar enum fields, 16 literals each.
2. An **Organization User** later edits **only the step prompt**.
3. The prompt instructs the model to encode raw input into those 8 selections.
4. Every declaration validator passes.
5. That Org User reads the operational output and decodes the raw content.

No declaration edit occurs ⇒ no approver review, no audit, no raw grant, no
release audit. The approved alphabet has become an exfiltration codebook,
contradicting the rule that **administration alone does not confer raw access**
(§2).

#### 1.4a.2 The approval object — identity, not just content

Declaration *content* is reusable; **authorization is not**. An approval is a
first-class row with its own stable identity and generation:

```
SafeOutputApproval = approval_id            # stable, unique — the authorization
                     generation             # monotonic per (org, workflow, step)
                     org_id, workflow_id, step_id
                     declaration_hash       # content-addressed declaration
                     step_semantic_hash     # the executable step revision
                     destinations           # §1.4a.7 — scoped, in the identity
                     capacity_bits          # computed at approval (§1.4a.5)
                     approval_mode, approvers, tenant_artifact_ref
                     approved_at, expires_at, status
```

`expires_at` is required whenever the tenant artifact carries a validity period,
and MUST satisfy `approval.expires_at <= artifact.valid_until` (round 4, HIGH:
otherwise a month-long artifact activated on its last day mints an indefinite
authorization).

**Why identity, not content, must follow the row** (re-review finding 1):
approval A1 authorizes declaration D for step revision H; rows are produced; A1
is **revoked**; the identical D/H is later re-approved as A2. A row that recorded
only D and H would find an *active* approval and be released — resurrecting a
withdrawn authorization. Therefore `approval_id` + `generation` (alongside
`declaration_hash` + `step_semantic_hash`) bind to:

- the **step attempt**, before model invocation;
- the **operational safe row**;
- the **vault object** (or its authenticated metadata);
- **every destination-specific copy** (§1.4a.7);
- **release and revocation audit** records.

Revoking one approval never revokes another that happens to share declaration
bytes, and re-approving identical content never reactivates rows produced under
an earlier approval — that would be a separate, explicitly audited operation.

`step_semantic_hash` covers every input capable of changing the emitted value —
prompt + system templates, input/context mappings, model/provider + immutable
settings, tool schemas **and tool implementation identity**, structured-output
parser/schema, result mappings/transformations, deterministic function identity,
and the projector + declaration versions. It is computed as a **dependency
closure over the immutable executable step revision and all transitively
referenced artifacts** — never a hand-maintained field list. "Function version"
is a platform-controlled content/release identity, not an author-supplied label.

**The closure extends UPSTREAM (round 4, HIGH).** Binding only the declassifying
step's own revision moves the codebook attack one edge back, and the shipped
engine makes that reachable today:

- the real email-triage workflow's deterministic `record` step reads
  `steps.triage.output_text` — its emitted business vocabulary is produced by an
  upstream *agentic* step;
- and `_build_user_message` defaults, when a step declares no `inputs:`, to
  `{"trigger": …, "prior_steps": context.steps}` — i.e. **every** prior output.

So an ordinary `ORG_WRITE_ROLES` user can edit only the UPSTREAM step's prompt,
leave the declassifying step byte-identical, and change which approved enum value
it emits. Therefore:

```
step_semantic_hash(B) = executable revision of B
                      + semantic revision of EVERY upstream producer whose
                        output can reach B's declassifying computation
                      + those producers' transitive executable artifacts
```

**The boundary is SEMANTIC INFLUENCE, not DAG ancestry (round 5, HIGH).** A
DAG-output closure is still too narrow, because the shipped engine exposes three
channels that bypass it — all verified in code:

- **Parallel siblings.** `_build_user_message` with `inputs:` omitted passes
  `prior_steps: context.steps`, and `context.steps` is **one shared mutable dict**
  written by concurrently scheduled branches (`asyncio.create_task`). A completed
  **non-ancestor sibling** is therefore observable purely by finishing first.
  "All reachable prior producers" ≠ "everything the step can observe."
- **Control semantics.** Edges, conditions, branch topology and workflow-level
  configuration decide *which* producer runs and which values are present. The
  connecting dataflow is executable too, not just the endpoint steps.
- **Mutable external effects.** The catalog ships write/read pairs —
  `file_write`/`file_read`, `connector_send`/`connector_query`,
  `browser_fill`/`browser_submit_form` → `browser_read_text`/`browser_read_table`.
  An edited upstream step can encode into external state that a **byte-identical**
  declassifying step reads through its already-approved tools. No context edge
  connects them at all.

The governing rule:

> **Every user-editable executable semantic capable of affecting the
> declassifying computation MUST be bound into the approval's semantic closure,
> or be statically excluded from that computation.**

Made mechanically conservative for the first build:

```
declassifying AGENTIC step   : explicit `inputs:` REQUIRED.
                               Omission → NOT APPROVABLE (never inferred from
                               the parallel global context).
explicit context input       : bind the producing step's executable revision
                               AND the topology/edge-condition semantics
                               controlling whether it runs.
mutable-external-effect path : a step whose tools can READ state another step in
                               the workflow can WRITE must either declare that
                               effect dependency (binding the writer's revision)
                               or be REFUSED declassification approval.
not statically enumerable    : FAIL CLOSED — bind the whole workflow executable
                               revision.
```

The whole-workflow fallback over-invalidates; that is the correct direction while
no real dependency graph exists, and it can be narrowed later.

An explicit mapping such as `steps.foo.category` binds **the executable revision
producing `foo`**, not the literal path string. Mutable rubric/system artifacts
(`agent_memory.md`, anything editable outside the workflow body) are included
whenever they can influence a declassifying step. Runtime *data values* are NOT
hashed — classifying raw input is the channel's whole purpose; what must be bound
is the user-editable executable semantics that transform it.

#### 1.4a.3 Lifecycle — an atomic state machine (not blind row saves)

The prior code review proved authorization transitions cannot be blind row
saves; the raw grant already uses compare-and-set for exactly this reason (§2,
§4.2). The same discipline applies here.

```
pending → active | rejected | cancelled
active  → revoked | superseded | expired
```

**Expiry is use-time authoritative** (round 4, HIGH — same pattern already
learned for raw grants): attempt binding REFUSES an approval whose `expires_at`
has passed, whether or not a sweeper has materialized the `expired` row yet. A
background sweeper exists only for timely visibility, never as the check.

**`expired` is a full lifecycle state, not vocabulary (round 5, HIGH):**

- **Historical rows: `expired` behaves like `revoked`, not `superseded`.** No new
  bindings, no new completion, ordinary below-grant reads of rows produced under
  it STOP, and live projections are scrubbed; the vault copy stays authoritative.
  This follows from §1.4a.4 — *a tenant authorization cannot mint a longer-lived
  downstream authorization than itself* — and an issuance-window-only reading
  would contradict it.
- **The completion fence checks TIME, not the materialized value.** Inside
  `finish_declassified_attempt` the predicate is
  `status == ACTIVE AND expires_at > now`, so an attempt that bound at 11:59,
  expired at 12:00 and finishes at 12:01 persists **vault-only** even though no
  sweeper has run.
- **Replacement activation cleans up stale rows in the same transaction**,
  otherwise an expired-but-unswept ACTIVE row blocks its own replacement forever
  (the raw-grant lifecycle hit exactly this):

```
lock the current ACTIVE approval, if any
  expired by time                          → ACTIVE → EXPIRED   (audit)
  declaration/semantic revision no longer
  matches the current executable step      → ACTIVE → SUPERSEDED (audit)
then evaluate the new activation predicates and activate exactly one generation
```

This also gives "an ordinary definition edit invalidates the approval" a
**transition owner**: the edit marks it stale, and the next activation
transitions it `ACTIVE → SUPERSEDED` rather than leaving a conflicting ACTIVE row.

**Activation is ONE database transaction** that verifies, and fails if any
check fails: the exact `declaration_hash` + `step_semantic_hash`; both approver
identities (or the tenant artifact, §1.4a.4); the distinctness rules; artifact
scope + validity; the computed capacity against the budget; and **no conflicting
active approval** for the same `(org, workflow, step)`. Exactly one row
transitions.

**Attempt binding is the linearization point.** Before the output-producing
operation begins, the attempt atomically observes an ACTIVE approval and
persists its `approval_id` + `generation`. A concurrent definition edit cannot
affect an in-flight attempt.

**The revocation race is ruled HARD (not lease).** An attempt that bound an
approval which is revoked before the attempt durably finishes becomes
**vault-only** — its operational row is projected as if never declassified. This
is chosen deliberately: the raw is never lost (it is in the vault), so the entire
cost of hard revocation is *over-redaction*, which is the safe direction; lease
semantics would trade that for a residual release the operator must reason about.
*(Round 4 approved this ruling.)*

**A start-side binding alone does NOT make that true (round 4, HIGH).**
Persistence is not one transaction across step row / context / audit / budget —
the engine commits them separately — so a delayed writer can land a declassified
row *after* revoke's scrub has already swept:

```
T1 attempt running under approval A
T2 revoke A; scrubber scans live rows
T1 finishes AFTER the scan → writes a declassified operational row
```

An application-layer read check does not save this: Contract B1 treats the
DB/backup operator as outside that layer, so bytes that land in the operational
DB after the scrub are exposed. **There must therefore be a SECOND linearization
point at completion**, serialized with revoke:

```
finish_declassified_attempt(attempt_id, bound_approval_id, bound_generation,
                            projected destination copies)
  ── one transaction ──
  lock / CAS-check the bound approval
    still allowed → persist the below-grant copies
    revoked/expired → persist vault-only / redacted copies
```

Revocation takes the compatible lock/CAS ordering, leaving exactly two legal
outcomes: **completion first** (row may commit; revoke then denies reads and
scrubs it) or **revocation first** (completion observes revoked; no declassified
operational copy is ever committed).

**`superseded` vs `revoked` are distinct:**

```
superseded : no new attempts; historical rows REMAIN authorized
revoked    : no new attempts; historical rows CEASE ordinary below-grant release
```

#### 1.4a.4 Authority — dual control, and a narrowly-scoped tenant artifact

A single Org Admin declassifying tenant data is the same unilateral act §2.1
forbids for raw access, so approval requires **`dual_administrator`** (two
distinct Administrators / Org Admins, requester ≠ approver) or
**`tenant_authorized`**.

A raw-grant artifact is scoped to a grant; a declassification artifact must be
**far narrower** — an org-wide "tenant approves safe output" must never authorize
future prompts or alphabets. The artifact binds exactly:

```
organization, workflow, step
declaration_hash, step_semantic_hash
destination/consumer scope, roles, retention
computed capacity_bits + the policy budget it was approved against
validity period (if any)
```

Any mismatch **rejects activation** — and validity is not merely an
activation-window check: an artifact's `valid_until` **caps the approval's
`expires_at`** (§1.4a.2). A tenant authorization cannot mint a longer-lived
downstream authorization than itself. Reuse for another step, clone, template
instance, declaration revision, or capacity increase is impossible by
construction.

Ordinary definition edits keep `ORG_WRITE_ROLES` — they simply *invalidate* the
approval rather than producing an approved declassifying step.
`POST /api/workflows/import` and clone/template paths take the identical
authorization + audit path. The **NL scaffold is FORBIDDEN from emitting a
`declassify` block** (validation error, not a silent strip): an LLM authoring a
governance boundary is the §1.4 failure mode one level up. Every create / edit /
activate / revoke audits workflow, step, `approval_id`, generation, old + new
hashes, and approver identities.

#### 1.4a.5 Permitted forms + capacity — finite, canonical, budgeted

Permitted: `enum`, enum-**list**, `integer` (finite lattice), `bool`.

```yaml
steps:
  - id: classify
    declassify:
      category:
        enum: [urgent, fyi, spam, personal, awaiting_reply]
        purpose: routing                 # closed enum (summary only)
        destinations: [step_output, context]   # closed enum, see §1.4a.7
        consumer: engine.condition        # closed enum, below
        roles: [org_admin, org_user]      # closed enum — the platform Role names
        retention: instance_lifetime      # closed enum, below
        provenance: model_derived         # closed enum, below
        opaque_sufficient: false          # bool — could an opaque id serve instead?
      attention:                       # a LIST — the field that motivated §1.4a
        enum: [urgent, awaiting_reply, review, none]
        max_items: 4
        ordered: false                 # canonicalized (sorted); duplicates rejected
        purpose: routing
        destinations: [step_output]
        consumer: engine.routing       # every field repeats ALL of these —
        roles: [ORG_ADMIN]             # there is NO inheritance from the field above
        retention: { kind: window, seconds: 2592000 }
        provenance: model_derived
        opaque_sufficient: false
```

The list form is required by the real workloads (`attention`, `apply_labels`,
paper-triage `tags` are deduped *lists* in `engine/functions.py`); it lands under
§1.4's existing "explicitly approved bounded metadata" clause. Lists freeze:
exact JSON array type; members unique (**duplicates rejected**); order
canonicalized unless `ordered: true`; `null` prohibited; empty-list semantics
defined; `max_items` ≤ enum size.

The float `number: {min,max}` form is **withdrawn** — over floats it is not a
finite alphabet. Replaced by an integer lattice: `integer: {min, max, step}` with
`step > 0`, `min ≤ max`, accepted values `min + k·step ≤ max`, bounded integer
encoding, no NaN/infinity, bool is not an integer. Continuous values stay
vaulted; a numeric category is a numeric enum.

**PERMANENT NON-DO:** no `string`, `regex`, `pattern`, or `any` form, ever.

**The §1.3 fields are CLOSED ENUMS, not prose (round 4, contract correction 1).**
Criterion 48 previously required a consumer/role/retention/provenance mapping the
grammar never defined, so it had no schema to test. Frozen vocabularies:

```
consumer     : engine.condition | engine.routing | api.read | ui.display
               | dedupe.key | audit.governance
roles        : list of WIRE TOKENS (below) — never display strings
retention    : instance_lifetime
               | { kind: window, seconds: <bounded int> }
               | { kind: policy, retention_policy_id: <content-addressed id> }
               | indefinite
provenance   : model_derived | third_party_derived | platform_computed
destinations : step_output | context | explain | ws | audit_governance
opaque_sufficient : bool
```

**Role wire tokens are frozen separately from the display names** (round 5) —
"the platform Role names" was ambiguous, and the earlier example used
`org_admin`/`org_user`, which match no runtime value:

```
ADMINISTRATOR  ORG_ADMIN  ORG_USER  ORG_VIEWER
```

mapped in one place to the runtime `Role` enum (`Administrator`,
`Organization Administrator`, `Organization User`, `Organization Viewer`). The
declaration hashes the token, so renaming a display string never changes an
approval's identity.

**`retention_window` as a bare enum is withdrawn** (round 5): a configured window
an operator can later widen would silently extend an already-approved,
already-hashed authorization. Retention must carry either an explicit bounded
`seconds` or an immutable content-addressed `retention_policy_id`, so the exact
authorization is hashable and artifact-bound — which is what criterion 48's word
"exact" requires.

All are required per declared field. `purpose` remains a summary tag and does not
substitute for them. Free text appears nowhere — the v6 F2 rule (no governance
prose in a persisted row) applies here too. These same values are what the tenant
artifact binds, so its role/retention scope is mechanically reproducible rather
than prose-only.

**Capacity counts every OBSERVABLE state** (re-review finding 5) — not just the
accepted values. A hostile model can select *valid*, *absent*, or *invalid* to
encode information, and partial projection (§1.4a.8) makes "redacted" visible:

```
observable(field) = accepted values
                  + absent            (when optional)
                  + redacted          (when distinguishable)
                  + any distinct validation/status representation

capacity(field) = log2(|observable(field)|)
capacity(step)  = Σ capacity(field)  ≤  POLICY_BUDGET_BITS
```

List cardinality is computed exactly — unordered-unique up to *m*: `Σ C(n,k)`;
ordered-unique: `Σ P(n,k)` — over the declared `k` range including the
empty-list rule.

**`POLICY_BUDGET_BITS = 32`** for the first build. Bounds: ≤8 declared fields
per step, ≤16 enum values per field, ≤32 chars per value (opaque-token charset).
A declaration whose computed capacity exceeds the budget is a **save-time
error**, and **raising the budget requires the same dual-control approval as an
approval itself** — otherwise the "budget" is a policy hook, not a contract.

**Honest naming.** A per-attempt alphabet is NOT an aggregate confidentiality
bound: the channel is finite per output but **unbounded over repeated
execution**, with a hostile sender able to influence run volume. External
onboarding stays gated on the separately-named aggregate/rate decision
(§1.4a.10), and `THREAT_MODEL` carries the exposure.

#### 1.4a.6 Revocation — what it does, and what it CANNOT do

Content-addressing must not make an approval permanent. Content and
authorization are split:

```
declaration content : immutable, permanently resolvable (integrity only)
approval status     : pending | active | revoked | superseded
effective authz     : whether rows produced under THAT approval_id stay readable
```

Revocation MUST: stop new attempts binding it; stop ordinary below-grant reads of
rows bound to that `approval_id`; keep historical re-projection available for
integrity verification; drive a scrub/re-projection backfill of live operational
rows; leave the vault copy authoritative; be recognized by the zero-raw verifier;
and audit the operation.

**The honest limit (re-review finding 6).** Revocation prevents *new*
declassification, removes authorization for application reads, and scrubs
supported **live** operational stores. It **does NOT retract bytes already
released or already copied into database dumps, backups, replicas, query logs, or
operator exports** — and the B1 adversary is precisely the database/backup
operator. **A wrongly-approved declaration is an exposure INCIDENT, not a
reversible configuration mistake.** Revocation therefore reports:

```
new executions blocked | in-flight attempts affected | live rows scrubbed
rows that could NOT be scrubbed | backup/replica retention exposure | completion state
```

#### 1.4a.7 Destination-scoped authorization is part of the projection identity

§1.3 requires per-field consumer / destination / roles / retention /
opaque-sufficiency justification, and a field justified for a routing edge is
**not** thereby authorized into step output, context, audit detail, WS events,
and explain responses. A single projected object copied everywhere would violate
exactly that (re-review finding 7), so projection is **destination-aware**:

```
project(asset_kind, destination, raw_value, approval_id, projector_version)
```

Each persisted or rendered copy records enough identity to reproduce its own
decision. The allowed destination set is part of the declaration's canonical
bytes, the approval scope, the tenant artifact, the step-attempt binding, and the
verifier's inventory.

**`audit` is NOT a destination for declassified business content (round 4,
HIGH).** Three properties cannot hold at once: *audit carries declassified
business bytes* · *audit is append-only* (the repo exposes only
`append`/`list_*`) · *revocation scrubs all live declassified copies*. A
compensating "redacted" entry does not help — the B1 adversary reads the original
row directly. We drop the first. An audit row may carry only **content-free
governance identity**:

```
approval_id · generation · declaration_hash · field + destination identity
projection outcome · opaque row/reference id
```

**No independent sidecar store is created (round 5, HIGH — corrected).** The
round-4 wording proposed a "revocable projection sidecar", but that is exactly an
unenumerated below-grant persistent copy: it appeared in none of the closed
destination vocabulary, the approval identity, the tenant artifact, the finish
transaction, the revocation scrub, or the verifier inventory — i.e. it moved the
persistence problem rather than closing it. Instead:

> An operator-facing audit VIEW may display a business value only by
> **dereferencing an already-authorized destination copy** (e.g. `step_output` or
> `context`), subject to that destination's existing roles, retention and
> revocation policy. It creates no new store and no new destination.

If a genuinely independent audit projection is ever needed, it must first become
a **first-class destination** (`audit_view`) bound into approval + generation,
roles, retention, artifact scope, the `finish_declassified_attempt` transaction,
the revocation scrub, and the verifier inventory — not added as a read model.
Criterion 56 pins the no-sidecar representation. **P2's raw-surface inventory is the mechanism that proves
every destination invokes this decision** rather than copying an
already-projected object by convention.

A closed `purpose` enum (`routing`/`ranking`/`display`/`dedupe`/`audit`) is
summary metadata only — it does not replace the §1.3 fields. The lexical rule
cannot carry that weight either: a 32-char no-whitespace token can still be
`alice@example.com`, `INV-2026-000184`, or a hex fragment, and §1.4 vaults
external identifiers unless individually approved. **Non-sensitivity comes from
the approval's provenance and scope, never from the charset.** The validator
rejects duplicate enum literals and requires a canonical catalog.

#### 1.4a.8 Canonical encoding + persistence (§4.3 coupling) — FROZEN concretely

Both hashes are load-bearing, so the algorithm is specified executably rather
than described (re-review finding 4):

```
canonical bytes : RFC 8785 JSON Canonicalization Scheme (JCS), UTF-8,
                  Unicode NFC, duplicate object keys REJECTED before hashing,
                  exact integer representation, per-field array-order rules
digest          : SHA-256 over a domain-separated prefix
                  ("wp/trace/declaration/v1" | "wp/trace/step-revision/v1")
                  + canonical bytes
```

The **canonical bytes are stored beside the digest**; a matching digest with
different stored bytes is a **FATAL integrity event**, never a merge.

**Two exactness rules, so criterion 57 is genuinely cross-process (round 4,
contract correction 2):**
- **Integer domain.** `min`/`max`/`step` must lie in the **I-JSON safe-integer
  range** (|n| ≤ 2^53−1); "exact integer representation" is not left to the
  implementation. Values outside it are a save-time error.
- **Unordered-list comparator.** For `ordered: false`, the canonical array is
  produced by **NFC-normalizing each literal, then sorting by Unicode
  code-point order**. Without naming the comparator, two correct
  implementations can hash the same set differently.

The declaration is persisted **content-addressed in an append-only table**;
definitions mutate in place and version history is deferred (E5), so a hash alone
would leave pre-edit rows unprojectable and break criterion 17. Legacy
`projector_version` `"1"` / `"2"` parse as **"no declaration"**; an **unknown
hash fails closed**; the TG3c zero-raw verifier **resolves each row's declaration
AND approval** before judging it (its check is structural, so applying only the
platform-global projector would false-positive across the 1,340 backfilled
instances).

**Partial projection is frozen:** one invalid declared field redacts **only that
field**; independently valid declared fields survive. Deterministic by
construction — and its observable "redacted" state is counted in §1.4a.5's
capacity.

#### 1.4a.9 Fork / retry — narrowed claim

Content-addressed persistence makes **historical projection evaluable**. It does
**not** by itself make retry, resume, or fork compatible across definition
changes: per `EXECUTION_SEMANTICS`, retried/resumed runs load the *current*
definition and forks copy old outputs into the *current* graph, so an old
`category` can be faithfully re-projected and still routed under new meanings.
Exact re-projection proves integrity, not semantic compatibility.

For §1.4a-enabled steps, either bind the full source definition revision and
**reject** retry/fork when it differs, or run an explicit compatibility check
over declared output schemas + downstream consumers. The §4.3 rehydration
predicate is unchanged.

#### 1.4a.10 Structural rules + deferrals

- **Namespace:** declared fields live under a dedicated `business` object rather
  than the flat output root, so the projector validates a nested structure
  instead of the reserved-name list growing with every new platform field. A
  later platform release reserving a formerly-permitted name needs a defined
  response for historical declarations.
- **Reserved names** (platform-global registry, projector metadata, raw-by-taint
  `output_text`/`error`/`recall`/`tool_calls`) are a **save-time validation
  error**. Platform-global wins on collision.
- **Enforcement:** a runtime value failing its declared rule is redacted and
  vaulted exactly like an unregistered field. A declaration grants a *validator*,
  never a bypass.
- **Criterion 18 equivalence:** "approved" means *bound to a step revision under
  dual-control authority, activated atomically, destination-scoped, validated at
  save, audited by approval id + hash*.
- **Deferred, with triggers:** cross-step/workflow-level declarations (trigger:
  duplication becomes painful); per-tenant override of a bundled template
  (trigger: first external tenant forking one); histogram/bucketing forms
  (trigger: a workload needing a continuous value below grant); an enforceable
  **aggregate** capacity budget + rate boundary (trigger: **first external
  tenant** — onboarding stays gated on this).

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
37. **(§1.4a)** A value that does NOT satisfy its declared rule — not in the
    enum, out of numeric range, wrong type — is redacted and vaulted exactly
    like an unregistered field. A declaration is a validator, never a bypass.
38. **(§1.4a)** A `declassify` block naming a reserved field (platform-global
    registry, projector metadata, or a raw-by-taint field such as
    `output_text` / `error` / `recall` / `tool_calls`) is a **save-time
    validation error**, not a silent ignore.
39. **(§1.4a)** Editing a declaration leaves PRE-EDIT rows projectable: the old
    declaration resolves by content-address, the row re-projects under its
    recorded version, and criterion 17 still holds. An **unknown** declaration
    hash fails closed (never falls back to the current declaration).
40. **(§1.4a)** The NL scaffold path cannot emit a `declassify` block (a
    scaffold that does is a validation error), and creating/editing one via any
    path — including `POST /api/workflows/import` — requires `ORG_ADMIN_ROLES`
    and emits an audit entry carrying the canonical declaration hash.
41. **(§1.4a)** An enum-list value exceeding `max_items`, or containing a member
    outside the declared enum, redacts; and the zero-raw verifier resolves each
    row's declaration before judging it (no false positives on
    declaration-projected rows).
42. **(§1.4a.2, ext 08-04; widened 08-08)** A prompt-only, model-setting,
    tool-set, input-mapping, or output-schema edit **invalidates** the approval —
    the step reverts to vault-by-default until re-approved. **Widened by round 4:
    editing only an UPSTREAM producer** (e.g. the agentic step whose
    `output_text` a declassifying `record` step reads) — leaving the declassifying
    step byte-identical — must also invalidate it. Includes the `inputs:`-omitted
    case, where the conservative closure is all prior producers, and edits to
    `agent_memory.md`. **Widened again by round 5** — the closure is the
    INFLUENCE graph, not DAG ancestry, so each of these must also invalidate:
    (a) a **non-ancestor sibling** edited when the declassifying step could
    observe it through the shared `context.steps`; (b) an **edge/condition-only**
    edit that changes which producer runs; (c) a **mutating-step → shared
    external state → read-tool** path (`file_write`→`file_read`,
    `connector_send`→`connector_query`, browser write→read) with the
    declassifying step byte-identical. A declassifying agentic step with
    `inputs:` omitted is NOT APPROVABLE.
43. **(§1.4a.2)** An in-flight attempt uses the revision bound **before model
    invocation**; a concurrent definition edit does not affect it.
44. **(§1.4a.3)** A single Org Admin cannot create a declaration-based bypass of
    the raw-grant boundary — safe-output approval requires dual-control
    (`dual_administrator`) or `tenant_authorized`, matching §2.1.
45. **(§1.4a.4)** List projection rejects duplicates and canonicalizes (or
    rejects) ordering; `null` and non-array values redact.
46. **(§1.4a.4)** Numeric declarations accept only values from a finite exact
    integer lattice (`min`/`max`/`step`); floats, NaN, infinity, and bools redact.
47. **(§1.4a.6)** A REVOKED declaration ceases below-grant release for rows
    already produced under it, while remaining resolvable for integrity
    verification; the verifier recognizes the revoked state and the backfill +
    affected-row count are audited.
48. **(§1.4a.7)** Every declared field carries an exact consumer / destination /
    role / retention / provenance mapping; a field justified for one destination
    is not thereby released into others (output, context, audit, WS, explain).
49. **(§1.4a.9)** Retry / resume / fork across a changed definition for a
    §1.4a-enabled step is REJECTED or compatibility-validated — exact
    re-projection alone is not accepted as semantic compatibility.
50. **(§1.4a.5)** The computed channel capacity (Σ log₂|observable domain| over
    declared fields, including list length/membership/order/duplicate rules) is
    enforced against `POLICY_BUDGET_BITS` at save time.
51. **(§1.4a.2, ext 2026-08-05)** A row produced under REVOKED approval A1 stays
    withheld even when an ACTIVE approval A2 exists with **identical declaration
    bytes** for the same step — rows bind `approval_id` + generation, not
    declaration content. Re-approval does not resurrect earlier rows.
52. **(§1.4a.3)** Activation is one transaction: two concurrent activations for
    the same `(org, workflow, step)` yield exactly one ACTIVE approval, and an
    activation whose hashes, approver distinctness, artifact scope, or capacity
    budget fail is rejected atomically.
53. **(§1.4a.3; widened 08-08)** An attempt that bound an approval later REVOKED
    before it durably finishes is persisted **vault-only** (hard revocation); a
    SUPERSEDED approval blocks new attempts while its historical rows remain
    authorized. **Both serializations are exercised**, not just the end state:
    (a) completion commits first, then revoke denies reads + scrubs it; (b) revoke
    (including its scrub) completes first, and the delayed writer then commits NO
    declassified operational copy. Case (b) is the one a start-side binding alone
    fails.
54. **(§1.4a.4; widened 08-08)** A tenant artifact scoped to a different step
    revision, declaration, destination set, capacity budget, **roles, retention,
    or validity period** rejects activation; an artifact cannot be reused for a
    clone/template instance. **Plus expiry:** an approval whose `expires_at` has
    passed refuses attempt binding at USE time, before any sweeper has
    materialized `expired`; and activation refuses an `expires_at` later than the
    artifact's `valid_until`. **Round 5 adds:** a row produced BEFORE expiry
    ceases ordinary below-grant release AFTER it (expired behaves like revoked,
    not superseded); and replacement activation transitions a stale ACTIVE row
    (`→ EXPIRED` by time, `→ SUPERSEDED` when its semantic revision no longer
    matches the step) **in the same transaction**, so an unswept row cannot block
    its own replacement.
55. **(§1.4a.5)** Capacity accounting distinguishes absent / valid / redacted
    states; a declaration whose observable domain exceeds the budget fails at
    save, and raising `POLICY_BUDGET_BITS` requires the same dual-control path.
56. **(§1.4a.7; widened 08-08)** A field authorized for `context` only is NOT
    released into **`step_output`**, audit, WS events, or explain — each
    destination reproduces its own projection decision rather than copying an
    already-projected object. **And no declassified business value is written into
    an append-only `audit_log.detail` row at all**: audit carries content-free
    governance identity only, with any operator-facing value resolved through the
    revocable projection sidecar (§1.4a.7).
57. **(§1.4a.8)** The canonical hash is reproducible across processes (RFC 8785
    JCS + NFC + SHA-256, domain-separated), and a stored digest whose canonical
    bytes differ is a FATAL integrity event, never a merge.

## 9. Cut plan — architecture approved; freezes gate authorization

| Cut | Contents | Contract | Authorization |
|---|---|---|---|
| **TG1** | grant **state machine** (§2) + two `approval_mode`s (§2.1) + closed reason_code/opaque ticket_ref + expiry transition (§2/F6) + target-org bypass + read-site `ADMIN_TIER`→grant (incl. WS) + grants admin API + revoke-on-deactivation/transfer | A | **BUILT 2026-08-01** (memory facts-mode-under-grant deferred to the memory endpoint's own cut) |
| **TG2 (human release)** | raw-access audit: **release-boundary model (§3.1)** + cardinality + explicit degradation, HTTP + WS release-before-send | A | **BUILT 2026-08-01** (`partial`/vault outcomes arrive with TG3) |
| **TG2 (system access)** | `raw_trace_system_access_attempted`-before-decrypt + fail-closed (§3.2) | A | **authorizable (v6 audit-before-decrypt folded — pending re-review)** |
| **TG3a** | typed registry (§1.2) + safe-output contract (§1.4) + vault-all-free-form + abstract vault repo/opaque IDs (§0.2) + schema (§4.1) + collision-free key; **DARK DUAL-WRITE** | A | **BUILT 2026-08-01** (same-DB; cross-system fencing §4.2 lands with the separate-vault B form at TG3d) |
| **TG3b.1** | rehydration read-back (§4.3) + integrity + **system-access audit (§3.2)**, fail-closed | A | **BUILT 2026-08-01** (additive; validated against inline) |
| **TG3b.2** | the write flip (flag-gated `trace_safe_only`): persist safe-only + durable-or-fail (§4.2) + rehydrate on resume/fork | A | **BUILT 2026-08-01** (default OFF; execution-state tables zero-raw at rest) |
| **TG3b.3** | read-surface raw-merge for grant-holders under the flip | A | **BUILT 2026-08-01** (tool_call audit-at-rest + manifest §5.1 remain) |
| **TG3c** | backfill + zero-raw verifier (§8.14) + CLI | A | **BUILT 2026-08-02** (retention/expiry + manifest §5.1 remain; cross-org fork already structurally prohibited) |
| **TG3d-1** | B1 boundary: per-org envelope encryption (keys outside the DB role), AEAD-bound ciphertext; dual-control grant + THREAT_MODEL §5a amendment remain for the gate | **B1** | **BUILT 2026-08-02** (crypto core; gate-wiring — dual-control + §5a — remains) |
| **TG3d-2** | B2 boundary: move the decrypt identity into an attested/enclaved runtime OR external/customer-mediated key release; THREAT_MODEL §5a amended (host-operator-resistant) | **B2** | own threat-model + infra design; pulled forward when a customer contract requires operator-non-decrypt |

**Sequencing invariant:** TG3a must not flip the operational store to
safe-only before TG3b (else an instance born between them resumes/forks
wrong) — hence the dark dual-write, or TG3a+TG3b ship atomically.

**No external organization is provisioned until the gate's required contract
(§0.1) — up to and including TG3d — is in effect**, not merely TG1–TG3c.
Estimate: TG1+TG2 ~2 days; TG3a–c ~5–7 days; TG3d scoped separately.
