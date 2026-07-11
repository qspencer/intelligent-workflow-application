# Email triage — live validation run (read-only)

The rubric-iteration validation of `email_triage` against **real mail**
(`qspencer@gmail.com`), and the experiment that decides whether the platform
adopts the **engram** per-entity memory library (deferral + trigger logged in
`docs/SEMANTICS.md` → "Deferred: engram").

## Safety posture

- **Read-only.** The agent has `tools: []` *and* an empty capability
  allowlist — it cannot send mail or apply labels, only classify. Keep it
  that way; the test suite pins it.
- Message bodies flow to Bedrock for classification and are stored in
  `step_executions.output` (local Postgres). No mailbox mutation of any kind.

## Running

Credentials for the account already live under `.secrets/gmail/qspencer@gmail.com/`.
Start the backend normally (`./scripts/run-local-be.sh`); the orchestrator
registers the trigger and seeds the rubric on boot. The cursor starts at
"now" — only mail arriving after startup is triaged. Cost: Haiku 4.5,
≤6k tokens/message (~$0.001–0.002 each).

## What this run is measuring

1. **Rubric quality** — same loop as PR/paper triage: query the results,
   find misclassifications, tighten `agent_memory.md`, watch `memory_hash`
   change, repeat.
2. **Memory-shaped failures** — the engram adoption evidence. Log every case of:
   - a *known repeat sender* re-classified from scratch (sender history
     would have fixed the category);
   - an **awaiting-reply** decision that needs cross-run state ("did we
     already reply?") the agent can't have;
   - a *claim inside received mail* (invoice, obligation, renewal) that the
     summary asserts as fact — the quarantine case.

If (2) accumulates real instances, that fires the SEMANTICS.md trigger:
design-reviewer pass, then the scoped engram integration.

## Querying results across runs

```sql
-- category distribution + confidence, most recent first
SELECT output->>'category' AS category,
       output->>'confidence' AS confidence,
       output->>'summary' AS summary,
       started_at
FROM step_executions
WHERE step_id = 'record' AND output->>'parse_ok' = 'true'
ORDER BY started_at DESC;
```

Or the dashboard: each run's Triage card on the instance page, filtered by
workflow `email-triage-live`.
