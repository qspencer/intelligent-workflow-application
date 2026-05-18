# Next steps — enabling more robust manual testing

Forward-looking work, prioritized by *manual-testing impact*. The driving
question for everything below: "what do I need to add so that someone
running through `docs/MANUAL_TESTING.md` doesn't have to drop into a Python
REPL or hand-write JSON to exercise the platform?"

This is a working backlog, not a commitment. Items are sized in
person-hours, scored by manual-testing payoff, and small enough to land in
isolation.

## How to read this

- **P0** — blocks the manual demo loop today; the dashboard sits empty
  without a workaround.
- **P1** — high-friction workaround exists; closing this turns 5-line REPL
  snippets into one CLI command.
- **P2** — polish that makes the manual loop fast and pleasant. Good
  weekend-afternoon work.

Effort: **S** = under half a day, **M** = a day, **L** = multiple days.
Estimates are floors, not ceilings.

Out of scope on purpose (call out a customer pull or an explicit re-eval to
unblock): LLM-driven orchestrator active reasoning, knowledge ingestion
phases B–F, generative UI, OAuth connectors (M365 / Google / Slack). See
`CLAUDE.md`'s re-evaluation checkpoint.

---

## P0 — must-haves

### P0.1 — `tools/fire.py`: one-shot workflow runner with persistence — **Done**

Landed at `backend/tools/fire.py`. Reads `DATABASE_URL` (Postgres / in-memory) and `BEDROCK_MODE` (live / record / replay) from env, persists the definition idempotently, fires the workflow once, prints `instance / state / tokens / cost / backend / view-URL`, and exits non-zero on FAILED / KILLED. Verified end-to-end against Postgres + live Bedrock with the PDF classifier (5-step diamond) — eval scores landed as queryable JSON columns on `step_executions.output`.

### P0.2 — trigger orchestrator in the FastAPI process — **Done**

Landed at `backend/src/workflow_platform/orchestrator.py` + wired into `main.py` lifespan. Reads `WORKFLOW_DEFINITIONS_DIR` (default `examples`), registers filesystem / schedule / webhook triggers against an auto-built engine, audits + continues on per-trigger failures, stops cleanly on shutdown. `manual` trigger type is a deliberate no-op (fired by `tools/fire.py` instead). 11 new unit tests cover the registration matrix + error paths; end-to-end verified by dropping a PDF into a watched folder and seeing the classifier run live against Bedrock with the resulting instance visible via `/api/workflow-instances`.

---

## P1 — high value, lower urgency

### P1.1 — Frontend "fire instance" affordance — **Done**

Landed: new `POST /api/workflows/{id}/run` endpoint (Admin/Operator), accepts JSON payload, awaits `engine.run`, returns `{status, instance_id, state}`. 5 new backend tests cover happy path / 404 / non-object body / role gating / empty body. Frontend gained a "Run" button per workflow row + a dialog with a JSON-validating textarea; on submit it navigates to the new instance's detail page.

### P1.2 — Persistent workflow definition store + dashboard import — **Done**

The auto-load half was already covered by P0.2 (`TriggerOrchestrator` reads `WORKFLOW_DEFINITIONS_DIR` and saves each YAML idempotently). Remaining work was the frontend "Import workflow" dialog: now in `WorkflowsListComponent` as a modal with YAML/JSON toggle, error surfacing, and list refresh on success. Two new `api.service` Vitest tests cover content-type negotiation.

### P1.3 — Sample workloads for the other two trigger shapes — **Done**

Landed: `examples/webhook_echo/` (webhook → one agentic summarizer) and `examples/scheduled_health_report/` (schedule → agentic status line → `append_file`). New stock function `append_file` registered in the default function registry. Each example has a README, a replay-mode pytest (8 tests in `test_example_workflows.py`), and a curl-fire section in `docs/MANUAL_TESTING.md`. PDF classifier categories untouched.

### P1.5 — Surface eval scores in the dashboard — **Done**

Landed: `frontend/src/app/services/evaluation.ts` — pure helper that finds steps whose output looks like a `record_evaluation` result (parse_ok-keyed shape) and lifts the score fields. Instance-detail component gained an "Evaluation" panel above the steps table — score badges color-coded by value (faithfulness + category), reasoning text, issues list, and a `raw` fallback when `parse_ok=false`. 13 new Vitest tests cover the helper. Closest signal-to-effort ratio of any post-P1.3 polish item.

### P1.6 — Surface `memory_hash` in step views — **Done**

Landed: instance-detail Steps table gained a "Memory" column. Agent steps show the first 8 chars of the `sha256:` memory hash (with the full hash on hover via the `title` attribute); non-agentic steps show `—`. Closes the "data's in the audit log but invisible at a glance" gap from the post-R2 work.

### P1.4 — Frontend role switcher widget — **Done**

