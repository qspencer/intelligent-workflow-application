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

The repository is on `main`. Phase 0 / Week 1 is complete: repo scaffold, `uv`-managed Python 3.12 backend with FastAPI, Bedrock wrapper with live/record/replay modes, Tool ABC, `pdf_extract` ported from the prototype, 14 tests passing, ruff + mypy strict + GitHub Actions CI, backend Dockerfile with tesseract + poppler, docker-compose with Postgres staged for Week 3.

Next up per `docs/BUILD_PLAN.md`: **Week 2 — Agent class with tool-use loop, and the mock-world testing primitive.** Don't build out of order. When asked to do work that belongs to a later phase, push back and explain why it's deferred.

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
