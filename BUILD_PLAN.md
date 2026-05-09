# Build Plan — Recommended Execution Sequence

## Purpose

This is an opinionated, time-sliced plan for *how* to build the platform. It complements (and in places diverges from) `IMPLEMENTATION_PLAN.md`.

- `IMPLEMENTATION_PLAN.md` describes *what* the system needs eventually, organized by layer (foundation → workflow engine → orchestrator → ...).
- This document organizes the same work *vertically*: a single end-to-end workflow first, then each layer is thickened. The goal is to surface integration risk early and give every subsequent feature a tested spine to land on.

Where this plan diverges from `IMPLEMENTATION_PLAN.md`, the divergence is called out explicitly and justified.

---

## Guiding principles

1. **Vertical slice first, then thicken.** A boring workflow that runs end-to-end with audit logs is more valuable than any single layer built to completion in isolation. Integration risk is the dominant risk.
2. **Replay and mock-world from day zero.** `IMPLEMENTATION_PLAN.md` puts testing infrastructure in Phase 10. Defer it and every agent test becomes painful; every regression hunt becomes guesswork. Build it first.
3. **Take from the prototype only what holds up.** The PDF Action Automator at `/home/ubuntu/Dev/pdf-tool` is a small (~830 LOC) rules-engine + single-prompt LLM classifier, not an agent framework. After review, the per-component verdicts are:

   | Prototype component | Verdict | Reason |
   |---|---|---|
   | `services/pdf_extractor.py` (~40 lines) | **Port as-is** | PyMuPDF + pytesseract auto-detect is the actual battle-tested logic. Wraps cleanly as a tool. |
   | `services/actions.py` action executors | **Reference, don't lift** | The implementations (httpx webhook, file ops, Playwright steps, CLI subprocess) are useful as call-shape references, but the prototype's `ActionExecutor.execute(config, context)` interface with `$key` template substitution is the wrong shape for LLM tool-use with structured `parameters_schema` and capability-checked execution. Rebuild around the new `Tool` interface using these as references. |
   | `services/bedrock_service.py` (62 lines) | **Don't port** | Single hardcoded "analyze a PDF" prompt returning JSON. No tool-use loop, no streaming, no token counting, no multi-turn. The new agent framework needs all of these. The only reusable insight is "use Bedrock `converse`" — already in `ARCHITECTURE.md`. |
   | `services/folder_watcher.py` (33 lines) | **Don't port — rewrite** | Trivially thin `watchdog` wrapper that hardcodes `.pdf`. The new system needs configurable patterns and a generic trigger-plugin shape. Rewriting takes less time than adapting. |
   | `main.py` WebSocket pattern | **Don't port — rewrite** | `set[WebSocket]` broadcast-to-all with no auth, no per-user scoping, no backpressure. The new system streams agent reasoning per-user with auth — a different shape entirely. |
   | FastAPI app structure | **Use as a reference layout, not a starting point** | Generic enough that the patterns are obvious; nothing project-specific to lift. |

   Net: port one file (`pdf_extractor.py`), reference a handful of action implementations when building tools, design everything else fresh for the agent-framework architecture.
4. **Static dashboard before generative UI.** Generative UI (`GENERATIVE_UI.md`) is a differentiator, not a foundation. A boring Angular dashboard with workflow list + trace view is enough for months of internal use.
5. **Defer learning and knowledge ingestion.** `LEARNING_IMPLEMENTATION.md` has 10 explicit open research questions. These need empirical iteration on real workloads — not upfront commitment.
6. **Capabilities and audit log are not retrofittable.** Bake them in by Phase 1, not Phase 7.

---

## Phase 0 — Spine (weeks 1–3)

**Goal:** one workflow runs end-to-end against a mock world, with replayable tests and an audit trail.

### Week 1 — Foundations and prototype port

