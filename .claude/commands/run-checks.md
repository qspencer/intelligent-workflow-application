---
description: Run ruff lint, ruff format check, mypy strict, and pytest against the backend in parallel
---

Run all four backend checks concurrently — issue all Bash tool calls in a single message so they execute in parallel:

- `cd backend && uv run ruff check .`
- `cd backend && uv run ruff format --check .`
- `cd backend && uv run mypy src tests`
- `cd backend && uv run pytest`

Report results in a brief 4-row table: `check | passed/failed | notes`. On any failure, include only the most relevant 5–10 lines of output beneath the table — not the full log. If everything passes, the table is enough; no narrative needed.
