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

### G2 — Webhook HMAC verification (production hardening)

`POST /api/triggers/webhook/{trigger_id}` is exempt from the user-auth
middleware (the webhook endpoint can't carry a user token). In
production that's a problem: anyone who learns a webhook URL can fire a
workflow. The endpoint should verify an `X-Hub-Signature-256` (or
similar) HMAC against a per-webhook secret stored in `SecretStore`.

Acceptance:
- New `WebhookTrigger` config field for the secret name.
- Middleware-equivalent verification in `POST /api/triggers/webhook/...`
  before calling into the registry.
- Reject with 401 on missing / bad signature; existing tests still pass
  for the unsigned-OK dev path.

Effort: **S**. Blocks any real-world webhook integration.

### G3 — Cost dashboard view

`/api/cost/by-{workflow,model,day}` exists and is queryable via curl,
but nothing in the UI surfaces it. A small "Cost" route alongside
Instances + Workflows showing the three breakdowns as tables would
close the gap. The endpoints, role-gating, and `CostReportService` are
all in.

Acceptance:
- New lazy-loaded route at `/cost`.
- Three small tables, each polling at the existing 5s cadence.
- Optional `?since=` controls.

Effort: **S+**. Pure frontend; no new backend work.

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
