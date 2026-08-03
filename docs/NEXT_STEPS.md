# Next steps

## Where things stand

*(Refreshed 2026-07-31.)* The 2026-07 build arc landed: local auth +
tenant-scoped roles (S1–S3), the acting email triage with two-axis
classification live on the full mailbox, codification (G13) live with 7
senders, the IA rework (one catalog, two renderings), the veracium
memory transparency surface, and the monitoring loop actually running in
production (five checks incl. the new `alert_stale_trigger`). Current
open work is the G15/G16 follow-ups below plus the two supervised
validation windows (two-axis part 2; codify §9), both accumulating on
live mail.

The manual-testing backlog that originally motivated this doc is closed.
Today you can: `docker compose up -d postgres`, start the backend with
`WORKFLOW_DEFINITIONS_DIR=../examples`, start the frontend, then drop a
PDF / click Run / curl a webhook / wait for a schedule — each fires
end-to-end with live audit events streaming into the dashboard. Role
switching, eval scores, memory-hash visibility, and Postgres-backed
persistence all work without leaving the browser.

This doc now tracks: (1) one explicitly-deferred item, (2) gaps surfaced
*during* the manual-testing push that didn't make the original backlog,
and (3) a "Landed" appendix so you can find where any completed item
lives.

For larger forward-looking work (knowledge ingestion, LLM-driven
orchestrator, generative UI, OAuth connectors), see `CLAUDE.md`'s
"What NOT to do" section and the post-Phase-2 re-evaluation checkpoint.

---

## Active

### P2.3 — Apply Terraform once and verify

`infra/` is `terraform validate`-clean but the deployed stack is
hypothetical until someone runs `apply`. Recommended posture for the
solo-dev case (worked out in earlier conversation): ALB security group
restricted to your public IP, AUTH_MODE=dev, log retention 7 days,
`desired_count=0` between sessions. Idle cost ~$53/month while applied.

Acceptance:
- `terraform apply` succeeds end-to-end.
- `curl http://<alb>/api/health` returns 200 from your IP, times out
  elsewhere.
- Dashboard at `<alb>/` loads and can fire a workflow.
- `terraform destroy` returns the account to clean state.

Effort: **M**. Coding is done; the risk is operational + the standing
cost. Deferred until there's an actual reason to deploy (a demo, a
customer pilot, a workflow that needs to run 24×7 against scheduled
triggers).

---

## Gaps surfaced during P0–P2 that weren't in the original backlog

### G1 — PDF classifier Bedrock recordings (path portability)

P2.2 landed a recording for `webhook_echo` (request hash depends only on
the trigger payload — portable). The original target was the PDF
classifier, but its request hash incorporates `trigger.file_path` and
the full extracted text — both vary by machine. Two ways forward, both
deferred:

1. Normalize file paths to a token before hashing (intrusive change to
   `BedrockClient`).
2. Pin a fixed test-fixture path convention (`/var/workflow-tests/...`)
   and document it.

Worth doing when someone wants to demo the PDF flow offline. Until then,
the existing `test_pdf_classifier_workflow.py` covers the flow with
`FakeBedrock`. Effort: **S** for either fix.

### G2 — Webhook HMAC verification (production hardening) — **Done**

Shipped exactly per the acceptance criteria: `trigger.config.secret_name`
names a `SecretStore` key; when set, `POST /api/triggers/webhook/{id}`
requires a GitHub-style `X-Hub-Signature-256: sha256=<hex>` over the raw
body (timing-safe compare), 401 on missing/bad signature, and **503
fail-closed** when the named secret can't be loaded (never falls open).
Signature is checked before the body is parsed. Triggers without
`secret_name` keep the unsigned local-dev path. 6 tests in
`backend/tests/test_webhook_hmac.py`.

### G3 — Cost dashboard view — **Done**

Landed: new lazy-loaded route at `/cost` (`frontend/src/app/components/cost-dashboard/`). Header nav gains a "Cost" link next to Instances + Workflows. Three side-by-side tables — by workflow / by model / by day — backed by `ApiService.costByWorkflow / costByModel / costByDay`. Each fetches in parallel and settles independently, so a single backend error doesn't blank out the other two. An aggregate totals row sums cost + tokens + step count across the selected window.

Filter is a single ngModel-bound dropdown: "All time" (no `since` param) / Last 24 hours / Last 7 days / Last 30 days (each translates to an ISO `since`).

No charts — tables match the existing UI's visual language. The by-day table is the obvious chart target if trend visualization becomes useful later.

Tests: +11 frontend tests (4 ApiService URL-construction tests covering the three endpoints + the `since` pass-through, 7 CostDashboardComponent tests covering totals computation, window→since translation across all 4 windows, partial-error isolation, ngOnInit dispatch). 68 frontend tests total (was 57). AOT build clean: 6.11 kB lazy chunk. Commit `e748b81`.

Aside: writing the component spec surfaced that the existing `Object.create(prototype)` test pattern doesn't work for components with class-field `signal()` initializers — those skip. The spec's `makeComponent` helper documents the workaround (manually re-wire the signal + computed fields) so future component specs have a template.

### G4 — Live events on the instances *list* page — **Done**

Landed 2026-07-19 in the UI refresh (commit `8002b9c`): the React
instances list subscribes to the same `useEvents` stream as the detail
page and refreshes immediately on `workflow_started` /
`workflow_completed` instead of waiting up to 5s for the poll.

### G5 — Memory-hash diff view — **Done**

Landed 2026-07-19: a "Compare with…" picker on the instance-detail
actions row (siblings of the same workflow) navigates to
`/compare/:a/:b` — a side-by-side per-step table showing each run's
state, seven-bucket category badge, memory hash (rubric version),
recall (edges·episodes consulted), usage, and a verdict-summary signal,
with "rubric changed" / "verdict changed" flags and row highlighting on
differences. Cross-workflow comparisons warn; a same-rubric banner
notes that differences must come from inputs or recalled memory. Zero
backend change (two `getInstance` calls + the existing sibling list).
Pure helpers in `lib/compare.ts` with unit tests (alignment across
missing steps, no-diff-when-facet-absent).

### G6 — Auto-load `agent_memory.md` adjacent to a workflow YAML — **Done**

