# Intelligent Workflow Platform

An AI-powered workflow platform you talk to, not configure: describe a workflow in
plain English, refine it on a visual canvas, and run it with full cost, capability,
and audit visibility. PDF/document processing was the first use case; the
architecture is trigger-agnostic. See `docs/VISION.md` for the product vision.

## What it does today

- **Describe → draft → refine → run.** A natural-language description becomes a
  draft workflow (one Bedrock call, constrained to real building blocks), opened on
  a React Flow canvas with catalog pickers, inline structural validation, and
  plain-language step inspectors.
- **Agentic + deterministic steps.** Workflows are DAGs mixing LLM agent steps
  (Claude via AWS Bedrock, tool-use loop, per-step token budgets) with
  deterministic functions — parallel branches, conditional edges, retries,
  timeouts, pause/resume, fork-from-step.
- **The trust wedge.** Per-run cost estimates and a live budget meter, per-step
  capability boundaries (which tools an agent may use and why), an
  explain-this-run forensic view, and a sandboxed dry-run ("mock the world, keep
  the brain") — governance no comparable GUI surfaces.
- **Triggers**: manual, filesystem watch, cron/interval schedule, webhook (HMAC
  verification via `X-Hub-Signature-256`), Gmail inbox polling.
- **Connectors + tools**: PDF extraction/OCR, file I/O, outbound HTTP, S3, Gmail
  (send/label), browser automation (Playwright). Capability allowlists gate what
  each agent step may touch.
- **Operations**: immutable audit log on every transition and tool call, cost
  reports (by workflow/model/day), threshold-based monitoring alerts, Prometheus
  `/metrics`, structured JSON logs, batch runs, OIDC auth + 5-role RBAC (dev-mode
  bypass for local work).
- **Bundled examples** under `examples/`: PDF classifier, invoice extraction,
  GitHub PR triage, research-paper triage, email triage, webhook echo, scheduled
  health report, RPA-challenge OCR — all offered as templates in the GUI.

## Status

Phases 0–2 (spine → security/executor depth → connectors, cost, monitoring) and
Phase 3 (the workflow canvas, cuts C1–C8) are complete. Every agent test runs
deterministically against recorded Bedrock responses and a mock world — no AWS
credentials needed for the default suite. What's next is demand-driven: the
deferred epics (sub-workflows, real-time collaboration, AI-assisted layout,
delivery surfaces, version history) each start when a concrete need pulls them in.

The living status lives in `CLAUDE.md`; the design corpus is under `docs/`
(indexed there). Run `git log --oneline` for ground truth.

## Quick start

Prerequisites: Python 3.12, [`uv`](https://docs.astral.sh/uv/), Node 22+, Docker,
and (for OCR) `tesseract` + `poppler-utils` on the system PATH.

```bash
# Backend — install, test, boot (replay Bedrock by default; no AWS needed)
cd backend && uv sync
uv run pytest                 # ~700 tests, <30s
AUTH_MODE=dev uv run uvicorn workflow_platform.main:app --reload --port 8001

# Frontend — the canvas GUI (proxies /api + /ws to :8001)
cd frontend && npm ci
npm run dev                   # → http://localhost:4200
```

For the full-feature local stack in one command (Postgres + migrations + live
Bedrock + all triggers + Gmail-from-`.secrets` + dev auth):

```bash
./scripts/run-local-be.sh     # backend only; start the frontend separately
```

`docker compose up --build` brings up Postgres + the backend container.
`docs/MANUAL_TESTING.md` is the full operator playbook (Part A: API, Part B: a
tick-the-box GUI walkthrough).

### Test suites

| Suite | Command | Notes |
|---|---|---|
| Backend unit | `cd backend && uv run pytest` | Default; replay Bedrock, in-memory repos |
| Frontend unit | `cd frontend && npm test` | Vitest + Testing Library |
| E2E + a11y | `cd frontend && npm run test:e2e` | Playwright + axe; self-contained servers |
| API contract | `SCHEMA_TESTS=1 uv run pytest -m schema` | Schemathesis over the OpenAPI schema |
| Postgres | `TEST_DATABASE_URL=… uv run pytest -m integration` | Real DB round-trip |
| Live drift | `BEDROCK_LIVE=1` / `GMAIL_LIVE=1` / `BROWSER_LIVE=1` | Real services; weekly CI cron |

## Repository layout

```
.
├── backend/           Python 3.12 backend (FastAPI, async-throughout)
│   ├── src/workflow_platform/
│   │   ├── engine/        Workflow engine (DAG executor, functions, tool catalog)
│   │   ├── agent/         Bedrock tool-use agent loop
│   │   ├── workflow/      Definitions (Pydantic), topology, validation
│   │   ├── triggers/      Filesystem / schedule / webhook / Gmail-poll
│   │   ├── connectors/    Webhook, S3, Gmail, browser + secret stores
│   │   ├── tools/         Agent-callable tools (pdf_extract, file I/O, email, …)
│   │   ├── api/           HTTP + WebSocket routes
│   │   ├── auth/          OIDC validation, RBAC, dev-mode bypass
│   │   ├── security/      Capability model (layered allowlist intersection)
│   │   ├── cost/          Pricing table, attribution, reports
│   │   ├── monitoring/    Threshold-based background alerts
│   │   ├── persistence/   Repos: in-memory (tests) + Postgres (SQLAlchemy/Alembic)
│   │   └── observability/ JSON logging, Prometheus metrics
│   ├── tests/         ~700 tests (replay-mode default) + gated live suites
│   └── tools/         Operator CLIs (fire, replay, smoke_live, smoke_gmail, …)
├── frontend/          React + Vite canvas GUI (React Flow), Vitest + Playwright
├── examples/          Bundled example workflows (also the GUI's template gallery)
├── scripts/           run-local-be.sh — one-command full-feature local backend
├── infra/             Terraform (validate-clean; not yet applied)
├── docs/              Design corpus + operator guides (indexed in CLAUDE.md)
└── CLAUDE.md          Operating manual + living status (auto-loaded by Claude Code)
```

## Key docs

- `docs/VISION.md` — product vision and anti-goals
- `docs/ARCHITECTURE.md` — decisions D1–D13, agent hierarchy, security model
- `docs/BUILD_PLAN.md` — the execution sequence that got here
- `docs/CANVAS_ROADMAP.md` — the GUI cut-by-cut roadmap (C1–C8 ✅, epics E1–E5)
- `docs/MANUAL_TESTING.md` — the single manual-test plan (API + GUI)
- `docs/TESTING.md` — automated-test inventory + future investments
- `docs/BEDROCK_SETUP.md` — AWS onboarding gates for live runs

The full index (~25 docs) is in `CLAUDE.md`.

## Tooling choices

| Concern | Choice |
|---|---|
| Package manager | `uv` (backend), `npm` (frontend) |
| Lint + format + types | `ruff` + `mypy --strict` (CI-enforced) |
| Web framework | FastAPI + `uvicorn`, async-throughout |
| Database | SQLAlchemy 2.0 async + `asyncpg` + Alembic (Postgres); in-memory repos for tests |
| LLM | Claude via AWS Bedrock (`boto3`, record/replay wrapper) |
| Frontend | React 18 + Vite + React Flow (`@xyflow/react`) |
| Tests | pytest / Vitest / Playwright + axe / schemathesis |
| IaC | Terraform under `infra/` |

Non-load-bearing choices — revisit with cause (see `CLAUDE.md` for the rules).
