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

Next up per `docs/BUILD_PLAN.md`: **Phase 1 / Week 4 — Security spine.** OIDC integration, RBAC roles + IdP-group mapping, agent capabilities (tools, file ACLs, network ACLs, max tokens) with runtime enforcement before every tool call. *Capabilities are not retrofittable; they land before the executor gets more capable.* Don't build out of order; if asked for a later-phase feature, push back and explain why it's deferred.

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
