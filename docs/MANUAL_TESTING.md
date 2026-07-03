# Manual testing guide

A run-through of what to verify by hand on a fresh checkout. Complements the
automated suites — it doesn't replace them. This is the **single** manual-test
plan; it covers both the backend/API (Part A) and the full canvas GUI through
C7 (Part B).

- **Part A — Backend & API (operator playbook).** Sections 1–11: backend boot,
  imports, the example workflows, live services, Postgres, cost, RBAC, metrics,
  logging, frontend smoke. Each says *what* you're testing, *why* it's worth
  doing manually, and *exact commands*. Where the answer is "still automated
  tests", I send you back to those rather than rebuild them as click-through.
- **Part B — Canvas GUI walkthrough (C5–C7).** A tick-the-box, follow-along
  click-through of the friendly shell (C5), the trust wedge (C6), and authoring
  parity (C7). See `docs/CANVAS_ROADMAP.md` for what each cut is.

Time budget: a quick smoke (pre-flight + sections 1–3) is ~10 minutes. Part A
end-to-end is ~40 minutes plus live-service latency (the browser end-to-end live
test alone is ~5 minutes). The Part B GUI walkthrough is ~20 minutes (a few
cents of Bedrock for the dry-run / scaffold / run cases).

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

# Part A — Backend & API (operator playbook)

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
uv run uvicorn workflow_platform.main:app --reload --port 8001
```

You should see one or two JSON log lines on startup, ending with
`Uvicorn running on http://0.0.0.0:8001`. Each line is one JSON object —
that's the `JsonFormatter` doing its job.

> **Gmail needs one more env var.** The `email_send` / `email_label_apply` tools
> and the `email` (Gmail-poll) trigger stay off — and you'll see a `Gmail poll disabled …
> Client credentials not in SecretStore` warning — unless
> **`WORKFLOW_PLATFORM_GMAIL_ACCOUNT=<address>`** is set, *even when the creds
> exist under `.secrets/gmail/<address>/`*. That env var is what triggers the
> on-disk-`.secrets`→`os.environ` seeding the `EnvSecretStore` reads from. Add
> `WORKFLOW_PLATFORM_GMAIL_ACCOUNT=intelligent.workflow.engine@quentinspencer.com`
> to the command above, or just use **`./scripts/run-local-be.sh`** (from the repo
> root), which sets it and verifies the credential files for you. Creds setup
> itself is Gates 3–4 in `docs/EMAIL_CONNECTOR_PLAN.md`
> (`uv run python tools/gmail_auth.py --account <address>`).

**Smoke** — terminal B:

```bash
# Health (no auth needed)
curl -s http://localhost:8001/api/health
# → {"status":"ok","version":"0.0.1"}

# Metrics (no auth needed; Prometheus text exposition format)
curl -s http://localhost:8001/metrics | head -20
# → comments + metric family declarations: workflow_runs_total, etc.

# An auth-gated endpoint (dev mode reads the X-Dev-User header)
curl -s -H 'X-Dev-User: alice' -H 'X-Dev-Groups: admins' \
  http://localhost:8001/api/workflows
# → []  (empty, no workflows imported yet)

# Same call without the header should fail
curl -i -s http://localhost:8001/api/workflows | head -3
# → HTTP/1.1 401 Unauthorized
```

**Pass when:** all three first commands return 200 with the expected shape;
the unauthenticated call returns 401.

---

## 2. Frontend boots and the routes render

**What:** the React dashboard (Vite) loads against the running backend.

**Why manual:** the App-routing test confirms routes mount, but says nothing
about whether the layout is intact, the dev-auth headers reach the backend,
or the Vite proxy passes traffic.

**Run** — terminal C:

```bash
cd frontend
npm start        # Vite dev server (alias: npm run dev)
```

Wait for `VITE v5… ready` and `Local: http://localhost:4200/`. The dev
server proxies `/api` and `/ws` to the backend on `:8001`.

In a browser, go to `http://localhost:4200`. The app lands on the
**Automations** home (the C5 friendly shell — see `docs/CANVAS_ROADMAP.md`).
The top nav shows **Automations** and **Templates**; the developer console
(Instances / Workflows / Cost) is hidden behind a **Developer** toggle.

**Click through:**

1. **Automations** (default, `/`) — heading "Your automations". Empty state
   ("No automations yet.") on a fresh backend, or a card grid once workflows
   exist. Not a list of instances.
2. **Templates** — a card grid of the bundled example workflows, each with a
   step count and a friendly trigger label ("On a webhook", "On a schedule", …).
