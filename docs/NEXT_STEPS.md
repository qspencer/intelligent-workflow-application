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

### G4 — Live events on the instances *list* page

P2.1 wired WebSocket events into the instance-*detail* page. The
*instances list* still polls every 3-5s — new instances created via
fire.py or a trigger appear with that lag. Subscribing the list to the
same `EventsService` (filter on `action == 'workflow_started'`,
prepend new instances to the list) would give parity.

Effort: **S**. Same stream, different consumer.

### G5 — Memory-hash diff view

`memory_hash` is now visible per agent step (P1.6). The natural follow-up
is a small "Compare runs" affordance: pick two instances of the same
workflow, show a side-by-side of their step outputs grouped by which
memory hash each saw. Useful when investigating "why did this run behave
differently from yesterday's?" — exactly the question memory versioning
was added to answer.

Effort: **M**. Mostly frontend; one new `/api/workflow-instances?...`
filter is probably enough on the backend.

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

### G10 — Learned-memory recall injection (veracium slice 2)

The write-only slice landed 2026-07-13 (decision record:
`docs/SEMANTICS.md` → "Adopted (write-only slice): veracium"): the engine
ingests per-run observations into a veracium store, but **nothing reads it
yet** — triage behavior is unchanged by design. Slice 2 closes the loop:
inject `recall()` context (grounded facts + the fenced never-assert
third-party section) into the agentic step's prompt alongside the rubric,
so per-sender history actually prevents the category flapping the live
validation documented.

Gate to start: enough accumulated history to judge recall quality. The
historical backfill ran 2026-07-13 (`tools/backfill_learned_memory.py`:
112 instances → 224 observations, ~$0.68), so the store is seeded; organic
mail accumulates on top. Design questions to settle at slice 2: where
recall sits in the prompt relative to the rubric; a per-step token budget
for recalled context; whether `maintain()` (expiry/consolidation) runs on
a schedule; and how `memory_hash`-style audit extends to recall reads
(`memory_recalled` entries). The quarantine rendering must survive prompt
assembly intact — that's the security property the whole adoption was for.

**Blocker found during the backfill (must resolve before recall ships):
system-observation laundering.** The verdict observation is authored
`system` (the classification is ours), but its template embeds
third-party-controlled text (subject line; the agent summary derives from
body content). veracium grants system-authored events MENTIONABLE
(assertable) disclosure, so the distiller sometimes lifts marketing-mail
content into assertable user facts (`user -[uses_tool]-> <advertiser>`,
130 such edges in the seeded store). No exposure in the write-only slice
(nothing reads the store), and the store is cheaply rebuildable from
Postgres once fixed. Candidate fixes, not yet decided: (a) platform-side —
strip raw third-party text from the system observation (sender address +
category + confidence only) or author it `third_party` and accept use-only
disclosure for classification history; (b) veracium-side — treat quoted/
embedded third-party spans inside SYSTEM events as use-only at extraction
(we own the library; kin to the 0.1.6 `use_only`-leak fix). Decide at
slice-2 design time, then re-run the backfill.

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
- **Formal ontology / knowledge graph / semantic layer** — each has a
  specific decision trigger documented in `docs/SEMANTICS.md`; none
  are needed at single-workload / single-engineer scale.

See `CLAUDE.md`'s re-evaluation checkpoint for context.

---

## Landed

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