- Monorepo scaffold: `backend/` (FastAPI, async), `frontend/` (Angular shell, no real UI yet), `shared/` (schemas), `tools/`, `tests/`
- Docker Compose: backend, frontend, Postgres
- CI skeleton: lint, type-check, unit tests
- **Bedrock wrapper with record/replay built in.** Recording captures every `converse` request/response; replay reads from disk; mode is per-test config. *Divergence: this is Phase 10.1 in `IMPLEMENTATION_PLAN.md` — pulled forward. Note: written fresh, not adapted from the prototype's `bedrock_service.py` — the prototype's wrapper is a single-prompt classifier, not a `converse` + tool-use loop.*
- Port `pdf_extractor.py` from the prototype as the first tool. This is the only prototype file worth porting wholesale (see principle 3).

### Week 2 — Agent primitive and mock world

- `Agent` class: system prompt, tool list, model id, policy (token budget, max tool calls); tool-use loop; structured output parsing
- `Tool` interface: `name`, `description`, `parameters_schema`, `execute(params, context) → result`; tool registry
- Token counter (input + output, attributed to agent + step)
- **Mock world primitive.** In-memory virtual filesystem, virtual database, virtual messaging — same interface real connectors will implement. *Divergence: this is in the "Innovation" section of `ARCHITECTURE.md` and Phase 10.2 of `IMPLEMENTATION_PLAN.md` — pulled forward to be the testing primitive from day one.*
- Unit tests for agent loop using replay mode

### Week 3 — Workflow engine and the first end-to-end run

- Workflow definition loader (JSON), validator (DAG check, tool-reference resolution)
- DAG executor: **sequential only, no parallel, no conditional edges yet** (those land in Phase 1)
- Two step types: `deterministic` and `agentic`
- Workflow instance lifecycle: `pending → running → completed | failed`
- File-watch trigger built fresh as the first trigger plugin. The prototype's `folder_watcher.py` hardcodes `.pdf` and has no plugin shape — rewriting is faster than adapting.
- Postgres tables + Alembic migrations: `workflow_definitions`, `workflow_instances`, `step_executions`, `audit_log`
- **Audit log writes from the first run.** *Divergence: this is Phase 7.4 in `IMPLEMENTATION_PLAN.md` — audit logging cannot be retrofitted credibly, build it in now.*
- The invoice workflow from `ARCHITECTURE.md` runs end-to-end against a mock world

### Phase 0 success criteria

- Drop a PDF into a watched folder → workflow runs → results land in mock world → audit log shows every action
- The same workflow runs as a deterministic replay test in CI in <1 second
- A second engineer can read the code and add a new tool in under a day

---

## Phase 1 — Hardening (weeks 4–6)

**Goal:** the spine becomes safe to run real work on. Security, parallelism, basic memory, basic UI.

### Week 4 — Security spine

- OIDC integration: configure IdP, validate tokens, FastAPI middleware
- RBAC: roles (Admin, Designer, Operator, Viewer, Auditor), role-to-IdP-group mapping, permission checks on API endpoints
- **Agent capabilities.** Capability set per agent (tools, file ACLs, network ACLs, max tokens). Runtime enforcement before every tool call. Inheritance chain: system → workflow → step → runtime. *Order: do this before the executor gets more capable. It is much harder to add later.*

### Week 5 — Executor depth and memory

- Parallel step execution (independent steps run concurrently)
- Conditional edges (`condition` evaluated against accumulated context)
- Pause/resume, per-step retry, per-step + per-workflow timeouts
- **Agent memory — Phase A only** from `LEARNING_IMPLEMENTATION.md`: text files per agent identity, append-only, injected into system prompt at startup. No compaction, no contextual retrieval, no embeddings yet. *Divergence: defer Phases B–F (knowledge ingestion, retrieval layer, context assembly, active queries, learning feedback) until there is a real workload to tune against.*
- Second trigger: webhook. *Forces the trigger-plugin abstraction to be genuinely reusable rather than aspirational.*

### Week 6 — Static dashboard and operator workflow