Landed: new `MemoryManager.write_raw(agent_id, content)` (overwrites the agent's memory file) + `seed_memory_from_workflow_dir(definition, yaml_path, memory)` helper in `workflow_platform.orchestrator`. Called from both `TriggerOrchestrator._register_one` and `tools/fire.py` after `definitions.save`. `main.py` auto-builds a `MemoryManager` from `WORKFLOW_PLATFORM_MEMORY_DIR` (default `./.memory`) and passes it to the auto-built engine.

Convention: one `agent_memory.md` per workflow, applied verbatim to every agentic step. Overwrite-on-load (static rubrics today; merge-with-observations is a future refinement when workloads accumulate runtime memory).

Verified: dropped the inlined `system_prompt` block from `examples/github_pr_triage/workflow.yaml`, re-ran a 10-PR batch — `memory_hash = sha256:bdb6ab7c96ace4e8` on every run, all concerns catalog-compliant, behavior matches v4. The rubric in `agent_memory.md` is now the single source of truth. 4 new tests cover the helper + end-to-end auto-load.

### G7 — Surface input / output token split per agent step — **Done**

Landed: `frontend/src/app/services/usage.ts` (pure helper, 13 Vitest tests in `usage.spec.ts`). The instance-detail Steps table gained a "Usage" column rendering `in: 1234 · out: 156 · $0.000789` for each agentic step; deterministic steps show `—`. Hover shows model + total tokens (and iteration count when > 1). When the output-cost share exceeds 50% (output_tokens × 5 > input_tokens at Haiku 4.5 pricing), the cell colors `var(--warn)` and bolds — visual cue that the agent is being unusually chatty and the prompt is worth trimming. No backend change; data was already in `step_executions.output.usage`.

### G8 — Fork-from-step affordance — **Done**

Surfaced as R1 in `docs/AGENT_MEMORY_RESEARCH_NOTES.md` (ActiveGraph paper's "cheap forking that branches a run at any event"). Landed across backend + frontend.

**Backend:** new `WorkflowEngine.fork(definition, source_instance_id, from_step_id)` creates a new instance with the source's topological-ancestor step outputs preserved as `COMPLETED` step executions, then drives the engine from `from_step_id` onward — picking up current `agent_memory.md` state. Added `_ancestors(definition, target_id)` helper and an `already_done` parameter on `_dispatch_loop` (incidentally tightening resume so first steps aren't accidentally re-run). New `POST /api/workflow-instances/{id}/fork` endpoint (Admin/Operator) with body `{"from_step_id": "<step>"}`. New `workflow_forked` audit action.

**Frontend:** `ApiService.forkInstance(id, fromStepId)`. "Fork" column on the instance-detail Steps table with a button per step; on success the dashboard navigates straight to the new instance.

**Tests:** 8 new backend unit tests in `test_engine_fork.py` (root/middle/leaf fork semantics, audit-entry shape, agent-memory pick-up via FakeBedrock, error cases) + 4 new API tests in `test_lifecycle_endpoints.py` + 1 new Vitest test for `forkInstance`. Resume's existing tests still pass with the dispatch-loop change.

### G9 — Persist the email-trigger poll cursor — **landed 2026-07-14**

Implemented as scoped: new `TriggerCursorRepo` (`trigger_cursors` table,
Alembic `0002`; in-memory + Postgres upsert impls), `GmailPollTrigger`
persists **cursor + the seen-id ring** after each dispatching poll and
initializes from the store on start (falling back to "now" on true first
start). The seen-id ring turned out to be load-bearing, not optional:
Gmail's `after:` is second-granular and inclusive, so the last processed
message always re-matches after a restart, and the in-memory dedupe dies
with the process — persisting both makes restarts loss-free *and*
duplicate-free. Keyed `email:<workflow_id>:<account>` so re-pointing a
workflow at another mailbox starts fresh. Store failures degrade to the
old process-local behavior (logged, never blocks polling). Tests:
`test_trigger_cursor.py` (acceptance criteria) + a Postgres upsert
round-trip. `FilesystemTrigger` still process-local — extend if a real
miss shows up there.

### G11 — Two-axis triage: separate category from attention — **Done 2026-07-26**

Built + cut over same day as the design (`docs/EMAIL_TRIAGE_TWO_AXIS_PLAN.md`
holds the authoritative status): 5-bucket message category + multi-valued
attention, tri-state reply lifecycle, re-minimized apply path. Original
notes kept below for the evidence trail.

Surfaced during the 2026-07-19 ground-truth labeling session, twice in
one pass. The seven-bucket taxonomy still mixes two orthogonal axes —
what mail IS (source: personal / notification / newsletter / promotion /
spam) and what it DEMANDS (attention). Interim fix in the rubric:
explicit precedence (urgent > awaiting-reply > source categories).
Collision evidence so far:

1. **personal × urgent** — a family member's identity-spoofing warning
   (dews7@me.com). Precedence resolves it (urgent), losing the
   personal-sender fact to the summary.
2. **notification × review** — a PayPal receipt ("Patreon: $5.34").
   Precedence CANNOT resolve this one: the mail states no demand, and
   its importance (routine vs account-compromise evidence) depends on
   user context the classifier doesn't have. This motivates an
   `attention: review` value — money-movement / security-adjacent
   notifications are "routine if expected; worth a glance because only
   the user knows."

Sketch: split the agent output into `category` (the five source values)
+ `attention: urgent | reply-expected | review | none`. Touches: rubric,
record_email_triage, review tool (second question per email), judge,
per-axis accuracy metrics, one re-classification pass
(tools/reclassify_triage.py makes this cheap). Longer-term, learned
memory closes the `review` gap properly: "user has a recurring ~$5
Patreon charge" makes the receipt confidently routine — importance
becomes a join between the email and what the system knows about the
user.

Trigger to start: the finished labeled corpus shows axis collisions
beyond a handful (tally them at session end), or the label-applying
variant goes live (actions need the attention axis more than
classification does). Effort: **M**.

**Session-end tally (2026-07-19, 154 messages):** 4 collisions — 2
precedence-resolved (personal×urgent, personal×awaiting-reply) and 2
notification×review (PayPal + ManifestRx receipts), which precedence
structurally cannot resolve. The review class is the real pull; call
is Quentin's on when to spend the M.

4. **awaiting-reply is a STATE, not a category** (2026-07-24, live:
   a baseball-game mail from a personal correspondent, labeled
   `wf/awaiting-reply` — correctly — but the user had already replied).
   Source categories are stable properties; the attention axis is
   time-varying and needs LIFECYCLE: applied when the demand is
   detected, retired when it's satisfied (reply sent). Sharpest
   implication for the split: category labels are permanent and safely
   codifiable (G13); attention labels need check-then-label at apply
   time (thread has no newer sent message) and a retirement path —
   label *removal* stays operator/deterministic-side, never the agent
   (the add-only fence holds).

### G12 — Ask-the-user: clarification elicitation for classification

Quentin, thinking aloud during the 2026-07-19 labeling session: the
system should be able to ASK the user for the one context fact that
would change its answer, rather than only passively accumulating
context. Examples: Indeed job alerts are routine notifications — unless
the user is out of work; the Kate Webb event thread classifies
differently if the system knows the user plans to attend.

Almost all infrastructure exists: the escalations plumbing
(`RequestHumanReviewTool` + resolve API + dashboard panel) is the
asking channel; answers land in veracium as USER-authored facts
(highest trust, assertable — exactly right for self-reported context),
with volatility classes fitting naturally (employment ≈ durable,
event attendance ≈ transient expiring at the event date); veracium V3
proactive recall is the sibling capability; PR #9 outcome tracking is
how the system learns which question types actually pay for
themselves.

Two make-or-break constraints:
1. **Question budget = expected value of information.** Ask only when
   the answer is durable/reusable AND classification is sensitive to
   it. Otherwise it's a nagging machine.
2. **Questions are an injection surface.** Question generation derived
   from third-party mail is tainted content; a hostile email must not
   be able to induce a manipulative question or smuggle framing into
   the USER-authored answer fact. Same provenance discipline as
   everything else.

Trigger to start: after the two-axis split (G11) — the `attention`
axis is where elicited context pays off — and after outcome tracking
ships (0.3.x), so question value is measurable. Effort: **M-L**.

### G13 — Codification loop, slice 1: evidence-driven sender pre-filter — **Done 2026-07-30**

Built + cut over (`docs/EMAIL_TRIAGE_CODIFY_PLAN.md` holds the
authoritative status): eligibility engine + CLI, DKIM/DMARC auth gate,
diamond workflow with an attention-only classifier, runtime disable
overlay, 7 senders live at 1-in-5 sampling. Follow-ups → G15/G16 below.
Original notes kept for the evidence trail.

From the 2026-07-24 Pega research (`docs/product/COMPETITIVE_LANDSCAPE.md`
Pega profile): the design-time/runtime split is a **dial per step**, and
the codification loop is what moves it. First concrete slice: a
deterministic pre-classify for email triage — senders whose veracium
outcome history is unanimous (e.g. ≥5 recalled-and-confirmed uses, zero
corrections) classify without the LLM; everyone else keeps runtime
judgment. **Demotion is the differentiator vs Pega's static maps**: a
codified sender that accrues a `corrected` outcome reverts to the LLM
automatically (drift re-opens the map). All substrate exists — the V4
edge counters (`times_used`, confirmed/corrected) are the evidence
ledger, deterministic steps are free, and this absorbs the
"deterministic connector actions" deferral in `EMAIL_TRIAGE_ACT_PLAN`
§10. Concept home: `docs/LEARNING.md` (execution learning).

**Codification criterion, sharpened (2026-07-25, from Quentin's filter
setup):** codify only where the SENDER determines the category — and
outcome unanimity over many messages is exactly the statistical test
for that (content variation never changed the verdict). Quentin's
Gmail filters are a hand-built version of the same layer, all
sender-based; what they couldn't filter (e.g. PayPal — receipt vs
security alert from one address) is content-dependent and stays with
runtime judgment forever. Corollary: the attention axis (G11) is
content/state-dependent by nature and is NEVER codifiable. His filter
export is candidate day-one seed evidence (pre-validated
sender→disposition rules) — import rather than re-learn.

