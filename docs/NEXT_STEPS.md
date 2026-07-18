# Next steps

## Where things stand

The manual-testing backlog that motivated this doc is essentially closed.
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

### G11 — Two-axis triage: separate category from attention

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