3. **Developer toggle** (top-right, "Developer: off") — click it and the
   **Instances / Workflows / Cost** nav links appear; the choice persists across
   reload (localStorage). With it on:
   - **Instances** — empty list, re-polls every few seconds.
   - **Workflows** — the developer table; "No workflows registered yet." when empty.
   - **Cost** — three empty tables (by workflow / model / day).
   Deep links (`/instances`, `/workflows`, `/cost`) work even with the toggle
   off — it governs nav visibility only.
4. **Set the dev identity** — easiest via the **"Acting as"** dropdown in the
   header (writes `wp.groups` to localStorage and reloads). Or in the console:
   ```js
   localStorage.setItem('wp.user', 'alice')
   localStorage.setItem('wp.groups', 'admins')
   ```
   Refresh. Open DevTools → Network → click an `/api/workflows` request →
   confirm the request headers include `X-Dev-User: alice`.

**Pass when:** the home renders at `/`, the Developer toggle reveals/hides the
console and persists, no console errors, and `X-Dev-User` shows up on the API
calls.

> For an exhaustive, tick-the-box click-through of the GUI surfaces — C5 front
> door, C6 trust wedge, C7 authoring — see **Part B** below.

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
  http://localhost:8001/api/workflows/import

# Confirm it shows up
curl -s -H 'X-Dev-User: alice' -H 'X-Dev-Groups: admins' \
  http://localhost:8001/api/workflows | jq '.[].id'
# → "pdf-classifier"
```

Refresh the dashboard's **Workflows** tab — `pdf-classifier` should appear.

**Pass when:** import returns 200 / 201; the workflow lists in both the API
and the dashboard.

---

## 3b. The workflow canvas (view / run / watch / edit)

**What:** the Zapier-style canvas at `/canvas/:id` — the Phase 3 surface
(`docs/WORKFLOW_CANVAS.md`). Four behaviors layer up: read-only view (C1),
live status when following a run (C2), run-from-form (C3), and edit mode (C4).

**Why manual:** the canvas is the product's intended differentiator and is
heavily interactive (select / drag / connect / save). Headless tests cover
the flows, but a human should eyeball layout and feel.

Prereq: at least one workflow imported (section 3). Open it by clicking its
**card** on the Automations home, or go straight to
`http://localhost:4200/canvas/pdf-classifier`. (Creating from a template or
"Create" on the home also lands here directly, in edit mode — `?edit=1`.)

**C1 — read-only view (no AWS):**
- Renders top-down: a trigger node ("When a file arrives") plus one node per
  step, with the `classify` → `route` / `evaluate` diamond. Agentic nodes are
  purple (🧠), deterministic blue (⚙️).
- Click any node → the right **Inspector** explains it in plain language
  (model, instructions, tools, limits — no YAML). Header pill: **"View only"**.

**C3 + C2 — run from a form, watch it live:** to exercise this without AWS,
import this trivial `noop` workflow (turn the **Developer** toggle on, then
paste into the Workflows page "Import workflow" dialog, YAML format) so it runs
with no Bedrock:

```yaml
id: manual-noop
name: Manual Noop
trigger:
  type: manual
  example_payload:
    note: "hello from manual testing"
steps:
  - { id: step-one, type: deterministic, function: noop, config: {} }
edges: []
policies: {}
```

Open its canvas → click **Run** (Admin/Operator) → a **form** generated from
`example_payload` opens (a `note` field — no JSON). Click Run → the canvas
flips to a **live view** (`?instance=…`): the node colors to "✓ Done" and a
footer shows the instance state + "N of M steps". For a real agentic run, do
the same on `pdf-classifier` with live Bedrock (section 5) and watch nodes go
"Running…" → "Done". The **"View on canvas"** link on any `/instances/:id`
page opens this same live view.

**C4 — edit mode (no AWS), Admin/Designer only:**
- Click **Edit** on a workflow's canvas — the button only appears for Admin /
  Designer (flip "Acting as" to Operator and confirm it disappears). Header
  pill: **"Editing"**, with Save / Discard.
- Select the agentic step → change **model** or **max_total_tokens**. Save is
  disabled until something changes.
- Palette ("+ Function step" / "+ AI step") adds a node; select a node and use
  the Inspector's **Connections** ("+ Connect to…") to wire it (or drag
  between node handles); **Delete step** removes a node + its edges.