Trigger to start: the ACT_PLAN §8 window closed AND an evidence
threshold met (≈5 senders with ≥5 unanimous confirmed outcomes; met as
of 2026-07-24 — 8 qualifying senders, zero corrections store-wide) —
or mail volume making classify spend material. Note the window's
effective volume is INBOX-residue only (~9/day; the filters pre-drain
sender-determined mail), so weigh evidence quality over raw count when
closing. Effort: **S–M**.

### G14 — MCP exposure of workflows (external-agent interop)

Expose workflow definitions as discoverable MCP tools so external
agents can list and invoke platform workflows — governed execution
(capabilities, audit, budgets) underneath an open interop surface. Pega
shipped exactly this in June 2026; the `run`/`run-batch` APIs already
carry the substance; veracium's MCP Registry listing is the in-house
playbook. Hard prerequisite: **API keys** (`AUTH_PLAN` §7 deferral) —
unattended external callers can't ride cookie sessions, so the triggers
chain.

Trigger to start: first external-agent consumer ask, or adopting the
`docs/product/` GTM posture (an MCP listing becomes a distribution
channel). Until then this is speculative horizontal surface — exactly
what the operating principles defer. Effort: **M** (server shim + API
keys prerequisite).

### G15 — Codify hardening follow-ups (deferred at build, triggers named)

Three deviations recorded honestly in `EMAIL_TRIAGE_CODIFY_PLAN.md`'s
status paragraph, each with a trigger:

- **Corrections era-scoping** — the domain fence currently counts
  7-bucket-era corrections (vocabulary collisions, not
  sender-unpredictability), keeping nytimes/wsj-class senders
  disqualified. Deliberately strict (over-disqualifies, never under).
  Trigger: the operator wanting a fenced sender codified, or the fence
  visibly costing meaningful spend.
- **Runtime rubric-hash verification** — the precheck validates schema
  version but cannot see the engine's memory hash; a rubric edit relies
  on regeneration discipline. Trigger: exposing the memory hash to
  deterministic functions for any other reason, or a stale-rule incident.
- **`skip_if` on ObservationSpec** — the fail-closed conditional
  observation surface designed in the codify plan; the decision-source
  query contract made it non-blocking. Trigger: any consumer needing
  per-run observation suppression.

### G21 — Execution-semantics safety follow-ups (external review rounds 2–3)

Landed in code across the review rounds: edge-condition errors fail
closed (`on_error: inactive` opt-out); production webhooks must be
signed; **definition delete requires `force=true` when run history
exists** (round 3). The rest are named follow-ups, SPLIT by severity per
the round-3 request (so "G21 done" can't mean one small UI change while a
destructive gap remains):

**Higher severity (before external/multi-author use):**
- **Persist immutable definition identity (content hash) on every
  instance** — prerequisite for correct crash recovery, replay,
  forensics, AND the cross-version fork guard. Currently instances
  load the *current* definition fresh; a mid-flight edit corrupts
  recovery. This unblocks the next two.
- **Reject cross-version forks** — once instances carry a def hash,
  refuse a fork when current ≠ source (the safe subset until
  replay_fork/migration_fork split exists).
- **Static retry/effect validation** — `validate_definition` gates
  `retries > 0` on `Tool.effect` (read-only auto-pass; mutating needs
  declared idempotency; unknown rejects). Cheapest high-value item.
- **`on_error: inactive` safety validation** — reject/warn when the
  swallowed-error edge guards a mutating/approval/security target.

**Lower severity / operability:**
- **Soft-delete + retention** — replace the `force` guard with real
  disable/archive + retained immutable versions.
- **Concurrency keys** (`concurrency: {key, mode}`), **worker leases**
  (multi-process recovery arbitration), **request-vs-effected state
  surfacing** (`pause_requested`/`kill_requested` in API/UI),
  **persist-before-ack inbound queue** + client idempotency key,
  **shared-txn step-commit** + post-crash consistency check,
  **machine-recorded effect-outcome** tags.

Original round-2 list preserved below for lineage:
- **Retry static validation** — `validate_definition` gates `retries > 0`
  on `Tool.effect` (read-only auto-pass; mutating needs declared
  idempotency; unknown rejects). Achievable now without ack-aware
  execution. **Highest priority** — it makes the retry invariant
  mechanical, not just documented.
- **Soft-delete + retention** — stop cascading run history on definition
  delete; disable/archive with audit + immutable versions retained;
  hard-delete a separate recorded process. Conflicts with the audit
  posture until fixed.
- **Fork version-binding** — split `replay_fork` (source def + memory
  snapshot, safe default) vs `migration_fork` (current def + compat
  validation); audit source/dest definition hashes.
- **Concurrency keys** — `concurrency: {key, mode}` for workflows that
  can target the same external object; until then the unsupported-unless-
  idempotent contract stands.
- **Worker leases** — prerequisite for multi-process/horizontal scale;
  today recovery is single-process restart only.
- **State surfacing** — `pause_requested`/`kill_requested` vs terminal in
  the API/UI so operators aren't misled about in-flight calls.

### G22 — Tenant-isolation enforcement inventory + hardening (external review rounds 2-3)

