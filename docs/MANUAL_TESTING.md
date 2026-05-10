# Manual testing guide

A run-through of what to verify by hand on a fresh checkout. Complements the
automated suites — it doesn't replace them.

Each section says *what* you're testing, *why* it's worth doing manually, and
*exact commands*. Where the answer is "still automated tests", I send you back
to those rather than rebuild them as click-through.

Time budget: a quick smoke (pre-flight + sections 1–3) is ~10 minutes.
Everything in this doc end-to-end is ~30 minutes plus Bedrock latency.

---

## Pre-flight

```bash
cd /home/ubuntu/Dev/intelligent-workflow-application

# Backend deps
cd backend && uv sync && cd ..

# Frontend deps
cd frontend && npm ci && cd ..

# Postgres for the database-backed checks
docker compose up -d postgres
until docker compose ps postgres --format json | grep -q '"Health":"healthy"'; do sleep 2; done

# Sanity: AWS creds resolve (only needed for live-Bedrock checks)
aws sts get-caller-identity > /dev/null && echo "AWS creds OK"
```

Bedrock gates from `docs/BEDROCK_SETUP.md` should already be cleared — if not,
fix that first or skip the live checks below.

---

## 1. Backend boots and core endpoints respond

**What:** the FastAPI app starts cleanly, `/api/health` and `/metrics` answer.

**Why manual:** catches startup-time errors that unit tests skip (Postgres
connection, middleware order, route registration). The output also shows what
JSON log records actually look like — useful for tuning your tail.

**Run** — terminal A:

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
AUTH_MODE=dev \
uv run uvicorn workflow_platform.main:app --reload --port 8000
```

You should see one or two JSON log lines on startup, ending with
`Uvicorn running on http://0.0.0.0:8000`. Each line is one JSON object —
that's the `JsonFormatter` doing its job.

**Smoke** — terminal B:

```bash
# Health (no auth needed)
curl -s http://localhost:8000/api/health
# → {"status":"ok","version":"0.0.1"}

# Metrics (no auth needed; Prometheus text exposition format)
curl -s http://localhost:8000/metrics | head -20
# → comments + metric family declarations: workflow_runs_total, etc.

# An auth-gated endpoint (dev mode reads the X-Dev-User header)
curl -s -H 'X-Dev-User: alice' -H 'X-Dev-Groups: admins' \
  http://localhost:8000/api/workflows
# → []  (empty, no workflows imported yet)

# Same call without the header should fail
curl -i -s http://localhost:8000/api/workflows | head -3
# → HTTP/1.1 401 Unauthorized
```

**Pass when:** all three first commands return 200 with the expected shape;
the unauthenticated call returns 401.

---

## 2. Frontend boots and the three routes render

**What:** the Angular dashboard loads against the running backend.

**Why manual:** the route-loader test confirms components compile, but says
nothing about whether the layout is intact, the auth interceptor reaches the
backend, or the proxy passes traffic.

**Run** — terminal C:

```bash
cd frontend
npm start
```

Wait for `Angular Live Development Server is listening on localhost:4200`.

In a browser, go to `http://localhost:4200`. The dashboard redirects to
`/instances` by default.

**Click through:**

1. **Instances** (default) — empty list, but the page loads, the table header
   is present, refresh cadence indicator works.
2. **Workflows** — empty list, "no workflows imported" message.
3. **Set the dev identity** in the browser console:
   ```js
   localStorage.setItem('wp.user', 'alice')
   localStorage.setItem('wp.groups', 'admins')
   ```
   Refresh. Open DevTools → Network → click an `/api/workflows` request →
   confirm the request headers include `X-Dev-User: alice`.

**Pass when:** all three routes render, no console errors, `X-Dev-User` shows
up on the API calls.

---

## 3. Import a workflow definition through the API

**What:** the `/api/workflows/import` endpoint accepts the YAML at
`examples/pdf_classifier/workflow.yaml`.

**Why manual:** validates the round-trip from YAML → Pydantic → repo → list
endpoint → frontend. Auto-tests cover each leg in isolation.