- The Inspector's fields are now **catalog pickers** (C7.2), not raw text: the
  trigger **type**, the deterministic **function**, and the agent **tools** are
  selects / a grouped checkbox list with descriptions; the agent **goal** is the
  reframed "Instructions for the AI" field with help + examples (C7.4). Invalid
  edits light up **red node borders** + a findings panel and block Save (C7.3).
  These have their own detailed walkthrough in **Part B**.
- **Save** round-trips through `/api/workflows/import` then re-fetches. Confirm
  it stuck:
  ```bash
  curl -s -H 'X-Dev-User: alice' -H 'X-Dev-Groups: admins' \
    http://localhost:8001/api/workflows/<id> | jq '.steps'
  ```

**Pass when:** the graph renders correctly; the Run form generates from the
example payload; a deterministic run shows live "Done" status; an edit saves +
persists; and the Edit button is hidden for non-Designer roles.

> **The trust-wedge (C6) and authoring (C7) surfaces — Test/dry-run, cost
> estimate, capability boundaries, explain-this-run, the catalog pickers,
> "Describe it", and inline validation — get a full tick-the-box walkthrough in
> Part B.** This section stays the quick "canvas basics" operator check.

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

That's the canonical "does this work?" check. 15 tests pass in ~3 s using
a faked Bedrock response and a real PyMuPDF extraction.

**Pass when:** the pytest is green and you've eyeballed the file copying into
`examples/pdf_classifier/output/<category>/`.

---

## 4b. Webhook example (real backend, no Bedrock recordings)

**What:** the `webhook_echo` example workflow under
`examples/webhook_echo/`. Smallest possible "does the webhook trigger
work?" check.

**Why manual:** exercises the orchestrator → webhook registry →
`POST /api/triggers/webhook/<id>` → engine path that automated tests
hit in isolation.

Start the backend with the orchestrator pointing at `examples/`:

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
WORKFLOW_DEFINITIONS_DIR=../examples \
AUTH_MODE=dev \
  uv run uvicorn workflow_platform.main:app --port 8001
```

Look for `Started webhook trigger for workflow webhook-echo` in the log.
Then in another shell:

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"event":"build_completed","project":"alpha","duration_s":12.7}' \
  http://localhost:8001/api/triggers/webhook/echo

# Show the run that just fired:
curl -s -H 'X-Dev-User: alice' -H 'X-Dev-Groups: admins' \
  'http://localhost:8001/api/workflow-instances?workflow_id=webhook-echo' | jq '.[0]'
```

**Pass when:** the instance is `completed` and
`context.steps.summarize.output_text` is the model's one-sentence summary
of the payload. Also visible on the dashboard at `/instances` after a
refresh.

**Secured variant (HMAC, G2).** Add `secret_name: MY_WEBHOOK_SECRET` to the
workflow's `trigger.config` (the value is a `SecretStore` key — with the
default `EnvSecretStore` that's an env-var name), export
`MY_WEBHOOK_SECRET=s3cret` before starting the backend, and restart. Then:

```bash
body='{"event":"build_completed","project":"alpha"}'
sig="sha256=$(printf '%s' "$body" | openssl dgst -sha256 -hmac 's3cret' | awk '{print $2}')"

# Unsigned → 401; signed → 200 fired.
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' \
  -d "$body" http://localhost:8001/api/triggers/webhook/echo          # → 401
curl -s -X POST -H 'Content-Type: application/json' -H "X-Hub-Signature-256: $sig" \
  -d "$body" http://localhost:8001/api/triggers/webhook/echo          # → fired
```

If the named secret isn't set in the environment, the endpoint returns **503**
(fails closed) rather than accepting unsigned posts.

---

## 4c. Schedule example (real backend, watch it tick)

**What:** the `scheduled_health_report` example workflow under
`examples/scheduled_health_report/`. Fires once a minute, appends a
status line to `/tmp/scheduled-health-report.log`.

**Why manual:** the schedule trigger has wall-clock behavior unit tests
can only simulate. Watching the file grow is the most direct proof.

Start the backend (same command as 4b), then:

```bash
# Wait a minute for the first fire, then:
tail -f /tmp/scheduled-health-report.log

# Instances per fire:
curl -s -H 'X-Dev-User: alice' -H 'X-Dev-Groups: admins' \
  'http://localhost:8001/api/workflow-instances?workflow_id=scheduled-health-report&limit=5' \
  | jq '.[] | {id, state, started_at}'
```

**Pass when:** the log file gains one line per minute and the instance
list grows by one each tick.

**Cost note:** ~$0.0001 per fire at Haiku 4.5. Leave the backend running
overnight only if you mean to spend a few cents.

---

## 4d. Browser connector — replay-mode RPA Challenge OCR