Landed this pass (the cheap, high-value test/permission fixes): the WS
isolation test rewritten forbidden-first + a direct `event_deliverable`
primitive test (the prior test could pass on a fully-unfiltered
implementation); Administrator-bypass test asserts full audit detail +
same-org negative control; bulk-delete postconditions; paired positive
controls; workflow delete tightened to Org-Admin+. Remaining structural
work, each a release gate before the first external org:
- Generated route-to-scope + route-to-test inventory; CI fails on an
  unclassified route (the reviewer's "most important structural fix").
- Fail-closed repository methods (retire the optional `org_id=` default —
  the likeliest future isolation defect).
- Postgres-backed isolation tests for the §4b join surfaces
  (audit/steps/cost) — the in-memory suite can't catch a missing SQL join.
- Migration-execution test (run `0005` against real rows, not just prove
  the map is total).
- User-management audit entries carry an `affected org_id` (so Org Admins
  see their own org's user events).
- Connector/secret per-org scoping as a hard prerequisite for org #2.
- `/metrics` protected by more than deployment convention.

### G22-orig — Tenant-isolation enforcement inventory (external review round 2)

A generated route-to-scope inventory proving every surface participates
in `OrgScope` (not reviewer memory), plus Postgres row-level security as
defense-in-depth. Trigger: first external organization — same gate as the
secrets-manager and host-admin-trust items in `THREAT_MODEL.md` §8.

### G19 — Workflow execution-semantics contract — **Done 2026-07-31** (`docs/EXECUTION_SEMANTICS.md`)

A formal document specifying what the engine guarantees: trigger
delivery (at-least-once + seen-id dedupe today), idempotency
expectations per Tool.effect class, retry classification (the reviewer
is right that blanket retry is unsafe for non-idempotent sends —
today's default is retries=0 and the only retrying step is the
enum-gated label apply), partial completion, cancellation, resume,
fork lineage. Most behavior exists and is tested; the CONTRACT is
unwritten. Trigger: before the next external review round, or any
second engineer writing a mutating workflow.

### G20 — Consolidated trust-boundary / threat-model document — **Done 2026-07-31** (`docs/THREAT_MODEL.md`)

One picture unifying what exists piecewise (capability intersection,
tenant isolation, mail-surface injection defenses, memory quarantine,
secrets handling, WS auth). Trigger: same as G19 — it is the
reviewer's requested bundle item 3.

### G24 — Verification-index self-validator — **Done 2026-08-01** (`backend/tools/check_verification_index.py`)

External review finding 11: a CI-gated checker that fails if the code-review
handoff index (`VERIFICATION_INDEX.md`) cites a file or test that no longer
exists, or marks a CONTRADICTED row as verified. Line numbers deliberately
unchecked (they drift — the reviewer's own point); symbol misses warn, not
fail. Wired as a CI step. Guards against the recurring 'doc edits drift from
code' pattern that produced three rounds of review findings.

### G23 — Triage acting/codify hardening → platform controls (external review 2026-07-31)

The three-plan review's lesson: turn production-discovered conventions
into platform-level controls. Two landed this pass (attention-set
preservation in the record; lock-serialized disable overlay). Remaining,
by theme:

**Acting-path safety (high priority — "the trigger has effectively fired"):**
- **Tool-parameter pinning — DONE 2026-08-01.** `pin_params` on
  AgenticStep maps a tool-param name to a context path the engine resolves
  and forces on every tool call; the model may request the call but pinned
  params are overwritten before dispatch, and an override attempt audits
  `tool_param_override_blocked`. Live on the apply step
  (`message_id`←trigger, `labels`←record). Steered-agent e2e proves it
  labels the real message with the validated label only.
- **Apply postcondition — DONE 2026-08-01.** `require_tool_call:
  {name, min_success}` on AgenticStep; the executor FAILS the step (audit
  `step_postcondition_failed`) if it finishes without the required
  successful tool calls — cost still attributed, tool-call audits
  preserved. Live on the apply step. Pairs with tool-param pinning: pinning
  bounds WHAT the agent can mutate, the postcondition bounds THAT it must
  actually do it.
- **Decouple verdict observation from Gmail apply success** — record the
  adjudicated classification whether or not Gmail accepts the label; a
  separate outcome event records the apply failure.

**Codify maturity (before treating as a general default, not just supervised):**
- Runtime **rubric-hash enforcement** (functions can't read the engine
  memory hash today — schema version only).
- **`skip_if`** engine surface (fail-closed learned-memory writes) as the
  designed belt-and-suspenders over the query-contract guard.
- **Per-sender** sampling floors (aggregate 3/n doesn't bound a
  low-volume sender).
- **Paired attention shadow eval** on sampled messages (full vs
  attention-only vs human, same messages) — spot checks can't establish
  parity across a different prompt with no recall.
- One **resolved policy fingerprint** (schema + rubric + auth-policy +
  normalization + vocab + sampling versions) so a normalization/auth
  change can't leave an artifact "compatible" while its keys mean
  something different.
- Multi-process transactional overlay storage (the lock covers
  single-process only).

**Process:** record threshold waivers as (original gate / evidence /
deviation / owner / residual risk / new prospective gate), never as a
pass; a population-scope expansion (like the same-day inbox→all-mail
widening) is an independently-reviewed amendment with volume/spend/
backfill/rollback stated up front.

### G25 — Code-level review findings (external code review, 2026-08-01)

The reviewer executed the code against the contracts and found 6 items.
**Resolved this pass** (against HEAD, not the frozen 1d2ef25 baseline):
- **F1 (HIGH)** unexpected branch exceptions: siblings cancel AND the
  originating step now persists FAILED (was stranded RUNNING — round-2
  follow-up). Whole dispatch-loop body guarded; `_run_step_once` catches
  unexpected `Exception` → step FAILED + `unexpected: true` audit → re-raise
  (not CancelledError, so siblings stay CANCELLED). Test asserts a FAILED,
  b CANCELLED. Closed.
- **F2 (HIGH)** tool pins **fail closed** — an unresolved pin path FAILS
  the step before dispatch + audits `tool_pin_unresolved`. Pinned.
- **F3 (HIGH-before-ext-org)** raw tool payloads projected across ALL
  read surfaces via one shared `redact_tool_data` (round-2 follow-up — the
  first fix only covered `tool_call` entries; raw data also flowed through
  `step_completed` audit, `StepExecution.output`, `instance.context`,
  explain, WS events) AND the model-ECHOED output_text — the redactor
  withholds output_text when a step used a tool, since the model can
  paraphrase a tool secret into free text (F3 round 3). End-to-end test:
  the model echoes both an input and an output secret; Viewer+User recover
  nothing from the 5 HTTP surfaces (incl. the LIST endpoint, which now
  returns a summary omitting context + trigger_payload entirely) + a
  dedicated WS delivery test; admin raw. Content-hash oracle dropped from
  the projection (byte length kept). Closed for the read surfaces. Internal
  design review of the gate work (below) also surfaced a **live** below-grant
  leak the shared redactor missed — the instance DETAIL endpoint returned the
  raw `trigger_payload` (and the `recall` correspondent history) because
  `redact_tool_data` no-ops on non-tool-call keys; now closed with per-asset
  projections (`safe_trigger_payload` keeps routing only; recall withheld) +
  two tests. *Gate (design DONE, build trigger-gated):* storage-level
  separation, a raw-trace privilege DISTINCT from ordinary admin, and audited
  raw access are now specified in `docs/TRACE_GOVERNANCE_PLAN.md` (TG1–TG3d,
  internal review + external review both folded). External review disposition:
  **adopt the architecture, revise before build** — two blocking findings
  folded: (1) a same-DB vault is NOT a boundary against the host/DB operator,
  so the plan now commits to two explicit contracts — Contract A
  (application-level governance, host trusted) for TG1–TG3c, and Contract B
  (separate credentials / envelope encryption, operator-inaccessible keys) at
  **TG3d, which is the true external-org gate**; (2) execution-critical raw
  writes are now durable-or-fail, not best-effort, so resume/fork stay
  recoverable. Grant is a scoped record (not a bare boolean), trigger
  projection is a typed default-deny registry, plus migration/deletion/
  retention/resource-limit contracts. **External review v2 folded:
  architecture accepted; TG1/TG2 approvable for implementation planning,
  TG3a–d implementation-blocked.** Four more blocking findings folded:
  (1) Contract B split into B1 (DB-operator resistance via envelope
  encryption) and B2 (host-operator resistance — needs external vault /
  attested execution; KMS-to-engine-identity alone doesn't stop a host root);
  the gate must NAME which it requires. (2) The Contract-B mechanism is now a
  schema-shaping decision made BEFORE TG3a (abstract vault repo + opaque IDs
  + reserve→persist→commit protocol), not an afterthought. (3) TG3a is a DARK
  DUAL-WRITE — it must not flip the operational store to safe-only before
  TG3b's rehydration lands, or instances created between them resume/fork
  wrong. (4) Output sensitivity is by TAINT (any raw-influenced free-form
  output/error is raw), not just tool-bearing steps; v1 vaults ALL free-form
  output + errors. Plus: grant-scope constrains admin bypass (org-A grant
  doesn't travel to org B); frozen execution identity (one immutable row per
  attempt) + attempt-isolation; exact disagreement predicate w/ projector
  versioning + AEAD binding; `workflow_instances.context` + errors + cost
  tags added to the zero-raw inventory; retention coupled to resume/fork. 17
  criteria. **External review v3 folded: architecture APPROVED.** The
  reviewer reduced the remainder to FIVE frozen contracts, all now written
  into the plan: (1) safe structured output = a registered versioned
  projection, not schema validation (`{"summary":"...SSN..."}` validates but
  is raw) — persist only closed enums/booleans/bounded-numerics/opaque-IDs/
  status; vault all free-form + errors; (2) the durable execution snapshot is
  defined by the actual context dependency graph, NOT by raw kind (a tool
  result is execution-critical when pinned/branched-on, diagnostic otherwise);
  (3) a vault crash-recovery state machine (RESERVED→STORED→REFERENCED→
  COMMITTED, deterministic idempotency key, reconciler checks references
  before delete, a REFERENCED object is promoted after a crash never
  orphan-deleted); (4) platform-wide grant authorization = two distinct
  Administrators, recipient excluded, mandatory expiry, unavailable
  single-admin; (5) an append-only two-event audit model (attempted-before-
  fetch + completion-with-outcome, explicit partial, per-delivered-WS-event
  correlation id). Plus schema-version fields on both rows, the immutable-
  attempt execution model destined for EXECUTION_SEMANTICS, and persisted
  queryable fork lineage for erasure. Criteria 17→24. **TG1 (freeze 4) and
  TG2 (freeze 5) are now authorizable; TG3a–d await freezes 1/2/3 + the
  attempt model.** Build stays trigger-gated; no external org until the gate's
  required contract (up to TG3d) is in effect. **B1-vs-B2 decided
  (2026-08-01):** B2 (operator-cannot-decrypt) is the destination contract,
  delivered B1-first (per-org envelope encryption) — the B1→B2 delta is the
  decrypt-runtime boundary, not the data model, so it never re-migrates the
  vault. Load-bearing invariant: per-org (BYOK-ready) keys from the first
  encrypted write. TG3d split into TG3d-1 (B1, shippable gate for a
  B1-accepting customer) + TG3d-2 (B2, pulled forward when a contract
  requires it). **External review v4 folded — architecture FROZEN without
  qualification; a narrow v5 folds six representability/semantics fixes:**
  (1) the grant becomes a STATE MACHINE (pending/active/rejected/revoked/
  expired + requested_by/approved_by/external_approval_ref) — a flat record
  couldn't represent a two-person platform-wide approval; uniqueness applies
  to ACTIVE grants only, activation is atomic compare-and-set;
  (2) the vault idempotency key was colliding — `hash(instance,attempt,kind)`
  collides for two steps both on attempt 1, fixed to
  `hash(org,instance,step_attempt_id,kind)` + a separate instance-level
  space; (3) the execution dependency graph is now PERSISTED
  (`raw_trace_dependencies`) at execution time, not inferred from serialized
  context; the recovery snapshot = the required_for_recovery set;
  (4) the audit boundary is RELEASE not receipt (append-only can't prove
  client receipt) — `raw_trace_release_decided` commits before any raw byte
  crosses the boundary; delivery-observed is telemetry only; (5) internal
  engine vault reads (resume/fork/retry/migration/erasure) get their own
  `raw_trace_system_accessed` records under the narrow runtime identity;
  (6) cross-org fork PROHIBITED first build. Plus: closed reason_code (grant
  metadata can't leak raw), explicit snapshot-expiry state
  (recovery_state/resume_available_until), and reconciliation concurrency
  (compare-and-set + lease). Criteria 24→31. **External review v6 folded —
  TG2's human release path is design-APPROVED; six implementable-semantics
  fixes:** (1) grant activation picks EXACTLY ONE `approval_mode`
  (dual_administrator | tenant_authorized) with per-mode required fields —
  v5 left both paths ambiguous; `cancelled` added to the enum; (2)
  `reason_note` REMOVED (a bounded string still holds a pasted email body,
  violating the safe-output contract) → closed reason_code + opaque
  ticket_ref; (3) internal engine vault access is now audit-BEFORE-decrypt +
  fail-closed (`raw_trace_system_access_attempted` commits before any
  fetch/decrypt; failure pauses/fails the op) — v5 only recorded it after;
  (4) the external-vault reconciler gets a real cross-system fencing/intent
  protocol (a delayed writer can't commit a reference to an aborted object;
  referenced-but-not-committed reads lazily promote, never read
  missing/corrupt); (5) the dependency manifest is FINALIZED
  (snapshot_generation/hash, append-only, fork binds a finalized generation,
  retention only on finalized, erasure atomically flips recovery_state); (6)
  grant expiry is a durable DB transition (an active-but-past-expiry row
  can't block a replacement — Postgres can't use now() in a partial index).
  Criteria 31→36. TG1, TG2-system, TG3 await re-review; TG2-human approved.
  **Design SETTLED as of v6 (terminal revision) — the plan is not reopened
  for further speculative review rounds; remaining precision is pinned by
  tests at build time (the §8 criteria are the contract).**
  **TG1 BUILT (2026-08-01):** the raw-trace privilege is a scoped, audited,
  revocable grant distinct from administration (Contract A) —
  `raw_trace_grants` state machine + Alembic 0006, `RawTraceGrantService`
  (request/approve/revoke/expiry, two approval modes, self-escalation +
  approver-distinct + duplicate-active guards), `ADMIN_TIER` retired for
  `_raw_reader_for_org` at every read surface (instance/explain/audit/WS),
  Administrator-gated grants API, revoke-on-deactivation/org-transfer.
  Criteria 1/2/5/11 test-pinned; the intended operational break is live
  (an Administrator without a grant reads no raw). **TG2-human BUILT
  (2026-08-01):** the release-boundary audit — `decide_raw_release` emits the
  append-only raw_trace_access_attempted + raw_trace_release_decided pair
  (one correlation id) at every raw surface (instance/explain/audit/WS)
  before any raw byte leaves; fail-closed to projected +
  redaction_reason=access_audit_unavailable on audit-append failure; a
  below-grant read emits no access event; detail/explain carry raw_included.
  Criteria 2/3/28 test-pinned (test_raw_trace_release_audit).
  **Immutable-attempt model DONE (2026-08-01):** EXECUTION_SEMANTICS §3a is
  written (normative) + a first-class `attempt` number (StepExecution/Row +
  Alembic 0007); the engine already appended one row per attempt, so this
  formalized the contract (and corrected two doc lines that wrongly said a
  re-run "overwrites" the row) and made the step-attempt id the vault keys on
  a guarantee. This was the named gate on TG3b.
  **TG3a BUILT (2026-08-01):** the raw-trace vault + write-time projector as a
  DARK DUAL-WRITE — `raw_traces` (Alembic 0008) + abstract opaque-id repo
  (idempotent put); the default-deny projector (tool_calls / free-form model
  output / recall / errors are raw) + collision-free idempotency key on the
  immutable step-attempt; the engine vaults trigger + each attempt's raw
  output/error while inline stays authoritative (vault failure logged, never
  raised). Additive, non-behavioral for readers. test_raw_trace_vault.
  **TG3b PART 1 BUILT (2026-08-01):** the read-back foundation —
  `RawTraceRehydrator` reconstructs full output/trigger from the vault (§4.3)
  + the system-access audit (§3.2, `raw_trace_system_access_attempted/
  _completed`, engine workload identity, audit-before-fetch, fail-closed),
  validated against the inline copy (test_raw_trace_rehydrate). Additive.
  **TG3b PART 2 BUILT (2026-08-01) — the safe-only flip (flag-gated):** with
  `trace_safe_only` ON the operational store persists only the safe projection
  (instance/step.output/context/trigger/step_completed audit zero-raw at
  rest) and raw lives in the vault; run vaults the raw trigger durably,
  _run_step_once vaults raw before persisting the projection (durable-or-fail),
  resume+fork rehydrate from the vault (system-audited, fail-closed, keyed on
  the latest COMPLETED step-attempt; fork re-binds its own copy). Default OFF
  = dark dual-write (full suite untouched); default flips at the gate.
  Projection extracted to a domain `trace_projection` module first.
  test_trace_safe_only_flip. **TG3b.3 + TG3c + TG3d-1 BUILT (2026-08-01/02):**
  (b.3) read-surface raw-merge so grant-holders regain raw via the API under
  the flip (rehydrate-on-release, no-op under default; test_trace_safe_only_
  read_merge); (c) backfill + STRUCTURAL zero-raw verifier + CLI
  (trace_migration + tools/trace_migration.py; made the projection idempotent
  so the fixed-point verifier works); (d-1) Contract B1 — per-org AES-256-GCM
  envelope encryption of the vault, AEAD-bound to (org,instance,attempt,kind),
  keys from WORKFLOW_PLATFORM_TRACE_MASTER_KEY held outside the DB (a DB dump
  is ciphertext-only), transparent seal-on-write / decrypt-on-read
  (test_trace_cipher). **Gate-wiring DONE (2026-08-01/02):** per-call
  tool_call audit-at-rest projection + explain rehydration; dual-control
  enforcement (`WORKFLOW_PLATFORM_RAW_GRANT_DUAL_CONTROL`); THREAT_MODEL §5a
  amendment; **secret-manager migration** — the vault master key is sourced
  from AWS Secrets Manager (`WORKFLOW_PLATFORM_TRACE_MASTER_KEY_SECRET`,
  `main.py::_resolve_trace_master_key`, off-disk), which is itself a §5a
  release gate. **Validated live** (two-org dry-run + 1,340-instance backfill,
  2026-08-02); external CODE-review archive prepared
  (`docs/TRACE_CODE_REVIEW_GUIDE.md`; archive in `docs/archives/`, see its README).
  **REMAINING before a real external tenant** (deferred, documented, waiting
  on an actual pull):
  1. **TG3d-2 (Contract B2 — host-operator resistance)** — attested/enclaved
     runtime or external/customer-mediated key release so the infra operator
     can't reach the key at decrypt time. Its own threat-model + infra design,
     NOT a code module. Biggest remaining security gap.
  2. **Finalized dependency manifest (§5.1)** — the persisted
     `raw_trace_dependencies` + `snapshot_generation` retention/erasure
     machinery (execution snapshot vs. shorter-lived diagnostic).
  3. **The other §5a release gates** (see THREAT_MODEL §5a) — backup
     encryption, credential-rotation design, the G22 tenant-surface inventory,
     resource-exhaustion limits. The trace boundary is one gate, now
     satisfiable; these are not.
  Deferred within TG1: memory facts-mode-under-grant. Ship to an external org
  stays trigger-gated + needs the chosen B-contract in effect + these gates.

### G-Trace-Review-2 — external CODE re-review (`2cfacfc`, 2026-08-03) — **FAILED; structural rework required**

The re-review passed the F1–F10 suite but reproduced **new, deeper bypasses in
the same areas.** The correct meta-point: round 1 patched surfaces/field-names,
not the boundaries the design promises. Contracts A + B1 still NOT delivered.

**Contained bugs fixed this round** (regression-tested in
`tests/test_trace_review_fixes.py`, "Re-review" section; 974 backend green):
- **rr-F3** memory `categories` endpoint discarded `commit_raw_release`'s result
  → facts returned even when `release_decided` failed. Now fails closed to counts.
- **rr-F4** rehydration returned the AEAD envelope when a row was sealed but no
  key was configured. Now `is_sealed_payload` is cipher-independent → sealed +
  no key raises.
- **rr-F1(marker)** `safe_tool_call` trusted a forged `_redacted` marker. Now
  keyed on the safe SHAPE (`input_keys`, no raw `input`/`result`).
- **rr-F6(partial)** immediate org-scoped activation didn't validate the approval
  mode (tenant_authorized activated with no artifact); `ticket_ref` took free
  text. Now mode validated before activation; refs are opaque tokens.

**Design review (2026-08-03) reframed this: it is BUILD-CONFORMANCE, not new design.**
Three of the four primitives are the v6-FROZEN contracts the build diverged from
— P1 = §1.4 CONTRACT 1, P3(predicate) = §4.3, P2 = criterion 16, P4 = §4.2 +
criterion 31. **No v7 design round**; hold the build to the sections that exist.
The one genuine design gap is §1.4's silence on how a workflow *declares* its
safe business fields. P3 splits: P3a (predicate + §4.1 version columns on
operational rows) vs P3b (§5.1 dependency manifest / `recovery_state`, which the
plan already defers — must NOT gate the leak fix).

**Structural findings — status:**
1. **Typed projector registry (P1)** — **DONE** (this slice). `_SAFE_KEYS` is
   replaced by `_SAFE_FIELDS`: every survivor must pass its field's VALIDATOR
   (approved opaque id / closed enum / bounded number / bool / checked
   container). The blanket "numbers+bools safe by type" pass is gone, so
   `{"category": SECRET}`, `{"usage": [SECRET]}`, `{"ssn": 123456789}` are all
   redacted + vaulted. `PROJECTOR_VERSION` bumped to `2`.
   **Follow-up — §1.4a: DESIGNED, THREE review rounds, BUILD STILL DEFERRED.**
   Reviews on 2026-08-03 (internal), 08-04 and 08-05 each approved the direction
   and deferred authorization. Round 3's finding was authorization *identity and
   lifecycle*: rows bound only declaration content, so revoking approval A1 and
   re-approving identical bytes as A2 would resurrect withheld rows; the lifecycle
   had no atomic transitions or revocation-race ruling; both "frozen" hashes were
   described but not specified; capacity ignored the observable absent/redacted
   states; the revocation claim overstated what it can retract (backups); and
   destination scope was stated but absent from the projection identity. All
   folded — `SafeOutputApproval` is now a first-class object (`approval_id` +
   generation on every produced row and destination copy), CAS lifecycle with
   HARD revocation, RFC-8785/SHA-256 hashing frozen concretely, capacity over
   observable states with `POLICY_BUDGET_BITS = 32`, honest revocation limits,
   destination-aware `project(...)`, YAML key renamed `safe_output` →
   `declassify`. Criteria 51-57 added. The earlier (round-2) findings below
   remain folded. Its central finding: approving the *declaration* alone
   is unsafe, because an ordinary `ORG_WRITE_ROLES` user can edit **only the
   prompt** and repurpose the approved alphabet as an exfiltration codebook —
   no `safe_output` edit, so no admin review, no audit, no grant, no release
   record. It also showed the "≈32 bits/attempt" capacity claim was wrong (it
   assumed scalar enums; an ordered 16-member list with `max_items:16` carries
   ~64 bits alone), and found three more gaps: no revocation lifecycle (append-
   only content-addressing made an approval permanent, so a bad approval became
   an irreversible below-grant disclosure), a closed-enum `purpose` that does not
   satisfy the FROZEN §1.3 consumer/role/retention contract, and an overstated
   fork/retry compatibility claim. **The revision is folded into §1.4a.1–.11**
   (approval binds the step REVISION under dual control; finite integer lattice
   replacing the float `number` form; computed capacity budget; revocation;
   full §1.3 justification; narrowed fork claim; frozen canonical encoding;
   `business` namespace; criteria 42–50). **Awaiting external re-approval —
   do NOT build; P1's over-redaction stands until then.**

   Original gap statement — per-workflow
   business vocabularies (`category`, `attention`, `relevance_bucket`,
   `document_type`, `complexity`) are deliberately NOT platform-registered, so
   they over-redact below grant today — including in the dashboard.
   `TRACE_GOVERNANCE_PLAN` **§1.4a** (written + design-reviewed 2026-08-03,
   adopt-with-conditions, all six folded) specifies the per-workflow
   safe-output declaration that restores it. Build notes from the review:
   - **enum LIST form is required** (`max_items`) — `attention`, `apply_labels`
     and paper-triage `tags` are deduped *lists* (`engine/functions.py`); a
     scalar-only rule would redact the very field that motivated §1.4a;
   - **persist the declaration content-addressed** (append-only, hash-keyed) —
     definitions mutate in place, so hash-only would make pre-edit rows
     unprojectable and break criterion 17; unknown hash fails closed;
   - the **TG3c verifier must resolve each row's declaration**, else it reports
     false positives across the 1,340 backfilled instances;
   - authority is **`ORG_ADMIN_ROLES`** (not `ORG_WRITE_ROLES` — that includes
     Org User, who cannot hold a raw grant), audited by hash, import validates
     identically, **NL scaffold forbidden from emitting the block**;
   - bounds ≤8 fields / ≤16 values / ≤32 chars; required closed-enum `purpose`;
   - `THREAT_MODEL` carries the bounded-channel known-gap row; criteria 37–41.
2. **Total raw-surface inventory (P2)** — **DONE.** Every reproduced bypass is
   closed: step **explain** no longer releases a no-tool step's `output_text`
   (free-form output is raw BY TAINT — the `or not step_used_tool` escape is
   deleted) and projects a deterministic step's `output`; **/api/escalations**
   grant-gates the agent-authored `reason` + `context` (they are written while
   reading hostile mail, and were returned in full to any Org Viewer) with a new
   `escalation` release surface + `raw_included`; **dry-run** projects
   `instance.error` and **run-batch** no longer returns `str(exc)` (it logs and
   returns the marker — the full error stays on the grant-gated detail
   endpoint). 5 regression tests in `test_trace_surface_inventory.py`.
   *Residual:* the inventory is enforced by these fixes + the default-deny
   projector, not yet by a mechanical enumeration — a new endpoint returning a
   raw field would still not be caught automatically. Worth a lint/test sweep
   when P3a lands.
3. **Rehydration validator (P3a)** — **DONE.** Operational rows now carry a
   PERSISTED projection stamp (`projector_version` + `projection_schema_version`,
   Alembic `0011`), set by the engine when it writes a projection. Rehydration
   keys off that stamp, never a redaction marker, so deleting a marker can no
   longer make resume/fork skip the vault. After fetching,
   `verify_projection_agreement` applies the FROZEN §4.3 predicate — re-project
   the raw under the RECORDED version and require it to reproduce the stored
   safe row; a mismatch audits `integrity_failed` and raises. `_payload_of` also
   validates vault lifecycle state (an ABORTED/RESERVED row is rejected) and the
   raw schema version. An unreproducible older projector returns `unsupported`
   and degrades with an audited outcome rather than reading as corrupt
   (criterion 17). 4 regression tests incl. both reviewer probes.
   *Residual:* the read-surface `merge_output` still falls back to the marker
   scan when a row has no stamp — required for the 1,340 backfilled rows, and
   safe there because a miss only means a grant-holder's read does not merge,
   with no effect on what the workflow executes on. **P3b (§5.1 dependency
   manifest / `recovery_state`) stays deferred** per the design review.
4. **DB compare-and-set for grant + vault lifecycle (P4)** — **DONE.** New
   `RawTraceGrantRepo.update_if(grant, expected_state)` is a real CAS in BOTH
   impls (Postgres `UPDATE … WHERE id=? AND state=?` requiring rowcount==1;
   in-memory compares-then-swaps with no await between — the design review's
   condition, so tests can't pass on assurance Postgres wouldn't give).
   `approve` CASes from `pending` and `revoke` from its observed state, so a
   concurrent cancel is no longer resurrected to active. Vault `put` now
   compares a `content_commitment` (sha256 of the PLAINTEXT, computed before
   sealing — ciphertext nonces differ per seal) plus the identity tuple, and
   raises `VaultConflict` on different content under the same key instead of
   silently retaining the OLD raw; the conflict is fatal in dark dual-write too,
   since it means the engine believes something false. Alembic `0010` adds the
   nullable column (pre-P4 rows fall back to payload comparison). 4 regression
   tests. **Residual:** `_clear_stale_active`'s expiry transition still uses a
   blind `save`; it is inside the activation lock and only moves an
   already-expired row, but it should move to CAS when the grant lifecycle next
   gets touched.
5. **Audit endpoints honest release (rides P2/P3)** — under safe-only the audit
   endpoints project at rest but `decide_raw_release` still records
   `outcome=released`+all-kinds; the kind list is also incomplete. Must rehydrate
   + record actual kinds, or return projected + `outcome=projected`. **OPEN.**

Recommended: build P1–P4 as shared primitives (a design-reviewer pass is
warranted after two failed rounds) rather than another patch sweep. Re-review
archive `docs/archives/trace-governance-review-2cfacfc.tar.gz`.

### G-Trace-Review — external CODE review (2026-08-02) — **round-1 fixes (superseded by G-Trace-Review-2)**

The code review of `e397b8b` reproduced real bypasses. All ten findings +
high-priority nits are now fixed, each with an adversarial regression test in
`tests/test_trace_review_fixes.py` (970 backend tests green). Status:

1. **Projector default-ALLOW → default-deny** — `_SAFE_KEYS` allowlist; free-form
   / unknown / `output_text` (tool or not) redacted; vault holds the FULL output
   so default-deny is lossless. **FIXED** (`0732503`).
2. **Error text leaks + not projected** — `redact_error` on the read surfaces
   (list/explain; detail+audit via default-deny); under the flip error is
   vaulted-before-persist + stored as a marker + grant-holder rehydrates
   (`merge_error`). **FIXED** (`280d900`).
3. **Memory introspection bypasses the grant** — `categories` mode now requires
   a covering raw grant (403 without) + the release-audit protocol; `summary`
   counts stay role-gated. **FIXED**.
4. **Dual-control bypass via `tenant_authorized`** — activator must be distinct
   from requester+recipient for BOTH modes, approver recorded, ref must be an
   opaque token. **FIXED** (`344ad42`).
5. **Grant activation not atomic** — in-process activation lock + migration 0009
   expression unique index over `COALESCE(org_id,'__platform_wide__')`. **FIXED**.
6. **B1 cipher binding bypassable** — verify expected tuple, decrypt with expected
   AAD, reject unsealed. **FIXED** (`f920555`).
7. **Trigger rehydration fails open** — projected-but-missing trigger →
   retrieval_failed + raise. **FIXED** (`f920555`).
8. **Release audit success-before-retrieval** — split begin/commit; the
   `release_decided` outcome (released/partial/retrieval_failed) is determined
   AFTER the fetch; `raw_included` honest; fail-closed. **FIXED** (`40ff847`).
9. **WS auth cached for the connection** — grant re-evaluated per raw-bearing
   frame. **FIXED** (`40ff847`).
10. **Verifier can certify nonzero-raw** — `verify_zero_raw` returns a
    `ZeroRawReport` that is `clean` only on an EXHAUSTIVE scan (capped → fail) +
    scans errors + surfaces append-only audit raw as its own category; backfill
    migrates error columns. **FIXED**.

Nits also folded: `from.address` dropped from the safe trigger; `external_approval_ref`
is an opaque token. **Residual (documented, not a bypass):** append-only pre-flip
`audit_log` raw is reported by the verifier but NOT rewritten by backfill (encrypt/
migrate is the remaining B1 at-rest work); the verifier's coarse partial-vs-complete
signal (marker scan) rather than exact per-kind accounting. Re-review before
re-claiming Contract A/B1 or "zero-raw".

- **F4** the apply postcondition — already shipped (4fc8721), acknowledged.
- **F5 (MED)** WS org resolution **fails closed** — a non-admin with no
  user row is rejected, not assigned "default". Pinned. *Gate:* OIDC
  token-in-query → cookie/BFF (OIDC-mode only).

**Deferred as pre-external-org gates:**
- **F6 (MED) cross-tenant slug oracle** — create de-dupes ids against ALL
  orgs, leaking another org's existence via the collision suffix. Proper
  fix is composite `(org_id, slug)` identity — an identity/schema change
  (a naive org-scoped de-dup would cause a global PK collision). One org
  today = no oracle. Test: two orgs, same name, neither observes the other.
- **Offline-reproducible handoff** — ship a hash-pinned wheelhouse OR a
  container/OCI digest of the recorded py3.12.3/uv0.12.0 env next time.

### G18 — Model price/quality benchmark on real labeled data — **harness built 2026-07-31**

`tools/eval_models.py`: runs the production classification task (current
rubric, trigger-shaped prompt) across models over the 154-message
ground-truth corpus (139 current-vocabulary labels; bodies fetched
read-only from Gmail and cached gitignored — no mail content in the
repo), scoring category accuracy vs the human label with tokens / $ /
latency from the live calls and an accuracy-per-dollar ranking. Results
append to `.memory/eval-results.jsonl`. Smoke-verified (3 msgs, Haiku,
100%, $0.005/msg). Related but distinct: the scaffold-quality suite
proposal in `docs/product/LLM_EVAL_FRAMEWORK.md` (different task, judge
layer L3 — pull ideas from it if this grows judge scoring).
Open: the full sweep is a spend decision (~$0.70/model for Haiku-class,
~$2 Sonnet, ~$3.50 Opus over all 139) — operator's call per run.

### G17 — Proactive recall adoption (veracium 0.4.x, trigger-gated)

The one 0.4.x feature assessed and deliberately NOT consumed
(`docs/VERACIUM_041_ADOPTION_PLAN.md` §4 holds the reasoning): the
session-briefing builder has no message-shaped consumer today. Two named
triggers, either reopens it: (1) the G12 elicitation design pass —
evaluate `proactive.assemble` before inventing a substrate; (2) an
operator daily-brief surface (its DATED COMMITMENTS section pairs with
the awaiting-reply lifecycle). Tracked here per the backstop's
2026-07-31 cross-track ledger review (finding 4).

### G16 — Two-axis acceptance labeling (optional)

The part-2 window closed for category + mechanics on operational
evidence (2026-07-30); attention runs in monitored status. The formal
30-message dev + 30-message held-out acceptance pass (zero false urgent,
≥80% review/awaiting-reply precision as raw counts) remains available if
attention precision ever needs a *number* rather than spot-checks —
e.g. before showing attention flags in a customer-facing surface.
Prep: the review CLI needs attention prompts.

### G10 — Learned-memory recall injection (veracium slice 2) — **landed 2026-07-17**

Implemented against both security acceptance criteria (below): an
opt-in `recall:` block on `learned_memory` (`query_from` context path +
`token_budget`); before each agentic step the engine normalizes the
entity (case + plus-addressing — requirement #2), calls
`LearnedMemoryService.recall_context` (zero LLM calls: subgraph render,
wiki off), and injects veracium's pre-rendered block **verbatim** below
the rubric — never-assert fence intact (requirement #1, pinned by
`test_engine_injects_recall_verbatim_with_fence`). Every recall audits
`memory_recalled` (query, context hash, edge/episode counts, injected
flag); failures audit `memory_recall_failed` and never fail the step;
step output gains a `recall` field for run-to-run correlation. Dry runs
snapshot-copy the real store into the ephemeral scratch DB so recall
behaves exactly as production while writes stay discarded.
Empty-store recalls skip injection (judged by counts — veracium renders
a placeholder string, not an empty one). First consumer:
`examples/email_triage_live/` (recall keyed on the sender address).

Gate to start: enough accumulated history to judge recall quality. The
historical backfill ran 2026-07-13 (`tools/backfill_learned_memory.py`:
112 instances → 224 observations, ~$0.68), so the store is seeded; organic
mail accumulates on top. Design questions to settle at slice 2: where
recall sits in the prompt relative to the rubric; a per-step token budget
for recalled context; whether `maintain()` (expiry/consolidation) runs on
a schedule; and how `memory_hash`-style audit extends to recall reads
(`memory_recalled` entries). The quarantine rendering must survive prompt
assembly intact — that's the security property the whole adoption was for.

**Blocker RESOLVED (2026-07-17): system-observation laundering.** The
verdict observation embedded third-party text in a system-authored event,
which veracium ≤0.1.6 granted assertable disclosure (130 laundered edges
in the first seeded store). Fixed veracium-side (option b): `derived_from`
shipped in 0.1.7, we upgraded to **0.2.1**, threaded
`ObservationSpec.derived_from` through the pipeline (the triage verdict
declares `system` + `derived_from: third_party`), and **rebuilt the store
from scratch** (`--reingest`, 149 instances / 298 observations, ~$0.91).
Post-rebuild audit: zero mentionable edges of any authorship; pinned by
`test_observe_derived_from_caps_disclosure` against real veracium.

**Two security requirements for the recall implementation** (from the
veracium dev session's response, `~/Documents/veracium/proposals/
response-to-veracium-enhancements.md` §4 — treat as G10 acceptance
criteria):
1. `recall.context` includes the UNVERIFIED third-party block *by design*
   — prompt assembly must preserve the never-assert fence verbatim: don't
   flatten it into plain context, don't re-summarize it with an LLM step
   (that would be laundering again, one layer up).
2. Recall keys derive from sender addresses — attacker-chosen strings.
   Normalize before keying (case, plus-addressing, display-name tricks);
   veracium treats ids as opaque, so normalization is ours. Note the wiki
   cost model when enabling it (W2): recompile-per-8-writes is
   **per-entity** — hundreds of senders = hundreds of wikis.

Effort: **M**. Also unlocks re-examining awaiting-reply via sent-mail
observation (explicitly out of both slices so far).

---

## Out of scope (still)

Pull-driven, not push-driven — surface these only when there's a
concrete workload requiring them:

- **LLM-driven orchestrator active reasoning** — passive monitoring
  covers current needs (Phase 2 / Week 9).
- **Knowledge ingestion / contextual retrieval (Phases B–F)** — 10 open
  research questions; `docs/RAG_PRODUCTION_NOTES.md` captures concrete
  defaults for when this starts.
- **Generative UI** — dashboard polling + live events are sufficient.
- **OAuth connectors (M365 / Google / Slack)** — pull in when a
  customer asks.
- **Cost analyst LLM** — deterministic cost reports cover operator
  needs; revisit when pattern-finding adds real value.
- **veracium Postgres `Store` adapter (V5)** — decided 2026-07-17: this
  project contributes it as a PR against veracium's `Store` interface
  (dev-session review), but not now. Triggers to start: the platform
  deploys beyond solo-dev, or per-entity write volume outgrows the
  single SQLite file. Until then the learned-memory store stays SQLite.
- **Formal ontology / knowledge graph / semantic layer** — each has a
  specific decision trigger documented in `docs/SEMANTICS.md`; none
  are needed at single-workload / single-engineer scale.

See `CLAUDE.md`'s re-evaluation checkpoint for context.

---

## Landed

- **Users + organizations skeleton (2026-07-19)** — built ahead of need on
  Quentin's call (its absence was distorting user-adjacent features);
  design-reviewer verdict "build modified", all conditions implemented.
  `users`/`organizations` tables (Alembic `0003`, default org seeded in
  the migration), JIT provisioning from `(iss, sub)` with TTL-throttled
  last-seen, `GET /api/me`, `org_id` on definitions AND instances from
  birth, `owner_user_id` set by the API create/import/scaffold paths.
  Explicitly out: enforcement/scoping, invitations, per-org RBAC,
  schema-per-tenant. **Follow-up decided now to avoid a second store
  rebuild:** the veracium memory namespace migrates from raw mailbox
  strings to `user:<user.id>` (mailbox as an attribute) in its own slice.

Tracks where every closed backlog item lives. One-liner per item; chase
the commit history for the full context.

- **P0.1** — `backend/tools/fire.py`: one-shot CLI runner respecting
  `DATABASE_URL` + `BEDROCK_MODE`.
- **P0.2** — `backend/src/workflow_platform/orchestrator.py`:
  startup-time trigger orchestrator loading
  `WORKFLOW_DEFINITIONS_DIR`. `main.py` auto-builds a `WorkflowEngine`.
- **P1.1** — `POST /api/workflows/{id}/run` endpoint + "Run" button on
  the workflows page with a JSON-payload dialog.
- **P1.2** — Dashboard "Import workflow" modal wired to existing
  `POST /api/workflows/import`. (Auto-load on startup was already
  covered by P0.2.)
- **P1.3** — `examples/webhook_echo/` + `examples/scheduled_health_report/`
  example workloads; new `append_file` stock function for periodic
  log-style writes.
- **P1.4** — `RoleSwitcherComponent` in the header — flip identity
  without DevTools.
- **P1.5** — `frontend/src/app/services/evaluation.ts` + "Evaluation"
  panel on the instance-detail page (color-coded scores, reasoning,
  issues, raw fallback).
- **P1.6** — "Memory" column on the instance-detail Steps table
  (first 8 chars, full hash on hover).
- **P2.1** — `frontend/src/app/services/events.service.ts`: WebSocket
  subscription to `/ws/events`, dedupe by id, 2s reconnect. `main.py`
  always builds an `EventBus`.
- **P2.2** *(partial)* — `examples/webhook_echo/recordings/` committed,
  replay-mode pytest covers it. PDF classifier recordings deferred
  (see G1).
- **P2.4** — `LOG_FORMAT` env var on the backend; `text` for dev,
  `json` (default) for production.
- **Email connector — Phase 1** — six days landed across 7 commits.
  `EmailConnector` ABC + `GmailConnector` + `GmailOAuthProvider` +
  `GmailPollTrigger` + `EmailSendTool` / `EmailLabelApplyTool` (capability-
  gated) + bootstrap helper that auto-wires the email tools into
  `main.py` / `tools/fire.py` when `WORKFLOW_PLATFORM_GMAIL_ACCOUNT` is
  set. `examples/email_triage/` workflow + rubric + 5 fixtures.
  `.github/workflows/live-tests.yml` runs `BEDROCK_LIVE=1 GMAIL_LIVE=1`
  weekly; `backend/tools/smoke_gmail.py` is the operator-facing
  five-step diagnostic harness. Live-validated against
  `intelligent.workflow.engine@quentinspencer.com` — full
  send→poll→receive roundtrip green. See
  `docs/EMAIL_CONNECTOR_PLAN.md` for the design + plan.

---

## Updating this doc

When something new lands or surfaces:

- Move closed items from "Active" / "Gaps" into "Landed" with a
  one-liner.
- Add new gaps that came up during the work as a `G<n>` entry under
  "Gaps surfaced," with the same shape (one paragraph + acceptance +
  effort).
- Keep "Out of scope" in sync with `CLAUDE.md`'s re-evaluation checkpoint
  — they should never disagree.
- If the doc is mostly empty under "Active" and "Gaps," that's a signal
  the project is in a steady state and the doc has earned a rest, not
  that it should be filled with speculation.
