# Workflow Execution Semantics — the Contract

Status: **written 2026-07-31** (G19; external review §6 / bundle item 2).
This documents what the engine **guarantees today**, verified against
`engine/executor.py` and the test suite — not a target design. Statements
about behavior that does NOT exist are marked **Not provided**. From this
date the document is normative: a change to any stated semantic is a
breaking change and updates this file in the same commit.

## 1. Entities and state machines

```
WorkflowInstance:  PENDING → RUNNING → {COMPLETED | FAILED | KILLED}
                              ↕ PAUSED (resumable; budget or operator)
                   FAILED —retry→ RUNNING (operator endpoint)
StepExecution:     PENDING → RUNNING → {COMPLETED | FAILED | CANCELLED}
                   PENDING → SKIPPED
```

A **sibling cancelled** by another branch's failure — or by a pause/kill
mid-flight — is persisted as `CANCELLED` (implemented + test-pinned
2026-08-01; the external review correctly found the earlier version
claimed a state the enum lacked). It is distinct from FAILED (its own
execution failed), SKIPPED (graph conditions made it unnecessary), and
PENDING (never began), so recovery and audit can tell a started-and-
cancelled step from an unscheduled one. A paused instance re-runs its
CANCELLED steps on resume (CANCELLED is not in `already_done`). Orthogonal to workflow state are **recovery-reasoning categories** an
operator applies when interpreting a failed/cancelled step —
`not_dispatched` (safe to retry) · `dispatched_outcome_unknown`
(timeout/cancel mid-call) · `effect_confirmed` · `effect_failed`. **These
are NOT machine-recorded today** (external-review clarification: the
engine keeps no acknowledgement state, so it cannot persist them). What
IS recorded: the step's engine state (`CANCELLED`/`FAILED`/…) and the
audited tool call + whatever result came back. Persisting the recovery
category from real dispatch/ack state is the future contract (G21) — until
then it is a human reasoning aid, not a guarantee.

- `KILLED` is terminal and not resumable; `PAUSED` resumes from the next
  unstarted step. **Retry of a `FAILED` instance, exact transition
  (verified against code):** the endpoint sets the instance
  `FAILED → PAUSED` then drives `resume()` (`PAUSED → RUNNING`); the
  resume path's `already_done` set contains only `COMPLETED` + `SKIPPED`
  steps, so **the `FAILED` step is re-run** and its row is overwritten
  when it executes again (effectively `FAILED → RUNNING`, no separate
  PENDING). Completed step outputs are preserved and never recomputed.
  Not addressed by re-running: a prior `dispatched_outcome_unknown`
  effect (the retry re-runs blind — see the §4 invariant), cancelled
  siblings (re-evaluated from the graph), and downstream skips
  (re-evaluated). Retry counters reset per step-run.
- Every state transition and every agent tool call appends an audit
  entry; step outputs persist when the step completes.

## 2. Trigger delivery guarantees (per trigger type)

