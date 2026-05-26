# Email Triage

End-to-end Gmail triage workflow. The agent reads one inbound message,
applies the rubric in `agent_memory.md` to pick a category, optionally
drafts a reply via `email_send`, applies a triage label via
`email_label_apply`, and emits a structured JSON record parsed by the
deterministic `record_email_triage` step.

Trigger is `gmail_poll` against
`intelligent.workflow.engine@quentinspencer.com` (the dedicated project
account). In production the connector polls Gmail directly; the
fixtures here let you exercise the workflow without authenticating
against Google.

## Files

- `workflow.yaml` — the workflow definition (`triage` agentic step →
  `record` deterministic step)
- `agent_memory.md` — categorization rubric (5 buckets, reply
  discipline, output schema). Auto-loaded into the agent's system
  prompt by the engine.
- `fixtures/` — five hand-crafted `EmailMessage`-shaped JSON files,
  one per category (urgent, fyi, spam, personal, awaiting-reply). No
  real-mail PII; all names + addresses synthetic.
- `scripts/run_one.sh` — fire one fixture through the workflow via
  `backend/tools/fire.py`
- `scripts/run_batch.sh` — fire every fixture in sequence; useful for
  iterating on the rubric

## Quick start

The test in `backend/tests/test_email_triage_workflow.py` exercises
the full workflow end-to-end against `FakeBedrock` and a
`FakeGmailService`:

```
cd backend
.venv/bin/pytest tests/test_email_triage_workflow.py -v
```

To fire a fixture against live Bedrock + a live Gmail account, complete
the gates in `docs/EMAIL_CONNECTOR_PLAN.md` (Gates 1-4), set
`WORKFLOW_PLATFORM_GMAIL_ACCOUNT`, and then:

```
WORKFLOW_PLATFORM_GMAIL_ACCOUNT=intelligent.workflow.engine@quentinspencer.com \
  examples/email_triage/scripts/run_one.sh \
  examples/email_triage/fixtures/01_urgent_meeting_moved.json
```

When the env var is set, `tools/fire.py` (and `main.py` for the dashboard
path) auto-wires `EmailSendTool` + `EmailLabelApplyTool` into the engine
catalog. The bootstrap helper at
`workflow_platform.connectors.email.bootstrap.maybe_build_gmail_connector`
reads `.secrets/gmail/<account>/` from disk if `EnvSecretStore` is the
backend — no extra steps beyond completing Gate 4.

## Output shape

`record_email_triage` parses the agent's final JSON into:

| Field             | Type            | Source                          |
|-------------------|-----------------|---------------------------------|
| `parse_ok`        | bool            | always present                  |
| `category`        | str             | agent's chosen bucket           |
| `confidence`      | float           | agent's self-reported certainty |
| `reply_drafted`   | bool            | did the agent call email_send   |
| `labels_applied` | list[str]       | labels the agent applied        |
| `label_count`     | int             | computed from `labels_applied`  |
| `summary`         | str             | one-sentence explanation        |

Query Postgres after a batch run:

```
SELECT
  output->>'category' AS category,
  COUNT(*) AS n,
  AVG((output->>'confidence')::float) AS avg_confidence
FROM step_executions
WHERE step_id = 'record'
  AND output->>'parse_ok' = 'true'
GROUP BY 1
ORDER BY n DESC;
```

## Iterating on the rubric

Same loop as `examples/research_paper_triage/` and
`examples/github_pr_triage/`: edit `agent_memory.md`, re-run the
batch, query the `step_executions` table grouped by
`output->>'category'` + `memory_hash` to compare runs. The
`memory_hash` field on each agentic step's output (16-char SHA-256
prefix) lets you A/B rubric edits.

## What's not wired yet

- The orchestrator's `gmail_poll` branch builds its own connector
  rather than sharing one with the tools — for single-account v1
  this works but means two parallel connector instances (one for the
  trigger polling, one for the tools' send/label_apply). Surfaces if
  token-refresh churn becomes noisy in the audit log.
- The example's `triaged/<category>` labels (urgent, fyi, spam,
  personal, awaiting-reply) need to exist in Gmail before
  `email_label_apply` calls succeed. Either pre-create them in the
  Gmail UI, or update the connector to auto-create on first use (it
  currently raises `GmailLabelNotFound`).