- Angular dashboard (deliberately static):
  - Workflow definitions list
  - Workflow instance list with status
  - Instance detail with step trace (Summary level only — `D9` levels can come later)
  - Retry / pause / kill buttons
  - Audit log query view
- WebSocket for live status updates, built fresh with auth and per-user scoping. The prototype's pattern is `set[WebSocket]` broadcast-to-all with no auth — wrong shape for a multi-tenant system.
- Replay-mode CLI: re-run any past instance from its recording, useful for debugging and regression testing

### Phase 1 success criteria

- An authenticated operator can start, observe, retry, and kill workflows from the UI
- A capability violation is denied, logged, and visible in the audit trail
- A failed workflow can be replayed locally in <5 seconds for debugging
- Two workflows of different shapes (PDF processing, webhook-triggered) run concurrently without interfering

---

## Phase 2 — Breadth (weeks 7–10)

**Goal:** prove the architecture generalizes. Add the second trigger family, the connector framework, the orchestrator (passive only), and basic cost control.

### Week 7 — Connector framework + first real connectors

- Connector interface: `trigger_listen`, `trigger_poll`, `send`, `query`, `authenticate`, `health_check`
- Credential storage: AWS Secrets Manager (SaaS path), pluggable for self-hosted
- **First two connectors: generic webhook + S3.** *Order matches `INTEGRATIONS.md` Phase 1 — these are the most flexible and validate the framework without an OAuth detour.*
- *Defer M365/Google/Slack until a specific customer or demand pulls them in. Each is roughly a week of OAuth + Graph/Workspace API work.*

### Week 8 — Cost metering and budget enforcement

- Token attribution chain: step agent → workflow instance → workflow definition → system
- Real-time budget tracking at all three levels
- Budget enforcement actions: `notify`, `pause`, `escalate`. *Defer `degrade` (model fallback) until there is performance data to base it on.*
- Simple cost dashboard widget: spend by workflow, by day, by model

### Week 9 — Orchestrator (passive monitoring only)

- Background async monitoring loop: stuck workflows, error rate, queue depth, token burn rate
- Threshold-based alerts to the dashboard
- **No LLM-driven active reasoning yet.** *Divergence: `IMPLEMENTATION_PLAN.md` Phase 3.2 puts the orchestrator agent here. Defer the LLM brain until there are enough running workflows to give it something meaningful to reason about — otherwise it is solving toy problems and developing reflexes that will not generalize.*
- Escalation receiver: workflow agents can post escalation requests to a queue; humans resolve via the dashboard. *No automatic resolution yet.*

### Week 10 — Generality validation

- A third workflow of a deliberately different shape (e.g., scheduled report generation, or a contract-review flow that branches on document type)
- Export/import of workflow definitions (JSON/YAML)
- Tighten anything brittle. Document gaps for the next phase.

### Phase 2 success criteria

- Three workflows of distinct shapes run concurrently against a mix of local files and S3
- An operator gets paged when error rate spikes; the audit trail explains why
- The system stays under budget when a workflow misbehaves; the misbehaving instance is paused, others continue
- Total spend is visible per workflow per day

---

## Aggressively deferred (with rationale)