**What:** the `rpa_challenge_ocr` example workflow under
`examples/rpa_challenge_ocr/` — six steps that exercise every browser
tool category (navigate / read / write / download) plus `image_ocr`
and the `filter_rows_by_date` / `write_csv` stock functions. Replay
test uses a fake `BrowserConnector` and canned OCR text so no real
Chromium or tesseract is required.

**Why manual:** the replay test pins the agentic flow end-to-end with
realistic step outputs. Reading the assertions is faster than reading
the unit-level browser tool tests when you want to confirm the example
still composes.

```bash
cd backend
uv run pytest tests/test_rpa_challenge_workflow.py -v
```

**Pass when:** 3 tests green. The end-to-end test drives 3 due dates →
filters to 2 overdue → fetches+OCRs each → writes a CSV with
`INV-001` / `INV-003` rows → uploads + submits + screenshots.

**Live variant** (real Chromium against the public RPA Challenge URL,
gated behind `BROWSER_LIVE=1`):

```bash
BROWSER_LIVE=1 uv run pytest tests/test_browser_live.py -v -k "not end_to_end"
```

3 connector smokes in ~6 s, free, no Bedrock. The end-to-end test in
the same file is additionally gated by `BEDROCK_LIVE=1` and costs
~$0.05–0.15 per run.

For ad-hoc full-stack debugging there's a printable diagnostic:

```bash
BROWSER_LIVE=1 BEDROCK_LIVE=1 uv run python tools/probe_rpa.py
```

Dumps every step's output + the audit log to stdout. Same harness
used during D8 capability development.

---

## 4e. DMARC report ingest (Gmail attachments → dmarc-viewer)

**What:** the `dmarc_ingest` example under `examples/dmarc_ingest/` — a fully
**deterministic** pipeline (no LLM anywhere): the `email` trigger (provider: gmail) filters
server-side (`query: has:attachment (filename:zip OR filename:gz)`) and
downloads each matching message's attachments to a spool dir (`download_dir`);
then two reusable catalog steps — `extract_archive` unzips the `.zip`/`.xml.gz`
reports into a staging dir, and `copy_files` delivers the XMLs into the
dmarc-viewer app's watched directory, which auto-loads any `.xml` written there.

**Why manual:** exercises the attachment-download path (connector →
trigger spool → payload `attachment_paths`) end to end against real mail.

Prereqs: Gmail credentials for the workflow's account under
`.secrets/gmail/<account>/` (Gates 3–4, `docs/EMAIL_CONNECTOR_PLAN.md`) and the
dmarc-viewer running in watched mode (`cd /home/ubuntu/Dev/dmarc-viewer && npm
start`).

```bash
# Replay-mode automated coverage (no Gmail / no Bedrock):
cd backend
uv run pytest tests/test_dmarc_ingest.py -v

# Backfill — one-shot over historical mail, firing the workflow per message
# via run-batch (backend must be running with the example loaded):
uv run python tools/fetch_dmarc.py \
  --account qrsconsulting@quentinspencer.com --since 2024-01-01 --fire

# Ongoing: just leave the backend running — the email trigger handles
# new report mail as it arrives (poll every 300s).
```

**Pass when:** the backfill prints per-batch `submitted/succeeded/failed`
counts, `.xml` files appear in `/home/ubuntu/Dev/dmarc-viewer/xml-files/`, and
the viewer auto-loads them (it rescans every 10s). Each run is visible in the
dashboard with the step's `extracted_count` in its output.

---

## 5. Live tests — Bedrock, Gmail, Browser

**What:** opt-in suites, each gated by its own env var. The three *live* ones run
together on a weekly cron (`.github/workflows/live-tests.yml`); `schema` and
`integration` are PR gates in `ci.yml` (own jobs).

| Marker | Env gate | Cost | What it hits |
|---|---|---|---|
| `live` | `BEDROCK_LIVE=1` | ~$0.0001 / run | Claude Haiku 4.5 via real Bedrock |
| `gmail_live` | `GMAIL_LIVE=1` | free | Project Gmail account (smoke + send + label) |
| `browser_live` | `BROWSER_LIVE=1` | free (Bedrock end-to-end variant: ~$0.05–0.15) | Real Chromium against RPA Challenge URL |
| `integration` | `TEST_DATABASE_URL=…` | free | Postgres (covered separately in §6) |
| `schema` | `SCHEMA_TESTS=1` | free | Schemathesis fuzz over the OpenAPI GET endpoints (covered separately in §5b) |