```bash
# Designer or Admin role required.
curl -s -X POST \
  -H 'X-Dev-User: alice' \
  -H 'X-Dev-Groups: admins' \
  -H 'Content-Type: application/x-yaml' \
  --data-binary @examples/pdf_classifier/workflow.yaml \
  http://localhost:8000/api/workflows/import

# Confirm it shows up
curl -s -H 'X-Dev-User: alice' -H 'X-Dev-Groups: admins' \
  http://localhost:8000/api/workflows | jq '.[].id'
# → "pdf-classifier"
```

Refresh the dashboard's **Workflows** tab — `pdf-classifier` should appear.

**Pass when:** import returns 200 / 201; the workflow lists in both the API
and the dashboard.

---

## 4. PDF classifier end-to-end via replay (no AWS)

**What:** the example workflow runs through `pdf_extract` → agentic classify
→ `route_by_classification` against fixtures.

**Why manual:** confirms the example actually works as documented, and you'll
read the audit log + step output as text — easier to spot anomalies than in
test assertions.

```bash
cd backend

# Drop a sample PDF (or use any PDF you have)
python - <<'PY'
import fitz
doc = fitz.open()
p = doc.new_page()
p.insert_text((72, 72), "INVOICE\nVendor: Acme\nTotal: $99.99\n")
doc.save("../examples/pdf_classifier/sample_inbox/test.pdf")
doc.close()
PY

# Run via the replay CLI (uses MockWorld, in-memory repos, no Postgres impact).
# This requires recordings; see "limitations" below.
uv run python tools/replay.py \
  --definition ../examples/pdf_classifier/workflow.yaml \
  --trigger '{"file_path":"../examples/pdf_classifier/sample_inbox/test.pdf"}' \
  --recordings-dir tests/recordings || echo "no recordings yet"
```

**Limitation:** `replay.py` requires Bedrock recordings keyed on the exact
prompt hash; we don't have any pre-recorded for this workflow yet. To create
them, run the same command with `BEDROCK_MODE=record` env var and AWS creds.

**Practical alternative — run the pytest that drives the same flow:**

```bash
uv run pytest tests/test_pdf_classifier_workflow.py -v
```

That's the canonical "does this work?" check. Eight tests pass in ~3 s using
a faked Bedrock response and a real PyMuPDF extraction.

**Pass when:** the pytest is green and you've eyeballed the file copying into
`examples/pdf_classifier/output/<category>/`.

---

## 5. Live Bedrock smoke through pytest

**What:** the three end-to-end live tests in `backend/tests/test_smoke_live.py`
run against real Claude Haiku 4.5.

**Why manual:** real Bedrock can drift in ways no replay test catches —
region access, inference profile health, quota.

**Cost:** ~$0.0001 / run (a few cents per month if you run it occasionally).

```bash
cd backend
BEDROCK_LIVE=1 uv run pytest -m live -v
```

**Expected output:**

```
tests/test_smoke_live.py::test_bedrock_converse_direct PASSED
tests/test_smoke_live.py::test_agent_run_no_tools PASSED
tests/test_smoke_live.py::test_workflow_engine_end_to_end PASSED
3 passed in ~5s
```

If a test fails: `backend/tools/smoke_live.py` is the diagnostic version — it
prints cause + action lines mapped to gates in `docs/BEDROCK_SETUP.md`. Run
that to see which gate regressed.

**Pass when:** all three tests green.

---

## 6. Postgres integration tests

**What:** the workflow engine actually persists state through Postgres
(workflow_definitions, workflow_instances, step_executions, audit_log) and
re-reads it across sessions.

**Why manual:** unit tests stub the repos. This is the only check that the
SQLAlchemy models, async session handling, and JSONB serialization line up.

```bash
cd backend
TEST_DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
  uv run pytest -m integration -v
```

**Expected:**

```
tests/test_postgres_repositories.py::test_workflow_persists_through_postgres PASSED
tests/test_postgres_repositories.py::test_definition_round_trip_via_postgres PASSED
2 passed in ~3s
```

**Pass when:** both tests green.

---

## 7. Cost report endpoints

**What:** `/api/cost/by-workflow`, `/api/cost/by-model`, `/api/cost/by-day`
respond with the right shape and aggregate over real step executions.

