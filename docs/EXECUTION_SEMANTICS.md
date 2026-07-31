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
StepExecution:     PENDING → RUNNING → {COMPLETED | FAILED | SKIPPED}
```

- `KILLED` is terminal and not resumable; `PAUSED` resumes from the next
  unstarted step; `FAILED` instances may be resumed via the retry
  endpoint (re-drives from the first non-terminal step).
- Every state transition and every agent tool call appends an audit
  entry; step outputs persist when the step completes.

## 2. Trigger delivery guarantees (per trigger type)

**The platform-wide guarantee is at-least-once with bounded dedupe.**
Exactly-once is **not provided** anywhere.

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
- Edge conditions are simpleeval expressions over `trigger`/`steps`
  context. An evaluation error makes the edge **inactive** (logged) —
  fail-toward-not-running-the-target. A step whose incoming edges are
  all resolved-inactive becomes `SKIPPED`; skip propagates downstream
  unless another active path reaches the step.
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
- **Not provided:** automatic error classification (retryable vs
  permanent), acknowledgement-aware retry, engine-enforced
  effect-gating of retries. Named follow-up if a second mutating
  connector arrives.

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
  in-flight Bedrock/tool calls are not interrupted mid-call.

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
- Trigger-side: the email cursor persists (G9), so mail arriving during
  downtime is delivered late, not lost.

## 8. Versioning, mutation, lineage

- Definitions are replaced whole (save/import); an in-flight instance
  continues on the definition object it started with. **In-flight
  migration is not provided.**
- Deleting a definition cascades its run history and 409s while any
  instance is non-terminal. Bundled examples re-seed on restart.
- Fork (`POST .../fork`): new instance with topological-ancestor outputs
  copied as COMPLETED, driven from the chosen step under the CURRENT
  definition + memory state; lineage recorded via the `workflow_forked`
  audit entry referencing the source instance.
- **Not provided:** dynamic graph changes to running instances,
  approval/escalation expiry (escalations remain open until resolved).

## 9. Quick reference — what is explicitly NOT provided

Exactly-once delivery · cross-step transactions · compensation ·
engine-enforced retry/effect gating · mid-step budget enforcement ·
in-flight definition migration · schedule catch-up · webhook queueing ·
approval expiry. Each is either an authoring rule (§4), an operator
procedure (§6), or a trigger-gated backlog item.
