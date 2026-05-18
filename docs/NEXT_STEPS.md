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

### P1.1 — Frontend "fire instance" affordance

**The pain.** Even with P0.1 + P0.2, manually exercising a workflow in the
dashboard means switching to a terminal. For a demo audience, that breaks
the flow.

**Proposal.** On the workflows tab, each row gets a "Run with payload"
button. Clicking it opens a JSON editor pre-filled with the trigger schema
(if known) or `{}`. Submitting POSTs to a new
`POST /api/workflows/{id}/run` endpoint that calls into the engine with
the same Postgres-backed repos the API uses elsewhere.

Acceptance:
- New API endpoint, role-gated to Operator+.
- Frontend dialog with JSON validation, displays errors inline.
- After submit, navigate to the new instance's detail view automatically.
- Doesn't replace P0.2 — both paths into the engine should keep working.

Effort: **M** (split: backend endpoint S, frontend dialog S+).

### P1.2 — Persistent workflow definition store + dashboard import

**The pain.** Definitions imported via `POST /api/workflows/import`
disappear on backend restart unless Postgres is wired up. And when Postgres
is wired up, you still have to re-curl every time you reset state.

**Proposal.** Two tiny pieces:
1. A "definitions directory" the backend syncs from at startup
   (`WORKFLOW_DEFINITIONS_DIR` again — same env var as P0.2). YAML files
   become rows in `workflow_definitions`. Re-import on every startup is
   cheap and idempotent.
2. A frontend "Import workflow" button that pastes YAML into the existing
   `/api/workflows/import` endpoint, surfaces validation errors inline, and
   refreshes the list.

Acceptance:
- Files in `examples/` get loaded into Postgres on startup.
- Editing one of the YAML files and restarting the backend updates the
  stored definition (last-writer-wins by id).
- Frontend import dialog closes on success, shows the validation error from
  the backend on failure.

Effort: **S+**.

### P1.3 — Sample workloads for the other two trigger shapes

**The pain.** PDF classifier covers filesystem trigger only. Webhook +
schedule are tested in pytest but have no example you can fire by hand.

**Proposal.** Two small additions under `examples/`:

- `examples/webhook_echo/workflow.yaml` + a README. Trigger:
  `{"type": "webhook", "config": {"trigger_id": "echo"}}`. One agentic step
  that summarizes the incoming payload. Demonstrates the webhook path end
  to end.
- `examples/scheduled_health_report/workflow.yaml` + README. Trigger:
  `{"type": "schedule", "config": {"interval_seconds": 60}}` or a cron
  expression. One agentic step, one deterministic step that writes a
  summary file. Demonstrates the schedule path.

Acceptance:
- Each example has its own README, replay-mode pytest, and a curl-line in
  `docs/MANUAL_TESTING.md` that fires it.
- The category list in the PDF classifier doesn't change.

Effort: **S** (mostly YAML + a few lines of agent prompt per workflow).

### P1.4 — Frontend role switcher widget

**The pain.** Manually testing the role matrix means opening DevTools and
running `localStorage.setItem('wp.user', 'alice')`. Easy to forget, easy
to leave in the wrong state.

**Proposal.** A small dropdown in the dashboard header: "Acting as: 
Admin / Designer / Operator / Viewer / Auditor". Picks update localStorage
and trigger a refresh of the current page. Default reads from current
state; falls back to "dev-user / admins".

Acceptance:
- Visible on every route.
- Role change reflected on the next API call (DevTools → Network confirms).
- Persists across reloads (already does, since localStorage).

Effort: **S**.

---

## P2 — polish

### P2.1 — Frontend WebSocket event subscription

**The pain.** The dashboard polls every 3–5s. Audit log + state changes
appear with that lag — fine for testing logic, surprising during demos.

**Proposal.** Subscribe `/ws/events` from the instance-detail component,
merge incoming events into the existing audit log + state signals. Polling
stays as a fallback if the socket disconnects.

Acceptance:
- New audit entries appear within 200ms of a state change in another tab.
- If the WebSocket fails, the existing polling refresh keeps working.
- No flicker on the audit log when the same entry arrives via both paths
  (the EventBus currently mirrors audit, so dedupe is needed).

Effort: **M**. The backend half is already there.

### P2.2 — Pre-recorded Bedrock fixtures for the PDF classifier

**The pain.** `tools/replay.py` against the PDF classifier example fails
out of the box because no recordings exist. Anyone trying to verify the
flow without AWS access hits a wall.

**Proposal.** Run the workflow once with `BEDROCK_MODE=record` against a
known-input PDF, commit the recording files under `tests/recordings/`. The
example README adds a "play this back" command.

Acceptance:
- `cd backend && uv run python tools/replay.py --definition
  ../examples/pdf_classifier/workflow.yaml --trigger
  '{"file_path":"path/to/sample.pdf"}'` succeeds without AWS creds.
- The committed sample PDF is small (under 50 KB) and unambiguously
  classifiable as one of the seven categories.

Effort: **S**.

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

### P2.4 — JSON log tail-friendly view in dev

**The pain.** Reading raw JSON log lines while you're poking at the
running server is harder than it should be.

**Proposal.** A `LOG_FORMAT` env var (default `json`, accept `text`).
Default `text` in `docker compose up backend` for dev ergonomics; default
`json` in `infra/ecs.tf` for CloudWatch parsing. No code change beyond
plumbing the env var into `configure_logging(json_output=...)`.

Acceptance:
- `LOG_FORMAT=text uv run uvicorn ...` produces human-readable lines
  locally.
- `LOG_FORMAT=json` (or unset) keeps the production behavior.

Effort: **S**.

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