**Why manual:** integration between the Bedrock pricing table, engine cost
attribution, and the report service. Easy to verify by eye after running a
live workflow.

```bash
H='X-Dev-User: alice'
G='X-Dev-Groups: admins'

# Empty until something has run
curl -s -H "$H" -H "$G" http://localhost:8000/api/cost/by-workflow | jq
curl -s -H "$H" -H "$G" http://localhost:8000/api/cost/by-model | jq
curl -s -H "$H" -H "$G" http://localhost:8000/api/cost/by-day | jq

# Optional: filter by time
curl -s -H "$H" -H "$G" \
  "http://localhost:8000/api/cost/by-day?since=2026-05-01T00:00:00Z" | jq
```

To get non-zero data, run one of the live-Bedrock paths against the same
Postgres database (e.g. modify `tools/smoke_live.py` to use Postgres repos
and run it; or write a one-shot script — see "Open gaps" below).

**Pass when:** endpoints return 200 with the expected JSON keys
(`workflow_id`, `total_tokens`, `total_cost_usd`, etc.) — empty arrays are a
valid pass on a fresh DB.

---

## 8. Auth + RBAC behavior

**What:** dev mode picks up identity from `X-Dev-User` and `X-Dev-Groups`;
role gates on audit / lifecycle / import endpoints work as advertised.

**Why manual:** spot-check the matrix. Auto-tests cover the matrix entry by
entry; manual is for "did I configure the right roles for what I'm doing?"

```bash
# Viewer can read but not retry
curl -i -s -X POST -H 'X-Dev-User: bob' -H 'X-Dev-Groups: viewers' \
  http://localhost:8000/api/workflow-instances/no-such-id/retry | head -3
# → HTTP/1.1 403 Forbidden  (or 404 if you flip the header to admins, since
#    the instance id doesn't exist)

# Admin can hit it (404 instead of 403 means the role check passed)
curl -i -s -X POST -H 'X-Dev-User: alice' -H 'X-Dev-Groups: admins' \
  http://localhost:8000/api/workflow-instances/no-such-id/retry | head -3
# → HTTP/1.1 404 Not Found

# Auditor can read /api/audit, viewer cannot
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'X-Dev-User: a' -H 'X-Dev-Groups: auditors' \
  http://localhost:8000/api/audit
# → 200

curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'X-Dev-User: b' -H 'X-Dev-Groups: viewers' \
  http://localhost:8000/api/audit
# → 403
```

The five roles (Admin, Workflow Designer, Operator, Viewer, Auditor) and the
group-name → role mapping are in `backend/src/workflow_platform/auth/rbac.py`.

**Pass when:** the four expected status codes match (403 / 404 / 200 / 403).

---

## 9. Metrics — Prometheus exposition

**What:** `/metrics` updates as workflows run.

**Why manual:** auto-tests verify counter increments in isolation. Watching
the metric body change while a workflow runs is the easiest way to confirm
the wire-up actually fires.

```bash
# Snapshot before and after a run
curl -s http://localhost:8000/metrics > /tmp/before.txt
BEDROCK_LIVE=1 uv run pytest tests/test_smoke_live.py::test_workflow_engine_end_to_end -q
curl -s http://localhost:8000/metrics > /tmp/after.txt

# Show new / changed metric lines
diff /tmp/before.txt /tmp/after.txt | head -40
```

Note: the live test above runs in its own process with a separate
`PrometheusMetrics` registry, so the running server's `/metrics` won't change
unless you arrange for the workflow to fire **inside** the running server.
Use this snippet to do that — see "Open gaps" for the longer story.

**Pass when:** the diff shows non-zero `workflow_runs_total`,
`workflow_step_runs_total`, `workflow_agent_tokens_total`,
`workflow_bedrock_cost_usd_total` (assuming you ran a workflow inside the
server process — see gaps section).

---

## 10. JSON logging in stdout

**What:** the FastAPI process emits structured JSON log lines.

**Why manual:** five-second human check that the formatter actually wins
over default uvicorn / FastAPI text formatters (it does, because
`configure_logging()` runs at module import).

In terminal A (where the server is running), look at any line:

```
{"ts": "2026-05-10T...", "level": "INFO", "logger": "uvicorn.access", "msg": "127.0.0.1:54732 - \"GET /api/health HTTP/1.1\" 200"}
```

If a request shows up as plain text instead of JSON, `configure_logging` lost
the race against another logger config (rare; means we should call it earlier
in `main.py`).

**Pass when:** every line is parseable JSON with `ts`, `level`, `logger`,
`msg` keys.

---

## 11. Frontend smoke tests

```bash
cd frontend
npm test         # vitest run, ~4s
npm run build    # AOT compile, catches template errors, ~6s
```

15 tests + a clean build. Same checks CI runs.

**Pass when:** vitest reports `Tests 15 passed (15)` and the build prints
`Application bundle generation complete`.

---

## Open gaps — things you can't yet test manually

These are real, and worth knowing before you go looking:

1. **No "fire workflow" API.** Workflows only enter the engine through
   triggers (filesystem, webhook, schedule). The webhook trigger has an HTTP
   endpoint but needs a `WebhookRegistry` registration that nothing in
   `main.py` populates today. So the dashboard sits empty unless you fire a
   workflow into the engine yourself.

   **Workaround** — paste this into a Python REPL with the venv activated:

   ```python
   import asyncio, os
   os.environ["DATABASE_URL"] = "postgresql+asyncpg://workflow:workflow@localhost:5432/workflow"
   from workflow_platform.bedrock import BedrockClient, BedrockMode
   from workflow_platform.engine import (
       WorkflowEngine, ToolCatalog, default_function_registry,
   )
   from workflow_platform.persistence.db import make_engine, make_session_factory
   from workflow_platform.persistence.postgres import postgres_repositories
   from workflow_platform.workflow import load_definition_from_file
   from workflow_platform.world import real_world

   async def fire():
       db = make_engine(os.environ["DATABASE_URL"])
       repos = postgres_repositories(make_session_factory(db))
       defn = load_definition_from_file("examples/pdf_classifier/workflow.yaml")
       await repos.definitions.save(defn)
       engine = WorkflowEngine(
           repositories=repos,
           functions=default_function_registry(),
           tools=ToolCatalog(),
           bedrock=BedrockClient(mode=BedrockMode.LIVE, region="us-east-1"),
           world=real_world(),
       )
       inst = await engine.run(defn, trigger_payload={
           "file_path": "/abs/path/to/some.pdf",
       })
       print(inst.id, inst.state.value)
       await db.dispose()

   asyncio.run(fire())
   ```

   Refresh the dashboard's **Instances** tab — your run is now visible with
   audit log, step outputs, and `pause` / `kill` buttons.

   This belongs in a small `tools/fire.py` once we hit the manual-test loop
   often enough to justify it. Worth holding off until that pain is real.

2. **Filesystem trigger isn't wired into the running server.** `watchdog` is
   the real watcher, and the unit tests prove it works, but the FastAPI
   process doesn't start any triggers — the engine lives there but nothing
   feeds it. Dropping a PDF in the inbox folder does nothing today.

3. **Schedule trigger same story.** Cron fires inside a test, not in the
   server process.

4. **No frontend WebSocket subscription yet.** The dashboard polls every
   3–5 s; the `/ws/events` endpoint is implemented but unused. So you won't
   see real-time updates — you'll see fresh data on the next poll tick.

5. **The deployed Terraform stack** is unverified end-to-end. The IaC under
   `infra/` is `terraform validate`-clean, but no one's run `terraform apply`
   yet. The whole "does the deployed system work?" question is open.

Each of these is a clearly-scoped follow-up, not a deep design issue. Items 1
and 2 share the same fix: a small entry point that loads workflows from a
configured directory, registers their triggers against the in-process engine,
and runs forever. Lands when there's a workflow worth running for hours.

---

## When this doc goes stale

Update it when any of the following changes:

- A new endpoint is added to `backend/src/workflow_platform/api/`
- A new test marker is registered in `pyproject.toml`
- A new lazy-loaded route lands in `frontend/src/app/app.routes.ts`
- One of the "open gaps" above gets closed (delete that bullet, add a new
  numbered section)
- Auth or role mapping changes in `backend/src/workflow_platform/auth/`
