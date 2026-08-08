---
description: Run the CI-equivalent local checks — lint, format, mypy strict, pytest, verification-index, and the dependency audits
---

Run these concurrently — issue all Bash tool calls in a single message so they
execute in parallel:

- `cd backend && uv run ruff check .`
- `cd backend && uv run ruff format --check .`
- `cd backend && uv run mypy src tests`
- `cd backend && uv run pytest`
- `cd backend && uv run python tools/check_verification_index.py`
- `cd backend && uv run --with pip-audit pip-audit`
- `cd frontend && npm audit --audit-level=high`

Report results in a brief table: `check | passed/failed | notes`. On any
failure, include only the most relevant 5–10 lines of output beneath the table —
not the full log. If everything passes, the table is enough; no narrative needed.

## Why the last three are here (added 2026-08-08)

They were CI-only, so "local green" repeatedly meant less than it looked:

- **verification-index** — catches a doc citing a test/file that no longer
  exists. A TG1 rename broke two citations and it went unnoticed for days,
  because the audit steps failed first and the job never reached this check.
- **pip-audit / npm audit** — a newly-published advisory turns CI red with **no
  code change**. Happened twice in two days (`cryptography` PYSEC-2026-3552 on
  the AES-GCM path; then `nanoid` GHSA-2v37-7h3g-55p8). Running them locally
  means the signal arrives before the push, not after.

**Audits failing is not necessarily your change.** Check whether the advisory
is new — `main` may be affected too and merely not have re-run. Verify against
the lockfile rather than trusting a green checkmark on an older run.

## Not covered locally

`pytest` here skips the Postgres / Bedrock / Gmail / browser / schemathesis
suites (they need `TEST_DATABASE_URL`, `BEDROCK_LIVE=1`, etc.), and the e2e +
schema CI jobs are not run. **A green run here is not proof the deployed box
works** — after any schema change, apply migrations and exercise a real run
(see `NEXT_STEPS.md` G26.1).