**Why manual:** real services can drift in ways no replay test catches
— region access, inference profile health, Gmail quota, RPA Challenge
UI changes, Postgres migrations.

```bash
cd backend

# Just Bedrock:
BEDROCK_LIVE=1 uv run pytest -m live -v
# 3 tests in ~5s.

# Bedrock + Gmail + Browser (the CI cadence):
BEDROCK_LIVE=1 GMAIL_LIVE=1 BROWSER_LIVE=1 \
  uv run pytest -m "live or gmail_live or browser_live" -v
# 10 tests, ~3–5 min (browser is the long pole).
```

**Expected for Bedrock alone:**

```
tests/test_smoke_live.py::test_bedrock_converse_direct PASSED
tests/test_smoke_live.py::test_agent_run_no_tools PASSED
tests/test_smoke_live.py::test_workflow_engine_end_to_end PASSED
3 passed in ~5s
```

If a Bedrock test fails: `backend/tools/smoke_live.py` is the
diagnostic version — it prints cause + action lines mapped to gates
in `docs/BEDROCK_SETUP.md`. For Gmail, `backend/tools/smoke_gmail.py`
plays the same role.

**Pass when:** every suite green at the markers you enabled.

---

## 5b. Schemathesis contract / fuzz validation

**What:** property-based validation derived from FastAPI's own OpenAPI schema.
Every **GET** endpoint is fuzzed (junk path ids, bad query params, odd headers);
the asserted property is `not_a_server_error` — no input may ever produce a 5xx.

