# Working on the Intelligent Workflow Platform

You are the engineering lead for this project: an AI-powered workflow automation platform where users describe workflows in natural language and the system builds, executes, and optimizes them. PDF document processing is the first use case; the architecture is trigger-agnostic.

This file is loaded into every conversation in this repository. Read it, internalize it, let it shape your defaults.

## Where the design lives

Don't restate or duplicate. Read these when the work touches them.

All design docs live under `docs/`. The `README.md` at the root has the quick-start.

| File | Contains |
|---|---|
| `docs/VISION.md` | Product vision, anti-goals, what success looks like |
| `docs/ARCHITECTURE.md` | Decisions D1–D13, agent hierarchy, knowledge system, security, mock-world testing |
| `docs/BUILD_PLAN.md` | Recommended execution sequence — vertical slice first, what to defer |
| `docs/IMPLEMENTATION_PLAN.md` | Layer-by-layer scope (alternative view; BUILD_PLAN diverges deliberately) |
| `docs/LEARNING.md` | Three learning dimensions: users, environment, execution |
| `docs/LEARNING_IMPLEMENTATION.md` | Memory + knowledge phases A–F, 10 open research questions |
| `docs/INTEGRATIONS.md` | Connector strategy, tier 1–3 priorities |
| `docs/GENERATIVE_UI.md` | Conversational UI generation (deferred until Phase 2+) |
| `docs/AGENT_SETUP.md` | How this project is configured for Claude Code |
| `docs/BEDROCK_SETUP.md` | Operator-facing AWS Bedrock onboarding (model access, inference profiles, use-case form, quotas) |
| `docs/MANUAL_TESTING.md` | Operator-facing local manual-test playbook (smoke + role checks + observability + what's not yet manually testable) |
| `docs/NEXT_STEPS.md` | Prioritized backlog (P0 / P1 / P2) for closing the manual-testing gaps surfaced in `MANUAL_TESTING.md` |
| `docs/USE_CASES.md` | Candidate validation workloads (filesystem / webhook / schedule), capability-fit map, recommended first build (GitHub PR triage) |
| `docs/RAG_PRODUCTION_NOTES.md` | Notes from a production-RAG article: what aligns with current design, what to pull forward, what to capture as Phase B inputs |
| `docs/SEMANTICS.md` | Decision log: why we haven't adopted a formal ontology / knowledge graph / semantic layer, and what trigger reopens each |

Lessons learned across sessions live in the auto-memory system. Update it as you discover things worth remembering. Memory takes precedence over assumptions; verify before acting on it.

## Current status

**Phase 0 is complete on `main`.** Weeks 1–3 landed:

- **Spine** — `uv` Python 3.12 backend, FastAPI, ruff + mypy strict, GitHub Actions CI with Postgres service, backend Dockerfile (tesseract + poppler), docker-compose with Postgres.
- **Bedrock wrapper** — live/record/replay modes; tests cannot accidentally hit AWS.
- **Tools** — `Tool` ABC with `ToolContext`; `pdf_extract` (ported from the prototype), `file_read` + `file_write` (go through World).
- **World abstraction** — `Filesystem` substantive on both sides; `Messaging` and `Database` are scaffolds (real impl raises `NotImplementedError` until the Week 7 connector framework). `MockWorld` for tests.
- **Agent loop** — tool-use loop with `AgentPolicy`, `AgentUsage`, full conversation + per-call tool log on `AgentResult`.
- **Workflow engine** — `WorkflowDefinition` (Pydantic, discriminated union over deterministic / agentic steps), DAG validator (Kahn's), `WorkflowEngine` walks topological order sequentially. Per-step lifecycle. Audit log writes for every transition + every agent tool call.
- **Persistence** — repository interface + `InMemoryRepositories` (unit tests) + `PostgresRepositories` (production). SQLAlchemy 2.0 async + asyncpg. Alembic with one migration creating `workflow_definitions`, `workflow_instances`, `step_executions`, `audit_log`.
- **Triggers** — `Trigger` plugin interface; `FilesystemTrigger` watches a folder.
- **API** — `/api/health`, `/api/workflows`, `/api/workflow-instances/{id}`, `/api/audit`. Picks Postgres or in-memory repos from `DATABASE_URL`.
- **Tests** — 62 unit + 2 integration (Postgres-gated). End-to-end test exercises the BUILD_PLAN success criterion (PDF-drop simulation → workflow → MockWorld + audit log) in under one second. Alembic up/down sanity check in CI.

**Phase 0 success criteria met:** workflow runs end to end against MockWorld with full audit trail, the same workflow re-runs as a deterministic test in CI in <1 s, and the architecture is shaped so a second engineer can add a tool or step function in under a day.

**Phase 1 / Weeks 4–5 are complete.** Security spine + executor depth + memory + a second trigger.

Week 4 (security spine):
- Capability model + intersection (system → workflow → step → runtime, most restrictive wins), enforced in Agent dispatch + FileRead/FileWrite/PdfExtract tools.
- OIDC validation (PyJWT against JWKS, iss/aud/exp checks) with a `dev` header-based bypass for local development.
- RBAC: 5 roles (Admin / Workflow Designer / Operator / Viewer / Auditor) mapped from IdP groups, AuthMiddleware + `require_roles` dependency on audit endpoints.

Week 5 (executor depth + memory + second trigger):
- Parallel DAG execution (asyncio.wait FIRST_COMPLETED, edge-driven readiness; failure cancels in-flight siblings).
- Conditional edges with simpleeval-sandboxed expressions; targets whose every incoming edge is inactive transition to SKIPPED, and skip propagates downstream.
- Per-step retries (`runtime.retries`) + per-step / per-workflow timeouts (`runtime.timeout_seconds`, `policies.timeout_seconds`); `step_retry` audit entries.
- Pause/resume: `WorkflowInstanceState.PAUSED`, between-step state polling, `engine.resume(definition, instance_id)` continues from where it left off; `POST /api/workflow-instances/{id}/{pause,resume}` (Admin/Operator).
- Agent memory Phase A: `MemoryManager` (file-backed, structured Markdown), engine prepends loaded memory to each agentic step's system prompt under "Prior agent memory".
- Webhook trigger: `WebhookRegistry` + `WebhookTrigger`; `POST /api/triggers/webhook/{trigger_id}` is exempt from user-auth middleware (production must add HMAC verification).

121 unit tests passing (was 100). Ruff and mypy strict clean across 72 source files.

**Phase 1 / Week 6 (static dashboard) is complete.** Backend lifecycle endpoints (kill, retry, list-instances), WebSocket event stream, replay-mode CLI, and a hand-written Angular 18 standalone dashboard.

- Backend: `WorkflowInstanceState.KILLED`, `POST /api/workflow-instances/{id}/kill`, `POST .../retry` (transitions FAILED → resume), `GET /api/workflow-instances` with `workflow_id` / `state` / `limit` filters.
- WebSocket: `EventBus` mirrors every audit append. `/ws/events` streams JSON to authenticated subscribers; dev mode reads identity from query params, oidc validates a `?token=` JWT.
- Replay CLI: `tools/replay.py --definition X --trigger '{...}' --recordings-dir Y` re-runs a workflow against in-memory repos + replay-mode Bedrock.
- Frontend: Angular 18 standalone, three lazy-loaded routes (workflows / instances / instance detail), `ApiService` over `/api`, dev-mode auth interceptor (X-Dev-User from localStorage), polling-based refresh (3-5s), retry/pause/kill/resume buttons, audit log inline. WebSocket integration deferred — polling cadence is sufficient for Week 6.

134 unit tests passing (was 121). Ruff and mypy strict clean across 77 source files. The frontend has no specs yet (will follow component refactors).

**Phase 2 / Week 7 (connector framework) is complete.**

- `Connector` ABC with the six methods from `docs/INTEGRATIONS.md` (trigger_listen / trigger_poll / send / query / authenticate / health_check); default no-ops for trigger and `NotImplementedError` for send/query so connectors only implement what they support.
- `SecretStore` ABC with `EnvSecretStore` (dev) and `AwsSecretsManagerStore` (SaaS, boto3 + asyncio.to_thread).
- `WebhookConnector` (outbound HTTP via httpx; auth headers from a SecretStore key) and `S3Connector` (boto3 S3 wrapped in asyncio.to_thread; `trigger_poll` emits new keys via an in-memory cursor).
- `ConnectorRegistry` plus `ConnectorSendTool` and `ConnectorQueryTool` for agent access. Capability allowlist still gates whether the agent can call connector tools.

164 unit tests passing (was 138). Ruff and mypy strict clean across 89 source files.

**Phase 2 / Week 8 (cost metering + budget enforcement) is complete.**

- `workflow_platform.cost.pricing` — Bedrock model pricing table (per-1M-token input/output prices for the main Anthropic models). `cost_for_usage(usage, model_id)` computes USD; unknown models return 0.0 with a debug log. Operators override via the `WORKFLOW_PLATFORM_PRICING` env var (JSON).
- Engine attribution — `_run_agentic` adds `model` and `cost_usd` to each step output; `WorkflowContext` accumulates `total_tokens` + `total_cost_usd` across the run; final values land on the `WorkflowInstance.context`.
- Budget enforcement — `WorkflowPolicy.budget_action: "notify" | "pause" | "escalate"` (default `pause`). After every step, the engine checks `total_tokens` against `policies.max_total_tokens`. `notify` audits `budget_exceeded` and continues; `pause` does the same audit + raises `_PauseRequested` (instance ends in `PAUSED`, resumable); `escalate` audits `budget_escalated` + pauses.
- `CostReportService` (`by_workflow` / `by_model` / `by_day`) aggregates over recent step executions. New `StepExecutionRepo.list_recent(limit, since)` on both in-memory and Postgres impls.
- API — `GET /api/cost/{by-workflow,by-model,by-day}`, all auth-gated, with optional `since=` ISO datetime filter.

180 unit tests passing (was 164). Ruff and mypy strict clean across 93 source files.

**Phase 2 / Week 9 (passive orchestrator) is complete.**

- `MonitoringService` runs an async background loop with configurable `interval_seconds`. Four threshold-based checks emit `alert_*` audit entries (and EventBus events when wired): `alert_stuck_workflow` (per-instance, fires once per process), `alert_high_error_rate` (rate over a recent window with a min-sample floor), `alert_high_queue_depth` (PENDING count), `alert_high_token_burn` (sum across recent agentic step outputs). `run_once(now=)` is exposed for deterministic tests.
- New `InstanceRepo.list_recent(limit, since)` on both in-memory and Postgres impls so the loop can scan system-wide instances without going through every workflow definition.
- `RequestHumanReviewTool` (`request_human_review`) writes an `escalation_requested` audit entry; per `docs/ARCHITECTURE.md` D7 this is the human-operator hop. `GET /api/escalations` lists pending (filters out resolved); `POST /api/escalations/{id}/resolve` (Admin/Operator) appends an `escalation_resolved` entry referencing the original.
- **No LLM-driven active reasoning yet** — strictly deterministic checks. The active-reasoning orchestrator lands when there's enough running-workflow signal to make its decisions worth making.

197 unit tests passing (was 180). Ruff and mypy strict clean across 98 source files.

**Phase 2 is complete.** Week 10 closed it with a third workflow shape (`ScheduleTrigger` via `croniter`), JSON/YAML export-import for definitions, and explicit verification of all four Phase 2 success criteria.

Week 10:
- `ScheduleTrigger`: cron expression (optional timezone) or `interval_seconds` shorthand. Background loop sleeps until next fire; callback exceptions don't kill the loop.
- `dump_definition_to_{json,yaml}` + `load_definition_from_yaml`. `GET /api/workflows/{id}/export?format=json|yaml` returns the right Content-Type; `POST /api/workflows/import` accepts JSON or YAML body (Designer/Admin role-gated; 400 on invalid).
- 4 Phase 2 verification tests pass: three concurrent shapes against local fs + mocked S3; error-rate alert with audit detail (`failed`/`total_terminal`/`rate`/`threshold`); runaway instance pauses while siblings continue; per-workflow / per-day cost reports.

219 unit tests passing (was 197). Ruff and mypy strict clean across 102 source files.

**Re-evaluation checkpoint** per `docs/BUILD_PLAN.md`. Deferred items, revisited at the end of Phase 2:
- Knowledge ingestion / contextual retrieval (Phases B–F): no concrete workload demanding it. Remain deferred.
- Generative UI: dashboard polling is sufficient. Remain deferred.
- M365 / Google / Slack connectors: pull in when a customer asks. Remain deferred.
- LLM-driven orchestrator active reasoning: passive monitoring covers current needs. Revisit when production traffic produces patterns worth reasoning over.
- Cost analyst LLM: deterministic cost reports cover operator needs. Revisit when pattern-finding adds real value.

**Post-Phase-2 (operator readiness + first real workload):**

- AWS Bedrock onboarding (`docs/BEDROCK_SETUP.md`): the four gates (model access — now retired by AWS; inference profile required for Claude 4.x; Anthropic use-case-details form; service quotas) documented with literal error strings. Live smoke verified end-to-end against `us.anthropic.claude-haiku-4-5-20251001-v1:0`.
- `backend/tools/smoke_live.py` rewritten: per-step pass/fail formatting, gate diagnosis (cause + action) on known errors, stops at first failure.
- `cost/pricing.py`: Claude 4 family entries (Haiku 4.5 = $1/$5; Sonnet 4.6 = $3/$15; Opus 4.6 / 4.7 = $5/$25 per 1M tokens) + a region-prefix normalization step so `us.` / `eu.` / `apac.` / `global.` inference-profile ids attribute correctly.
- Deployment IaC: `infra/` is written and `terraform validate` clean (~$53/mo idle when applied). Not yet applied — solo-dev posture means run-locally is fine; if deployed, restrict ALB ingress to a single IP.
- First real workload: PDF classifier example under `examples/pdf_classifier/`. Trigger = filesystem inbox; deterministic `pdf_extract` → agentic `classify` (Claude returns JSON: `document_type` + `summary` + `key_fields`) → deterministic `route_by_classification` (copies the PDF into `output/<category>/`). Categories mirror the prototype's seven (invoice / receipt / contract / report / letter / form / other). End-to-end test in `backend/tests/test_pdf_classifier_workflow.py` drives a real PyMuPDF extraction with a faked Bedrock response and asserts the file lands in the right folder.
- Observability (`workflow_platform.observability`): structured JSON logging via `JsonFormatter` on stdlib `logging` (no extra deps) + a `configure_logging(level, json_output=True)` helper called from `main.py`. Prometheus metrics via `prometheus-client`: `Metrics` protocol + `NoopMetrics` (default) + `PrometheusMetrics`. The engine records workflow runs by state, workflow / step durations as histograms, agent tokens by model + kind, and Bedrock cost by model. `/metrics` endpoint exposed in `main.py`, exempt from auth.
- Live Bedrock smoke folded into pytest as `@pytest.mark.live`, opt-in via `BEDROCK_LIVE=1` — mirrors the Postgres `@pytest.mark.integration` + `TEST_DATABASE_URL` pattern. Three checks (direct converse / Agent.run / WorkflowEngine end-to-end) deselect by default and never run in CI. The standalone `backend/tools/smoke_live.py` stays as the printable diagnostic harness.
- Frontend smoke tests: Vitest + jsdom under `frontend/src/`. 15 tests covering the auth interceptor (header injection from localStorage), `ApiService` URL construction across all 7 endpoints, and the route config (asserts the lazy-loaded components actually JIT-compile — the closest we get to template-bug detection without TestBed). Wired into CI as a second job that runs `npm test` + `npm run build`. `npm test` is now Vitest, not the unused `ng test`.
- LLM-as-judge eval loop on the PDF classifier (informed by `docs/RAG_PRODUCTION_NOTES.md` R1): new agentic `evaluate` step + deterministic `record_evaluation` function emit `faithfulness_score` + `category_score` + `reasoning` + `issues` as structured fields on `step_executions.output`. Workflow is now a diamond after `classify` — routing (user-facing) and evaluation (observability) run in parallel; an unparseable evaluator response sets `parse_ok=False` and doesn't fail the workflow. Queryable across runs via Postgres JSON paths.
- Agent memory versioning in audit (R2): `_run_agentic` now computes `sha256:<16 chars>` of the loaded memory text and includes it as `memory_hash` on the agent step's output (null when no `MemoryManager` is configured). Lets audit-log consumers correlate behavior changes with memory edits without re-reading the file.
- `backend/tools/fire.py` (P0.1 from `docs/NEXT_STEPS.md`): one-shot workflow runner that respects `DATABASE_URL` (Postgres-backed → visible in dashboard) and `BEDROCK_MODE` (live/record/replay). Persists the definition idempotently, runs once with a trigger payload, prints `instance / state / tokens / cost / backend / view-URL`, exits non-zero on FAILED / KILLED. Verified end-to-end against the PDF classifier with Postgres + live Bedrock — closes the "dashboard sits empty unless I paste 20 lines into a REPL" gap from `docs/MANUAL_TESTING.md`.
- Trigger orchestrator (P0.2): `workflow_platform.orchestrator.TriggerOrchestrator` loads workflow YAMLs from `WORKFLOW_DEFINITIONS_DIR` (default `examples`) at startup, registers each definition's trigger (`filesystem` / `schedule` / `webhook` / `manual` → no-op) against the in-process engine, and stops them cleanly on lifespan shutdown. `main.py` now auto-builds a `WorkflowEngine` (BedrockClient + real_world + stock tools) when `create_app()` is called without one, so the production app actually has a usable engine. Per-definition errors (parse failures, unknown trigger types, constructor failures, runtime callback exceptions) audit + continue instead of crashing the server. Verified end-to-end: `uvicorn` started with `WORKFLOW_DEFINITIONS_DIR=/tmp/e2e-defs` → PDF dropped in inbox → filesystem trigger fired → classifier ran against live Bedrock → file landed in `output/invoice/`, instance visible via `/api/workflow-instances`. Closes "Open gaps" #2 + #3 in `docs/MANUAL_TESTING.md`.
- Webhook + schedule example workloads (P1.3): `examples/webhook_echo/` (webhook → one agentic summarizer) and `examples/scheduled_health_report/` (schedule every 60s → agentic status line → deterministic `append_file` to `/tmp/scheduled-health-report.log`). Companions to the PDF classifier; together they cover all three trigger shapes. New stock function `append_file` (config: `content_from`, `path`) registered in `default_function_registry`. `docs/MANUAL_TESTING.md` gained two new numbered sections (4b webhook, 4c schedule) with curl-fire commands.
- Eval scores in the dashboard (P1.5): new `frontend/src/app/services/evaluation.ts` helper extracts `record_evaluation`-shaped output from any step (keyed on `parse_ok`). The instance-detail view gained an "Evaluation" panel above the steps table — color-coded score badges (green/yellow/red by 0-5 bucket), reasoning, issues list, and a raw fallback for unparseable outputs. 13 new Vitest tests cover the helper. Closes the "data is queryable in Postgres but invisible at a glance" gap.
- Dashboard polish (P1.4 + P1.6 + P2.4): Steps table gained a "Memory" column showing the first 8 chars of each agent step's `memory_hash` (with full hash on hover via `title=`). New `RoleSwitcherComponent` in the header lets you flip between the 5 group identities without touching DevTools — writes localStorage and reloads (5 Vitest tests cover defaults, unknowns, change-then-reload, username preservation). `LOG_FORMAT=text` env var on the backend swaps the JSON formatter for plain text; default behavior unchanged.
- Dashboard import + run (P1.2 + P1.1): "Import workflow" dialog on the Workflows page (YAML/JSON toggle, error surfacing) wired to the existing `POST /api/workflows/import` endpoint; "Run" button per row that opens a JSON-payload dialog wired to a new `POST /api/workflows/{id}/run` endpoint (Admin/Operator-gated, awaits `engine.run`, returns `instance_id`). After a successful Run, the dashboard navigates straight to the new instance's detail view. 5 new backend tests + 3 new frontend tests.
- Live event stream (P2.1): new `EventsService` opens a WebSocket to `/ws/events`, parses audit-entry frames, reconnects every 2s on close. Instance-detail subscribes and merges incoming events into the audit list with dedupe-by-id (so the polling refresh + WS push don't double-render). `main.py` always builds an `EventBus` now (was conditional), so the WS endpoint is live in every deployment. 7 new Vitest tests cover open / parse / malformed / reconnect / no-reconnect-after-unsub / close-on-unsub.
- Recorded Bedrock fixture (P2.2, partial): `examples/webhook_echo/recordings/` has a committed response that lets `tools/replay.py` run the workflow end-to-end with `BEDROCK_MODE=replay` and no AWS credentials. New pytest covers the replay path. PDF classifier recordings were the original target but deferred — its request hash includes file paths, which aren't portable across machines.
- First validation workload: GitHub PR triage. `examples/github_pr_triage/` holds the workflow YAML, agent memory rubric (7 categories + 5-tier complexity + concerns checklist), five synthetic PR fixtures, and three helper scripts (`fetch_prs.sh` for `gh api`, `run_one.sh`, `run_batch.sh`). New stock function `record_pr_triage` parses the agent's JSON output into structured fields (`category`, `complexity`, `needs_tests`, `summary`, `concerns`, `concern_count`) so SQL queries over `step_executions.output` work directly. 13 replay-mode tests in `backend/tests/test_github_pr_triage.py`. See `docs/USE_CASES.md` for the catalog of candidate workloads and why this one came first.
- Auto-loaded agent memory (G6): `TriggerOrchestrator` + `tools/fire.py` now read `<yaml_dir>/agent_memory.md` next to each workflow YAML and seed it as the `MemoryManager` content for every agentic step's `agent_id`. Surfaced during the PR-triage validation iteration: example workflows shipped memory files that were never loaded at runtime, so `memory_hash` was null on every run and rubric edits had no effect. New `MemoryManager.write_raw` method + `seed_memory_from_workflow_dir` helper. `main.py` auto-builds a `MemoryManager` from `WORKFLOW_PLATFORM_MEMORY_DIR` (default `./.memory`). Verified end-to-end against the PR-triage example with the inlined `system_prompt` block removed.
- Second validation workload: research paper triage. `examples/research_paper_triage/` holds the workflow YAML, agent memory rubric (reader-interest profile + 5-bucket relevance scale + 7-tag catalog), five synthetic paper fixtures, three helper scripts (`fetch_papers.sh` querying arXiv API + slimming Atom to JSON, `run_one.sh`, `run_batch.sh`), and a committed 50-paper batch under `data/arxiv_batch_50.json`. New stock function `record_paper_triage` parses agent output into `relevance_score` / `relevance_bucket` / `summary` / `key_concepts` / `tags` / counts. 13 replay-mode tests in `backend/tests/test_research_paper_triage.py`. Validated with 3 live rubric iterations on the 50 committed papers (~$0.44 total spend, $0.003/paper); main finding was `case_study` over-application — three rounds of tightening took false-positive rate from 54% → 16% → 8%, with remaining 4 being borderline abstracts.

307 backend unit tests + 43 frontend tests passing; 2 Postgres-gated + 3 Bedrock-gated integration tests deselect by default. Ruff and mypy strict clean across 114 source files.

Looking ahead: applying the IaC if/when there's a reason to deploy. Further connectors and the LLM-driven orchestrator stay deferred until a workload pulls them in.

Run `git log --oneline` for the live state of the tree.

## Operating principles

These are project rules. Apply them by default; only override with explicit reason and surface the deviation.

- **Vertical slice over horizontal layers.** A working end-to-end slice with audit logs and replay tests beats any single layer built to completion in isolation. See BUILD_PLAN principle 1.
- **Replay mode and mock worlds from day zero.** Bedrock replay is in place; mock world lands Week 2. Every agent test is replayable and runs without AWS credentials.
- **Capabilities and audit log are not retrofittable.** Bake them into the spine in Phase 0/1, not Phase 7.
- **Phase-aware scope.** Knowledge ingestion (Phase B) is deferred — it has 10 open research questions. Generative UI is deferred. LLM-driven orchestrator is deferred. M365/Google/Slack connectors are deferred. See BUILD_PLAN's "Aggressively deferred" table.
- **Take from the prototype only what holds up.** The prototype at `/home/ubuntu/Dev/pdf-tool` is a rules-engine + single-prompt classifier, not an agent framework. Read the per-component verdicts in `docs/ARCHITECTURE.md` ("What We Take from the Prototype") before suggesting reuse. Default: inspect, evaluate, then decide — don't reuse vaguely.
- **TDD by layer.** See `docs/ARCHITECTURE.md` "TDD by Layer" table — agent framework gets unit tests, LLM-dependent code gets replay tests, the workflow engine has no LLM dependency at all.
- **Cost-aware by default.** Don't use an LLM where deterministic logic works. Cheapest model first; escalate only when it demonstrably struggles. (VISION anti-goal #3.)

## Coding rules

- Python 3.12, async-throughout. Wrap sync boto3 / blocking I/O via `asyncio.to_thread`.
- Strict typing: mypy strict in CI on `src` and `tests`. Pydantic v2 for data models.
- Lint + format with ruff. No black, no isort, no flake8.
- One file per concept. No premature abstractions — three similar lines is better than a wrong abstraction.
- Comments only when the *why* is non-obvious. Don't explain *what*; names do that.
- Memory files (when implemented Week 5) are **structured Markdown**, not JSON. Headings as chunk boundaries. See `docs/LEARNING.md` "Memory Storage Format."
- Never commit credentials, secrets, or `.env`. `.gitignore` covers the obvious paths.
- New tests get the `BEDROCK_MODE=replay` default from `tests/conftest.py` automatically. To regenerate fixtures, run with `BEDROCK_MODE=record` and AWS credentials.

## Tooling decisions

Non-load-bearing — change with cause, but document the change.

| Concern | Choice |
|---|---|
| Package manager | `uv` |
| Lint + format | `ruff` |
| Type checker | `mypy` strict |
| Web framework | FastAPI + uvicorn, async-throughout |
| DB (Week 3+) | SQLAlchemy 2.0 async + asyncpg + Alembic + pgvector |
| LLM | AWS Bedrock via `boto3` (sync, wrapped) |
| Tests | pytest + pytest-asyncio |

## What NOT to do

- Don't build the LLM-driven orchestrator yet (Phase 2).
- Don't build the knowledge ingestion / contextual retrieval pipeline (deferred; 10 open research questions).
- Don't build the generative UI (Phase 2+; static Angular dashboard lands Week 6).
- Don't port the prototype's `bedrock_service.py`, `folder_watcher.py`, or WebSocket pattern. Reference — don't lift — the action executors.
- Don't add audit logging or capability checks "later." They land in Phase 0/1.
- Don't add features, refactors, or abstractions beyond what the task requires. Bug fixes don't need surrounding cleanup.

## A note on this file vs the design corpus

`CLAUDE.md` is the operating manual — short, action-oriented, loaded every session. The design corpus under `docs/` is the reference — long, declarative, read on demand. Don't migrate design content into CLAUDE.md and don't migrate operational rules out of it. Keep this file lean.

CLAUDE.md stays at the project root because Claude Code auto-loads it from there. Don't move it.