**Delivery semantics are TRIGGER-SPECIFIC; there is no global delivery
guarantee** (external-review correction — the earlier "platform-wide
at-least-once" claim was contradicted by this same table). Email and
filesystem aim for at-least-once under the stated bounds; webhook,
schedule, and API are best-effort or caller-mediated. Exactly-once is
**not provided** anywhere.

| Trigger | Guarantee | Dedupe | Missed-while-down |
|---|---|---|---|
| `email` (Gmail poll) | at-least-once | persisted time cursor + last-500 seen-id ring (G9) | backfilled from the cursor on restart |
| `filesystem` | at-least-once per file appearance | in-process only | files present at start fire once |
| `webhook` | one accepted POST → one instance, started synchronously in-request | **none — caller owns idempotency** | lost (no queue; no persist-before-ack) |
| `schedule` | per tick while the process runs | n/a | missed ticks are NOT replayed |
| `manual` / API run | one request → one instance; caller gets the instance id | none | n/a |

**Webhook/API acceptance precision (external review §4):** there is **no
persist-before-ack** — an accepted request runs the instance in-process,
so a crash after accept-but-before-first-step-persist loses that instance
with no trace, and a caller retry creates an **independent** instance (no
client idempotency key exists). The caller owns dedupe before acceptance;
after a 2xx the instance id is the handle. A persisted inbound queue with
an optional idempotency key is the named follow-up (G21).

Duplicate delivery is therefore possible (cursor-persist failure, seen-id
ring overflow past 500, webhook re-sends). **Consequence: every workflow
whose steps mutate external state must tolerate re-processing the same
trigger payload.** The two production workloads do: label application is
idempotent at Gmail (adding a present label is a no-op) and DMARC
delivery overwrites by basename.

## 3. Execution model

- Steps run in dependency order; independent branches run concurrently
  (`asyncio.wait FIRST_COMPLETED`, edge-driven readiness). There is no
  cross-instance coordination: N instances of one workflow run fully
  independently, and last-write-wins on any shared external target.
  **This is a load-bearing semantic, not a detail** (external review §5):
  two triggers for the same object, concurrent record updates, a fork
  racing its origin, or schedule overlap all resolve last-write-wins with
  no locking. **Contract until concurrency keys exist: a workflow whose
  steps can target the same external object concurrently is unsupported
  unless that operation is commutative or idempotent.** A
  `concurrency: {key, mode: reject|queue|replace|allow}` control is the
  named follow-up.
- Edge conditions are simpleeval expressions over `trigger`/`steps`
  context. A **falsy** result makes the edge inactive. A condition that
  **errors** (typo, unexpected shape) **fails the instance by default**
  (changed 2026-07-31: a broken condition must never silently bypass a
  validation/approval/containment gate). An edge whose target is
  genuinely optional may opt into the old behavior with
  `on_error: inactive` — which `validate_definition` should warn/reject
  on when the target is a mutating/approval/security step or is reachable
  only through that edge (a mutating step must not become skippable via a
  swallowed error). That validation is a G21 item; the opt-out exists but
  is not yet safety-checked. A step whose incoming edges are all
  resolved-inactive becomes `SKIPPED`; skip propagates downstream unless
  another active path reaches the step.
- A step failure (after retries) fails the instance and **cancels
  in-flight sibling branches**; their external effects up to the
  cancellation point are NOT undone (see §6).
- Step outputs are immutable once written. Reruns happen only via new
  instances (retry re-executes non-terminal steps; completed steps'
  outputs are never recomputed in place).
- **Persistence is NOT atomic across records (verified against the
  Postgres repos — external review §5).** Step-output+state (one row, one
  transaction), the workflow-context/instance update, each audit append,
  and budget accounting are **separate transactions**. A crash between
  them can leave: output stored but instance still RUNNING, a completed
  effect with a missing audit line, or state committed but cost
  unrecorded. Recovery (§7) re-runs any non-`COMPLETED` step, which
  bounds the first case; the others are accepted small windows at
  single-operator scale. A post-crash consistency check + shared-txn
  step-commit is a G21 item.

## 4. Retries — and why the default is zero

- `runtime.retries` per step, **default 0**. A retry re-runs the entire
  step (an agentic step replays its whole conversation from scratch).
- The engine does **not** currently gate retries on error class or on
  `Tool.effect` — a configured retry fires for any failure. The
  contract is therefore a rule for workflow authors, test-enforced for
  the bundled examples:

  > **Set `retries > 0` only on steps whose tools are all `read_only`,
  > or whose mutations are idempotent against the external system.**

  The one retrying step in production (`apply`, retries=2) satisfies
  this: Gmail label-add is idempotent. `email_send` is NOT idempotent
  and no bundled workflow retries a step holding it.
- The reviewer-supplied invariant is adopted as the normative rule:

  > **A retry may never occur merely because the platform did not
  > receive a successful response; it occurs only when the effect
  > contract makes repetition safe, or a stable idempotency mechanism
  > prevents duplication.**

  Corollary — three outcomes must be distinguished when reasoning about
  a failed step: *request failed before dispatch* (safe to retry),
  *outcome unknown* (timeout/cancel mid-call — retry only under the
  invariant), *side effect confirmed* (never retry the effect). The
  engine does not yet track acknowledgement state; today the
  distinction is carried by tool choice (idempotent label-add) rather
  than machinery.
- **High-priority follow-up (external review §4): make the invariant
  static-checkable now.** `Tool.effect` already exists, so
  `workflow.validate_definition` can, without acknowledgement-aware
  execution, pass `retries > 0` automatically for all-`read_only` steps,
  require a declared idempotency strategy on mutating steps, and reject
  retries on unknown/non-idempotent effects (manual override explicit +
  audited). Tracked as G21.
- **Not provided (execution-time):** automatic error classification,
  acknowledgement-aware retry, engine-enforced effect-gating at runtime.

## 5. Timeouts, budgets, pause

- Per-step `runtime.timeout_seconds` and per-workflow
  `policies.timeout_seconds` cancel the step / instance; cancellation
  mid-tool-call abandons the call (external effect may have happened —
  same at-least-once caveat as §2).
- Token budget (`policies.max_total_tokens`) is checked **after each
  step**, not mid-step: an in-flight agentic step can overshoot by up
  to its own per-step cap before the pause lands. `budget_action`:
  `notify` (audit + continue), `pause` (default), `escalate`.
- Operator pause is polled **between steps**; a running step completes
  first. Kill marks the instance KILLED at the next between-step check;
  in-flight Bedrock/tool calls are not interrupted mid-call. **The
  request/effected distinction should surface in the UI/API**
  (`pause_requested`→`paused`, `kill_requested`→`killed`) so an operator
  who hits "kill" during a consequential call is not misled into thinking
  the call was interrupted — a follow-up on the API surface (G21).

## 6. Failure, partial completion, compensation

- **Compensation/rollback is not provided.** A failed instance leaves
  completed steps' external effects in place, fully audited. Recovery
  is operator-driven: the retry endpoint (resume from failure), fork
  (re-drive from a chosen step with ancestor outputs preserved), or
  manual remediation guided by the audit log.
- Partial-failure semantics for the acting email path are pinned in
  `EMAIL_TRIAGE_ACT_PLAN` §6b (classification recorded even when apply
  fails; apply never blocks recording).

## 7. Crash recovery and resume

- Per-step persistence is the recovery unit: after a process crash, the
  instance is re-driven with `already_done` seeded from persisted
  COMPLETED/SKIPPED steps — completed work is not repeated, but a step
  that was RUNNING at crash time re-runs in full (at-least-once again).
- **Honest limitation (external review §6): recovery is
  restart-triggered, not lease-arbitrated.** The single-process
  deployment recovers its own instances on boot; there are no worker
  leases, heartbeats, stale-running timeouts, or atomic ownership. A
  RUNNING step is re-driven **only** because the one process restarted —
  in a multi-process deployment this would need leases to avoid two
  workers driving one instance during a partition. Named prerequisite
  for horizontal scale (G21).
- Trigger-side: the email cursor persists (G9), so mail arriving during
  downtime is delivered late, not lost.

## 8. Versioning, mutation, lineage

- Definitions are replaced whole (save/import). **An in-flight instance
  is NOT version-bound (verified — external review §6):** the retry/resume
  path loads the CURRENT definition by id (`definitions.get`), not a
  stored snapshot or hash, so a crash-recovered or retried instance runs
  against whatever the definition is *now*. Prompts, tool schemas,
  resolved capabilities, model settings, and memory are likewise read
  fresh, not snapshotted. This is safe while definitions aren't edited
  mid-flight and is the honest current behavior; persisting an immutable
  definition identity (content hash) on every instance — the prerequisite
  for correct recovery, replay, forensics, AND the fork guard below — is a
  high-priority G21 item.
- **Definition deletion, exact contract** (external review 2026-08-01 #6):
  *No history* → deletes freely. *History present* → **409 unless
  `force=true`** (the delete then cascades that definition's instances +
  step_executions; audit entries are immutable and preserved). *Any
  non-terminal run (pending/running/paused)* → **409 regardless of
  `force`** (never delete rows out from under the engine). *Role* →
  **Organization Administrator or Administrator only** (tightened from Org
  User, external review finding 5 — an ordinary user must not erase
  forensic history). *Audit* → a `workflow_deleted` entry records the
  cascade counts. Summarized here; the prose below is the rationale.
  Deleting a definition **requires `force=true` when any run history
  exists** (changed 2026-07-31 per external review §8 — a plain delete no
  longer silently cascades the audit trail; a history-free draft still
  deletes freely, and non-terminal runs still 409 regardless). Full
  disable/archive/soft-delete with retained immutable versions is the
  larger model, tracked G21; the `force` guard is the interim containment
  so a UI button can't destroy forensic evidence by accident.
- Fork (`POST .../fork`): new instance with topological-ancestor outputs
  copied as COMPLETED, driven from the chosen step under the CURRENT
  definition + memory state. **The reviewer is right this is dangerous
  when the definition has since changed** (§7): old ancestor output
  schema meeting new downstream expectations. The intended split, one a
  named follow-up (G21): `replay_fork` (source definition + source memory
  snapshot — the safe default) vs `migration_fork` (current definition,
  with explicit compatibility validation). Today's single behavior is the
  migration variant WITHOUT the validation — safe on an unchanged
  definition, which is the only case exercised. The reviewer's proposed
  containment (reject a fork when current-def-hash ≠ source-instance
  def-hash) is the right guard but **depends on the version-binding field
  above** — the instance stores no definition hash to compare yet — so it
  is bundled into that G21 item rather than half-built against a missing
  field. The `workflow_forked`
  audit entry should carry source-instance, source + destination
  definition hashes, and memory-snapshot identity (follow-up).
- **Not provided:** dynamic graph changes to running instances,
  approval/escalation expiry (escalations remain open until resolved).

## 9. Quick reference — what is explicitly NOT provided

Exactly-once delivery · cross-step transactions · compensation ·
engine-enforced retry/effect gating · mid-step budget enforcement ·
in-flight definition migration · schedule catch-up · webhook queueing ·
approval expiry · worker leases / multi-process recovery arbitration ·
concurrency keys · fork version-binding validation · soft-delete
retention. Each is either an authoring rule (§4), an operator
procedure (§6), or a trigger-gated backlog item.