**Why manual / separate:** complements the hand-written endpoint tests with
breadth + adversarial inputs, at near-zero maintenance (the cases are generated
from the app, so they can't drift from the schema). Scoped to GET endpoints so it
stays self-contained — no Bedrock, no state mutation, no recordings — and runs
in-process against in-memory repos + replay Bedrock. It's a **PR gate** in
`ci.yml` (its own `schema` job, parallel to `backend`), and opt-in locally.

```bash
cd backend
SCHEMA_TESTS=1 uv run pytest -m schema -q
# ~20 GET operations fuzzed; ~2–3 min (hypothesis sweep). No AWS, free.
```

Authenticates as a dev admin (`X-Dev-User` / `X-Dev-Groups: admins`) so it
exercises real handler logic rather than bouncing off the auth middleware. A
failure means some GET handler 500s on an input it should reject cleanly (e.g. a
404 / 422) — a real bug. Side-effecting / model-calling endpoints (scaffold,
dry-run, run, import, lifecycle) are deliberately out of scope; they're covered
by the hand-written tests in §A.

**Pass when:** `uv run pytest -m schema` reports all GET operations passed (no
server errors).

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
curl -s -H "$H" -H "$G" http://localhost:8001/api/cost/by-workflow | jq
curl -s -H "$H" -H "$G" http://localhost:8001/api/cost/by-model | jq
curl -s -H "$H" -H "$G" http://localhost:8001/api/cost/by-day | jq

# Optional: filter by time
curl -s -H "$H" -H "$G" \
  "http://localhost:8001/api/cost/by-day?since=2026-05-01T00:00:00Z" | jq
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
  http://localhost:8001/api/workflow-instances/no-such-id/retry | head -3
# → HTTP/1.1 403 Forbidden  (or 404 if you flip the header to admins, since
#    the instance id doesn't exist)

# Admin can hit it (404 instead of 403 means the role check passed)
curl -i -s -X POST -H 'X-Dev-User: alice' -H 'X-Dev-Groups: admins' \
  http://localhost:8001/api/workflow-instances/no-such-id/retry | head -3
# → HTTP/1.1 404 Not Found

# Auditor can read /api/audit, viewer cannot
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'X-Dev-User: a' -H 'X-Dev-Groups: auditors' \
  http://localhost:8001/api/audit
# → 200

curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'X-Dev-User: b' -H 'X-Dev-Groups: viewers' \
  http://localhost:8001/api/audit
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
curl -s http://localhost:8001/metrics > /tmp/before.txt
BEDROCK_LIVE=1 uv run pytest tests/test_smoke_live.py::test_workflow_engine_end_to_end -q
curl -s http://localhost:8001/metrics > /tmp/after.txt

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
npm test         # vitest run, ~7s
npm run build    # tsc -b && vite build (typecheck + bundle), ~5s
```

134 tests across 24 files + a clean build. Same checks CI runs. Covers the
dev-auth headers, the fetch API client (URL / method / body construction),
App routing, evaluation/usage helpers, the role switcher, the WebSocket
events hook, the canvas + run-form helpers (layout, labels, status, immutable
form updates), and the C6/C7 components (cost meter, explain panel, validation
panel, the catalog `ToolPicker` + Inspector pickers, and the `GoalField`).

**Pass when:** vitest reports `Tests 134 passed (134)` and the build prints the
Vite `✓ built in …` summary.

---

# Part B — Canvas GUI walkthrough (C5–C7)

A tick-the-box, follow-along click-through of the GUI. Each case: **Steps →
Expected.** Tick the box when it passes. This is the canvas counterpart to Part
A's API checks — it covers the friendly shell (C5), the trust wedge (C6), and
authoring parity (C7). See `docs/CANVAS_ROADMAP.md` for the cut definitions.

## Setup

Two processes. The Vite dev server runs on **:4200** and proxies `/api` + `/ws`
to the backend on **:8001**. The C6/C7 cases that call the model (dry-run,
"Describe it") need **live Bedrock** — same AWS creds as Part A §5.

```bash
# Terminal 1 — backend (dev auth; live Bedrock for dry-run + scaffold; Postgres
# so created workflows persist across restarts)
cd backend
DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
AUTH_MODE=dev BEDROCK_MODE=live \
  uv run uvicorn workflow_platform.main:app --reload --port 8001

# Terminal 2 — frontend
cd frontend
npm run dev      # → http://localhost:4200
```

`scripts/run-local-be.sh` wires all of the above (Postgres + migrate + live Bedrock
+ all triggers + Gmail-from-`.secrets` + dev auth) in one command if you'd rather
not assemble the env by hand.

Notes:
- **Templates load from repo-root `examples/`** by default, resolved
  independently of the working directory. Override with `WORKFLOW_DEFINITIONS_DIR`.
- **Two independent header controls:** the **"Acting as"** role switcher
  (`admins` / `designers` / `operators` / `viewers` / `auditors`; writes
  localStorage + reloads) and the **Developer** toggle (shows/hides the dev
  console nav). Default identity (nothing set) acts as **admin**.
- **In-memory backend** (no `DATABASE_URL`): created workflows live only until
  restart; the bundled templates are always present.

---

## C5 — Friendly shell & cold-start

### TC1 — Automations home is the front door  ☐
**Steps:** Load `http://localhost:4200/` (bare root).
**Expected:** heading **"Your automations"** (not a list of instances); top nav
shows **Automations** + **Templates** only; fresh backend shows the empty state
("No automations yet."), otherwise a **card grid**.

### TC2 — Templates gallery lists the bundled examples  ☐
**Steps:** Click **Browse templates** (or the Templates nav link).
**Expected:** a card grid of the bundled example workflows (Email Triage, GitHub
PR triage, Invoice Extraction, PDF Classifier, RPA Challenge OCR, Research Paper
triage, Scheduled Health Report, Webhook Echo, …), each with a **step count** and
a **friendly trigger label** ("On a webhook", "On a schedule", "On a new file",
"Run manually") — not the raw enum.

### TC3 — Use a template → clone → land on the canvas  ☐
**Steps:** On a template card (admin/designer), click **Use this template**.
**Expected:** navigate to `/canvas/<new-id>?edit=1`, canvas opens **in edit mode**
("Editing" pill, palette, Save/Discard); cloned **steps + edges present**; name
ends with **"(copy)"**, id is its slug; back on **Automations** the new workflow
appears as a card.

### TC4 — Create a blank workflow  ☐
**Steps:** Home → **Create** → name it ("Invoice triage") → **Create** in dialog.
**Expected:** canvas opens in edit mode with a single trigger node, **no steps**;
id is the slug; add a step + Save persists; the card shows **0 runs** on the home.

### TC5 — Blank id de-duplication  ☐
**Steps:** **Create** with a **blank** name, twice.
**Expected:** first → "Untitled workflow" / `untitled-workflow`; second →
`untitled-workflow-2` (no collision, no error).

### TC6 — Developer toggle reveals/hides the console  ☐
**Steps:** Click **Developer: off** (top-right), then reload.
**Expected:** toggling **on** reveals **Instances / Workflows / Cost** in the
nav, **off** hides them; the state **persists across reload**; with it on,
**Workflows** shows the developer table (the dev console still works, just demoted).

### TC7 — Deep links to the dev console still work  ☐
**Steps:** With Developer **off**, visit `/workflows` and `/instances` directly.
**Expected:** both render — the toggle governs **nav visibility only**.

### TC8 — Card enrichment: run count + latest status  ☐
**Steps:** Run a workflow once, return to **Automations**.
**Expected:** that card shows an updated **run count** and a **status pill** for
the latest run ("Done" / "Failed" / "Running") — friendly words, not the enum.

### TC9 — RBAC: authoring is gated, reading is not  ☐
**Steps:** Use the **role switcher** to become a **viewer**.
**Expected:** on the home the **Create** + **Describe it** buttons are gone; in
**Templates** the **Use this template** button is gone (browsing still works);
switch back to designer/admin → all return. API cross-check:
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8001/api/workflows \
  -H 'X-Dev-User: v' -H 'X-Dev-Groups: viewers' -H 'Content-Type: application/json' -d '{}'
# → 403
```

### TC10 — Dialog cancel / no-op safety  ☐
**Steps:** Open **Create**, type a name, click **Cancel** (or the overlay).
**Expected:** dialog closes, **no workflow created**, home unchanged.

---

## C6 — The trust wedge

Use a real agentic workflow with history — `pdf-classifier` (import it, Part A
§3) run a couple of times via live Bedrock is ideal.

### TC11 — Cost estimate in the Run dialog  ☐
**Steps:** Open a workflow's canvas → **Run**.
**Expected:** the Run dialog shows a per-run **cost estimate** — "~$X/run (avg of
N runs)" when the workflow has history, or the per-step **model rates** when it
has none (no fabricated number). Backed by `GET /api/workflows/{id}/cost-estimate`.

### TC12 — Live budget meter during a run  ☐
**Steps:** Start a run and watch the canvas **footer** while it executes.
**Expected:** a **token / $ meter** climbs live as steps complete; it turns
`--warn` past ~80% of `max_total_tokens` and `--err` at the cap, with the
`budget_action` (notify / pause / escalate) shown. A workflow with
`budget_action: pause` visibly **pauses** at the cap.

### TC13 — Capability boundary on an agent node  ☐
**Steps:** Select an **agent** step (view mode is fine).
**Expected:** the Inspector shows a **capability boundary** — the tools the step
*can* use vs greyed-out/denied, each with a reason (e.g. "not in this step's
allowlist"). Backed by `GET /api/workflows/{id}/capabilities` (the engine's layer
intersection). Deterministic steps don't show it.

### TC14 — Explain-this-run  ☐
**Steps:** Open a **finished** run (`/canvas/<id>?instance=<uuid>`, or "View on
canvas" from an instance page) → click a step node.
**Expected:** an **explain** panel — for an agent step: what it was asked, the
tools it called (args + results), tokens/cost, and the memory hash in effect; for
a deterministic step: its function + output. Backed by
`GET /api/workflow-instances/{id}/steps/{step_id}/explain`.

### TC15 — Test / dry-run (sandboxed)  ☐
**Steps:** On a non-browser workflow's canvas, click **Test**.
**Expected:** it runs against a **MockWorld** with external tools
(email/connector/browser) replaced by no-op stubs but **live Bedrock** ("sandbox
the world, keep the brain"); the canvas shows the **"🧪 Dry run — sandboxed …
Nothing real was touched."** banner and live status. A **browser** workflow is
**rejected** with a clear message. Backed by `POST /api/workflows/{id}/dry-run`;
the instance is tagged `dry_run` in its context.

---

## C7 — Authoring parity

### TC16 — "Describe it" NL scaffold  ☐
**Steps:** On the home (admin/designer), click **Describe it** → type a plain
description (e.g. *"When a PDF lands in my inbox folder, extract the text,
classify it, and file it into a folder by type."*) → **Draft it**. (Live Bedrock.)
**Expected:** a workflow is drafted and you land on its **canvas in edit mode**;
the steps/edges reflect the description and reference only **real** functions /
tools. A nonsense description or model hiccup surfaces an error in the dialog to
retry. Backed by `POST /api/workflows/scaffold`.

### TC17 — Catalog pickers in the Inspector  ☐
**Steps:** In edit mode, select the **trigger** node, a **deterministic** step,
then an **agent** step.
**Expected:** trigger **type** is a **select** of real trigger types with a
description + config-field hints; the deterministic **function** is a **select**
with a description; the agent **tools** is a **ToolPicker** — checkboxes
**grouped by category** (filesystem / email / connector / browser / …) with
one-line descriptions. Tools enabled but absent from the engine's catalog are
surfaced, not dropped. Backed by `GET /api/catalog`. (If the catalog can't load,
each falls back to the prior raw text input.)

### TC18 — Build-time validation (red borders + blocked save)  ☐
**Steps:** In edit mode, break something — clear an agent step's **goal**, or add
a step and leave it **disconnected**.
**Expected:** a **findings panel** lists every problem (error/warning counts +
messages); error nodes get a **red border**; clicking a finding selects its node.
**Save is blocked** while errors exist ("Fix N validation error(s) before
saving."); warnings (e.g. disconnected step) don't block. Backed by
`POST /api/workflows/validate`.

### TC19 — Safer goal editing  ☐
**Steps:** In edit mode, select an **agent** step and look at the goal field.
**Expected:** it's labelled **"Instructions for the AI"** (not "goal") with inline
help ("this is what the AI reads as its task on every run … editing this changes
how the agent behaves") and a **See examples** toggle that reveals a few example
instructions. Editing it updates the draft (and re-runs validation per TC18).

---

# Reference (applies to both parts)

## Triggering workflows from the dashboard or shell

Three equally-supported ways to start a run today; pick whichever fits
the moment.

1. **Dashboard "Run" button.** Workflows page → each row has a Run
   button that opens a JSON-payload dialog → `POST
   /api/workflows/{id}/run` (Admin/Operator). The dashboard navigates
   straight to the new instance's detail view on success. Same page
   also has an "Import workflow" dialog wired to
   `POST /api/workflows/import`. A workflow's **canvas** (section 3b)
   has its own Run button that builds a *form* from `example_payload`
   instead of asking for JSON, then hands off to the live canvas view —
   the most non-technical-friendly path.

2. **`backend/tools/fire.py`.** One-shot CLI runner that respects
   `DATABASE_URL` (Postgres or in-memory) and `BEDROCK_MODE` (live /
   record / replay):

   ```bash
   cd backend
   DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
   BEDROCK_MODE=live \
     uv run python tools/fire.py \
     --definition ../examples/pdf_classifier/workflow.yaml \
     --trigger '{"file_path": "/abs/path/to/some.pdf"}'
   ```

   Output ends with a `view: http://localhost:4200/instances/<uuid>`
   line you can click through. Non-zero exit on FAILED / KILLED so
   it's CI-able.

3. **In-process triggers via `WORKFLOW_DEFINITIONS_DIR`.** Filesystem
   / schedule / webhook triggers all auto-register on startup when you
   point the env var at a directory of workflow YAMLs. (**The email trigger
   additionally needs `WORKFLOW_PLATFORM_GMAIL_ACCOUNT=<address>`** — see
   the note under §1 — otherwise it logs "Gmail poll disabled" and stays
   off even with creds on disk.)

   ```bash
   cd backend
   DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
   WORKFLOW_DEFINITIONS_DIR=../examples \
   AUTH_MODE=dev \
     uv run uvicorn workflow_platform.main:app --port 8001

   # In another shell:
   cp my-invoice.pdf ../examples/pdf_classifier/sample_inbox/
   ```

   The orchestrator logs `Started <type> trigger for workflow <id>`
   on startup and `workflow … fired by trigger … → instance … state=…`
   per fire. Per-definition errors log + skip; the server stays up.

   Caveat: example workflows ship with relative
   `trigger.config.path` values. Start the server from the repo root
   so the relative path resolves — or edit the YAML to use an
   absolute path.

---

## Open gaps — things you can't yet test manually

1. **The deployed Terraform stack** is unverified end-to-end. The IaC
   under `infra/` is `terraform validate`-clean, but no one's run
   `terraform apply` yet. The whole "does the deployed system work?"
   question is open (P2.3 in `docs/NEXT_STEPS.md`).

2. **The RPA Challenge OCR live workflow** completes every step and
   submits the form successfully, but the challenge's server returns
   only `{"success":false}` with no diagnostic detail. We can't
   iterate against opaque pass/fail signals cost-effectively. Same
   class of problem as any third-party system that gates on undisclosed
   validation — flagging it so no one chases it without a plan.

See `docs/NEXT_STEPS.md` for the prioritized backlog.

---

## When this doc goes stale

Update it when any of the following changes:

- A new endpoint is added to `backend/src/workflow_platform/api/`
- A new test marker is registered in `pyproject.toml`
- A new example workflow lands under `examples/` (add a 4x section)
- A new route lands in `frontend/src/components/App.tsx`
- A new canvas cut ships (add a Part B `TC` case; the next is **C8** —
  batch run + a11y/responsive — currently unbuilt)
- One of the "open gaps" above gets closed (delete that bullet)
- Auth or role mapping changes in `backend/src/workflow_platform/auth/`
- Frontend or backend test counts drift far enough from what's quoted
  here that they stop being a useful sanity-check (rule of thumb: ±10%)