| Item | Source | Why defer |
|---|---|---|
| Knowledge ingestion pipeline (parse, chunk, contextualize, embed, index) | `LEARNING_IMPLEMENTATION.md` Phase B | 10 open research questions. Premature without real corpus + real queries. Unscoped pgvector work also adds operational complexity. |
| Retrieval layer, context assembly engine, active knowledge queries | `LEARNING_IMPLEMENTATION.md` Phases C–E | Depend on B. No agents yet need shared knowledge — agent memory is enough. |
| Learning feedback loop | `LEARNING_IMPLEMENTATION.md` Phase F | Needs a stable execution baseline to measure improvements against. |
| Generative UI (component spec generation, sandboxed Angular generation) | `GENERATIVE_UI.md` | High risk, high differentiation, but a static dashboard is enough for the first 6 months of internal use. Build it on top of a working system. |
| Cost Analyst agent (LLM-driven recommendations) | `ARCHITECTURE.md` D-section | Premature optimizer. Deterministic cost dashboards first; LLM analysis when there is data worth analyzing. |
| Agent-generated code for deterministic steps | `ARCHITECTURE.md` "Agent-Generated Code" | Compelling but a research project. Manually-authored deterministic functions cover the same ground for now. |
| Agent-to-agent escalation chain (LLM resolving escalations) | `ARCHITECTURE.md` D7 | Start with humans-resolve-everything; add LLM resolution after escalation patterns are observed. |
| Workflow graph mutations at runtime | `ARCHITECTURE.md` D8 | Designers can edit definitions explicitly. Runtime mutations are a power feature; defer until requested. |
| Model degradation on budget pressure | `ARCHITECTURE.md` D3 | Needs comparative quality data per model per task. Build that data first by metering, then act on it. |
| M365 / Google Workspace / Slack connectors | `INTEGRATIONS.md` Tier 1 | Each is roughly a week of OAuth + API work. Pull in the first one when a real workload demands it. |
| Self-hosted packaging (Helm chart, customer Bedrock) | `IMPLEMENTATION_PLAN.md` Phase 11.2 | SaaS first, as `VISION.md` and `ARCHITECTURE.md` D13 already specify. |
| Mobile / responsive layouts | `GENERATIVE_UI.md` | Internal-tool-grade desktop UI is enough. Responsive comes when an external customer needs it. |
| Cross-tenant aggregate learning (differential privacy) | `LEARNING_IMPLEMENTATION.md` research gap 10 | Speculative. Unblock a single tenant's workload first. |

---

## Risks and how this plan addresses them

| Risk | Mitigation |
|---|---|
| Layered build hides integration bugs until the end | Vertical slice in Phase 0 forces every layer to meet at week 3 |
| Agent tests are flaky and slow | Replay mode from week 1, mock world from week 2; integration tests are deterministic |
| Security retrofitted late = compliance debt | Capabilities + audit log built into the spine in Phase 0/1 |
| Infinite scope (every doc has more ideas than this plan executes) | Explicit deferred list with reasons; revisit after Phase 2 with real workload data |
| Premature investment in knowledge/learning systems with 10 open research questions | Phase A only (text-file memory); empirical iteration before Phase B |
| Overinvestment in UI before the core works | Static Angular dashboard in Phase 1; generative UI deferred entirely |

---

## Tradeoffs

This plan trades **breadth of demo** for **structural integrity**. At the end of three weeks, the visible artifact is one boring workflow processing PDFs into a mock world — not a flashy dashboard, not a generative UI, not a multi-agent orchestrator reasoning across the system. What it does have:

- A tested agent framework with replay-based tests
- A mock-world test harness reusable for every future feature
- An audit trail
- A clear next layer to build on

Every subsequent feature lands on tested foundations instead of stacking on unverified abstractions. The compounding return on this is large; the cost is that month one looks unimpressive.

The other tradeoff: some Phase 0/1 components will be rebuilt later (sequential executor → parallel executor; text-file memory → contextually-retrieved memory; static dashboard → generative UI). That is intentional. The cost of rebuilding a small, well-tested component is low. The cost of designing the final version up front, before the workload exists to inform it, is high.

---

## Re-evaluation points

- **End of Phase 0 (week 3):** does the spine feel right? Cheap to revise now; expensive later.
- **End of Phase 1 (week 6):** does the capability/permission model survive contact with real workflows? Adjust before connectors land.
- **End of Phase 2 (week 10):** which deferred items now have real signal pulling them in? Re-prioritize the next quarter against actual workload, not speculation.

---

## What this plan does *not* commit to

- A specific embedding model (Phase B research gap 2)
- A specific chunk size (research gap 1)
- A specific orchestrator escalation policy beyond "humans resolve"
- A connector for any specific SaaS beyond webhook + S3
- A specific UI for generative components

These are intentionally left open until Phase 2 produces enough real-workload signal to choose well.
