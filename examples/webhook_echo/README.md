# Webhook echo example

Smallest possible workflow that proves the webhook trigger path works end to
end. Receives a JSON payload via HTTP POST, asks Claude to summarize it in
one sentence, persists the run.

## Files

- `workflow.yaml` — the definition. Trigger = webhook (`trigger_id: echo`),
  one agentic step, no edges.

## Run it (orchestrator-wired backend)

If the backend is running with the orchestrator loading this directory:

```bash
# Fire the webhook (no auth needed — webhook endpoints are exempt).
curl -X POST -H 'Content-Type: application/json' \
  -d '{"event": "build_completed", "project": "alpha", "duration_s": 12.7}' \
  http://localhost:8000/api/triggers/webhook/echo

# Dashboard:
open http://localhost:4200/instances
```

To bring up the backend with this example loaded:

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
WORKFLOW_DEFINITIONS_DIR=../examples \
AUTH_MODE=dev \
  uv run uvicorn workflow_platform.main:app --port 8000
```

## Run it (one-shot via `tools/fire.py`, no server needed)

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
BEDROCK_MODE=live \
  uv run python tools/fire.py \
  --definition ../examples/webhook_echo/workflow.yaml \
  --trigger '{"event":"build_completed","project":"alpha","duration_s":12.7}'
```

Output ends with a `view:` URL pointing at the instance in the dashboard.

## What's worth checking

- The audit log shows `workflow_started` → `step_started` → `tool_call`
  (just the agent's converse call) → `step_completed` → `workflow_completed`.
- `steps.summarize.output_text` contains the one-sentence summary.
- Token + cost are attributed to the right model.

## Extending it

Real webhook integrations replace the summarizer with a meaningful agent or
a deterministic step. The webhook trigger itself is the load-bearing piece;
everything downstream is a normal workflow.
