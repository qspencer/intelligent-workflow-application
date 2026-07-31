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

A **sibling cancelled** by another branch's failure is distinct from
FAILED (its own execution failed), SKIPPED (graph conditions made it
unnecessary), and PENDING (never began). Orthogonal to workflow state,
a step that dispatched an external effect records an **effect outcome**
for recovery reasoning: `not_dispatched` (safe to retry) ·
`dispatched_outcome_unknown` (timeout/cancel mid-call) · `effect_confirmed`
· `effect_failed`. Today `CANCELLED` and the effect-outcome tags are the
documented contract; the engine records the transition and audits it, and
tracking the effect-outcome tag mechanically is the named follow-up (it
needs acknowledgement state the engine does not yet keep).

- `KILLED` is terminal and not resumable; `PAUSED` resumes from the next
  unstarted step; `FAILED` instances may be resumed via the retry
  endpoint (re-drives from the first non-terminal step).
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
| `webhook` | exactly what the caller sends | **none — caller owns idempotency** | lost (no queue) |
| `schedule` | per tick while the process runs | n/a | missed ticks are NOT replayed |
| `manual` / API run | once per request | none | n/a |

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
  (changed 2026-07-31 per external review §3: a broken condition must
  never silently bypass a validation/approval/containment gate). An edge
  whose target is genuinely optional may opt into the old behavior with
  `on_error: inactive`. A step whose incoming edges are all
  resolved-inactive becomes `SKIPPED`; skip propagates downstream unless
  another active path reaches the step.
- A step failure (after retries) fails the instance and **cancels
  in-flight sibling branches**; their external effects up to the
  cancellation point are NOT undone (see §6).
- Step outputs are immutable once written. Reruns happen only via new
  instances (retry re-executes non-terminal steps; completed steps'
  outputs are never recomputed in place).

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

- Definitions are replaced whole (save/import); an in-flight instance
  continues on the definition object it started with. **In-flight
  migration is not provided.**
- Deleting a definition **currently cascades its run history** and 409s
  while any instance is non-terminal. **The reviewer is right this
  conflicts with the audit/forensic posture** (§8): the intended model is
  disable/archive/soft-delete with run history + audit + immutable
  definition versions retained per policy, and hard deletion a separate,
  recorded process. Changing this is tracked as G21 (a behavior change,
  not a doc edit); until then, treat delete as destructive and prefer
  leaving definitions in place.
- Fork (`POST .../fork`): new instance with topological-ancestor outputs
  copied as COMPLETED, driven from the chosen step under the CURRENT
  definition + memory state. **The reviewer is right this is dangerous
  when the definition has since changed** (§7): old ancestor output
  schema meeting new downstream expectations. The intended split, one a
  named follow-up (G21): `replay_fork` (source definition + source memory
  snapshot — the safe default) vs `migration_fork` (current definition,
  with explicit compatibility validation). Today's single behavior is the
  migration variant WITHOUT the validation — safe on an unchanged
  definition, which is the only case exercised. The `workflow_forked`
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
