# Scheduled health report example

Periodic workflow demonstrating the `schedule` trigger end to end. Fires
every 60 seconds, asks Claude to write a one-line status, appends it to
`/tmp/scheduled-health-report.log` via the `append_file` stock function.

## Files

- `workflow.yaml` — schedule trigger + one agentic step + one deterministic
  step. Edge wires `status` → `append`.

## Run it

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
WORKFLOW_DEFINITIONS_DIR=../examples \
AUTH_MODE=dev \
  uv run uvicorn workflow_platform.main:app --port 8001

# In another terminal — wait a minute for the first fire, then:
tail -f /tmp/scheduled-health-report.log

# Dashboard view:
open http://localhost:4200/instances?workflow_id=scheduled-health-report
```

The log file accumulates one line per minute:

```
[2026-05-10T17:42:00+00:00] system: ok — no alerts pending
[2026-05-10T17:43:00+00:00] system: ok — background jobs idle
...
```

To fire it once on demand (skip the wait), use `tools/fire.py`:

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
BEDROCK_MODE=live \
  uv run python tools/fire.py \
  --definition ../examples/scheduled_health_report/workflow.yaml \
  --trigger '{"triggered_at":"2026-05-10T17:42:00+00:00","schedule":"every 60s"}'
```

## What's worth checking

- `tail -f /tmp/scheduled-health-report.log` shows a new line each minute
  while the backend runs.
- `/api/workflow-instances?workflow_id=scheduled-health-report` returns
  one instance per fire, each `completed`.
- The deterministic step's output (`steps.append.output`) shows
  `path`, `appended_chars`, and `total_bytes` — useful for queries like
  "how many bytes of status history did we accumulate this week?"

## Tuning

- Drop `interval_seconds` to `5` for a faster demo loop. Bedrock charges
  per fire (about $0.0001 each at Haiku 4.5), so keep this finite.
- Switch to `cron: "0 9 * * *"` for a 9 AM daily report.
- Point `path:` somewhere persistent (an S3 path once `S3Connector` grows
  a generic write path; today the local filesystem is fine).

## Extending it

Real scheduled workloads replace the status line with something
substantive — overnight ETL summaries, recurring health checks of an
external system, periodic SLA reports. The trigger + `append_file`
primitive stay the same.
