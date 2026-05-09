# Intelligent Workflow Platform

An AI-powered workflow platform that you talk to, not configure. See `VISION.md`.

## Status

Phase 0, Week 1 — foundations. The system runs no real workflows yet. This week landed:

- Monorepo scaffold (`backend/`, `frontend/`, `shared/`, `tools/`)
- Python backend (`uv`-managed, Python 3.12, FastAPI, async)
- Bedrock wrapper with record/replay built in (deterministic agent tests from day zero)
- PDF extractor ported from the prototype as the first `Tool`
- CI skeleton (GitHub Actions: ruff + mypy + pytest)
- Local dev via Docker Compose (Postgres + backend; frontend lands in Phase 1)

See `BUILD_PLAN.md` for the full execution sequence and `IMPLEMENTATION_PLAN.md` for layer-by-layer scope.

## Quick start

Prerequisites: Python 3.12, [`uv`](https://docs.astral.sh/uv/), Docker, and (for OCR) `tesseract` and `poppler-utils` on the system PATH.

```bash
# Install backend deps + create the venv
cd backend && uv sync

# Run the test suite (no Bedrock credentials required — replay mode is the default)
uv run pytest

# Lint + type-check
uv run ruff check .
uv run mypy src

# Boot the backend locally
uv run uvicorn workflow_platform.main:app --reload
# → GET http://localhost:8000/api/health
```

For Postgres + backend together:

```bash
docker compose up --build
```

## Repository layout

```
.
├── backend/           Python backend (FastAPI, async)
│   ├── src/workflow_platform/
│   │   ├── bedrock/   Bedrock client with record/replay
│   │   ├── tools/     Tool interface + concrete tools (pdf_extract first)
│   │   └── main.py    FastAPI app
│   └── tests/
├── frontend/          Angular UI (placeholder until Phase 1)
├── shared/            Cross-cutting schemas (placeholder)
├── tools/             Dev/ops scripts (placeholder)
├── ARCHITECTURE.md    Design and decisions (D1–D13)
├── BUILD_PLAN.md      Recommended execution sequence
├── IMPLEMENTATION_PLAN.md  Layer-by-layer scope
└── VISION.md          Product vision and anti-goals
```

## Tooling choices

| Concern | Choice | Why |
|---|---|---|
| Package manager | `uv` | Fast, lockfile, single tool for venv + deps + lockfile |
| Lint + format | `ruff` | One tool replacing black + isort + flake8 |
| Type checker | `mypy` | Standard, mature, integrates with `ruff` |
| Web framework | FastAPI + `uvicorn` | Async-native, Pydantic-integrated, validated in the prototype |
| Async DB (later) | SQLAlchemy 2.0 + `asyncpg` + Alembic | Async-throughout, mature migrations |
| Bedrock SDK | `boto3` | Official AWS SDK; sync calls wrapped via `asyncio.to_thread` |
| Tests | `pytest` + `pytest-asyncio` | Standard |

These can be revisited if they don't fit. None of them are load-bearing on the architecture.