Landed at `frontend/src/app/components/role-switcher/`. Dropdown in the dashboard header with the five roles (Admin / Designer / Operator / Viewer / Auditor → group names). Updates localStorage and reloads the page so every component re-fetches with the new identity. Defaults to whatever's already in localStorage; falls back to `admins` on unknown / missing values. 5 Vitest tests cover defaults, unknown values, change-then-reload, and username preservation.

---

## P2 — polish

### P2.1 — Frontend WebSocket event subscription — **Done**

Landed: new `EventsService` (`frontend/src/app/services/events.service.ts`) opens a WS to `/ws/events?user=&groups=`, parses each audit-entry frame, and reconnects every 2s on close. Instance-detail component subscribes, filters by `workflow_instance_id`, and appends to the audit list with dedupe-by-id (so the polling refresh + WS push don't double-render the same entry). `main.py` now always builds an `EventBus` (was conditional) so the WS endpoint is live in every deployment. 7 new Vitest tests cover the service end-to-end (open / parse / malformed / reconnect / no-reconnect-after-unsub / close-on-unsub).

### P2.2 — Pre-recorded Bedrock fixtures — **Done (partial)**

Landed: `examples/webhook_echo/recordings/` has a committed Bedrock response fixture for the canonical payload. A new pytest in `test_example_workflows.py` runs the whole webhook_echo flow in REPLAY mode against the fixture, asserting no AWS credentials are needed. README updated with the replay command.

**Scope adjustment vs. original proposal:** the PDF classifier was the original target, but its request hash incorporates `trigger.file_path` and the full extracted text — both vary by machine + filesystem path. Webhook_echo's request depends only on the trigger payload, so the recording is portable. PDF classifier recordings would need either a path-normalization step (intrusive change to BedrockClient) or a fixed-path convention; deferring until there's demand.

### P2.3 — Apply Terraform once and verify

**The pain.** `infra/` is `terraform validate`-clean but the deployed
stack is hypothetical until someone runs `apply`.

**Proposal.** Tighten the IaC for solo-dev posture (ALB SG locked to
your IP, log retention 7 days, `desired_count=0` between sessions —
recommended in the earlier conversation), apply, run a manual test
against the deployed `/api/health` + `/metrics` + dashboard, document any
surprises.

Acceptance:
- `terraform apply` succeeds end-to-end.
- `curl https://<alb>/api/health` (or http://) returns 200 from your IP
  and times out from any other.
- Dashboard at `<alb>/` loads, can fire a workflow if P0.1 + P0.2 are in
  by then.
- `terraform destroy` returns the account to clean state.

Effort: **M**. The IaC is written; risk is operational, not coding.

### P2.4 — JSON log tail-friendly view in dev — **Done**

Landed: `main.py` reads `LOG_FORMAT` env var. `LOG_FORMAT=text` → plain text via `logging.Formatter()`; anything else (or unset) keeps the production JSON behavior. Default unchanged from before, so deployed environments don't shift.

---

## Suggested sequencing

Front-load P0 — those are the items that turn manual testing from "REPL +
guesswork" into "two terminals + a browser." Then mix P1 items based on
what you're demoing next:

- Demoing the platform to someone who's never seen it: P1.1 + P1.4 first
  (fewer terminals, prettier role demo).
- Demoing automation specifically: P1.3 first (more workflow shapes to
  show off).
- Hardening for someone else to operate: P1.2 first (definitions don't
  evaporate on restart).

Don't bother with P2 until the P0 + P1 pieces compose into a working
demo loop. P2 items are independently deferrable — you can pick them off
when you have an hour.

---

## What landing all of this gets you

A 30-minute manual demo loop, repeatable from a clean checkout:

1. `docker compose up -d` (Postgres + backend, triggers wired).
2. `npm start` (frontend).
3. Workflows from `examples/` already imported into Postgres (P1.2).
4. Drop a PDF in `sample_inbox/` (P0.2). Or click "Run" on the workflow
   list (P1.1). Or `curl`-fire a webhook trigger (P0.2). Or wait 60
   seconds for the schedule (P0.2 + P1.3).
5. Watch the new instance appear in the dashboard with live event updates
   (P2.1) and structured state transitions.
6. Switch the role dropdown to Viewer (P1.4) — the lifecycle buttons
   disappear because the role lacks the capability.
7. Open `/metrics` in another tab, see the counters move.

Today, you can do step 1 (without triggers wired), step 2, step 5
(polling), and step 7. Steps 3, 4, 6 are blocked on the items above.

---

## Updating this doc

When an item lands:

- Move its status from "P{0,1,2}" to "Done — landed in `<commit hash>`,
  one-line outcome".
- Update `docs/MANUAL_TESTING.md`'s "Open gaps" section to remove the
  corresponding bullet.
- If a new gap surfaces during manual testing that isn't listed here, add
  it with the same template (problem / proposal / acceptance / effort).
