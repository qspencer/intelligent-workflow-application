# GitHub PR triage example

Receives a GitHub pull-request payload (webhook or `tools/fire.py`), asks
Claude to categorize the PR and flag review concerns, and records the
structured triage result. Designed as a validation workload — see
`docs/USE_CASES.md` for why this one came first.

## What you get by running it

For each PR fired through the workflow, `step_executions.output` for the
`record_triage` step contains:

```json
{
  "parse_ok": true,
  "category": "bug_fix",
  "complexity": "small",
  "needs_tests": true,
  "summary": "Adds bounded retries and dead-lettering for transient Stripe webhook failures.",
  "concerns": [],
  "concern_count": 0
}
```

Queryable directly from Postgres:

```sql
SELECT
  output->>'category'    AS category,
  output->>'complexity'  AS complexity,
  (output->>'concern_count')::int AS concerns
FROM step_executions
WHERE step_id = 'record_triage' AND state = 'completed'
ORDER BY started_at DESC;
```

## Files in this directory

| File | What it is |
|---|---|
| `workflow.yaml` | The definition. Webhook trigger + 1 agentic step + 1 deterministic step. |
| `agent_memory.md` | Rubric the agent sees in its system prompt: categories, complexity scale, concern checklist, output discipline. **Edit this to change behavior** — the `memory_hash` recorded on each run lets you correlate output drift with the edit. |
| `fixtures/*.json` | Five hand-crafted PR payloads covering: perfect PR, missing description, gigantic refactor, dependency bump, doc-only from external contributor. Used by the test. |
| `scripts/fetch_prs.sh` | One-liner to pull real closed PRs from any GitHub repo via `gh api`. |
| `scripts/run_one.sh` | Fire a single PR JSON through the workflow via `tools/fire.py`. |
| `scripts/run_batch.sh` | Fire an array of PR JSON objects, one at a time, with a summary at the end. |

The replay-mode pytest for this workflow lives at
`backend/tests/test_github_pr_triage.py` and runs in CI. It reads
`workflow.yaml`, `agent_memory.md`, and the JSONs in `fixtures/` directly,
so any edit here is picked up on the next test run.

## Three ways to fire PRs at the workflow

### Synthetic fixtures (offline, no AWS — fastest iteration)

The test suite at `backend/tests/test_github_pr_triage.py` uses `FakeBedrock`
so it costs nothing and runs in under a second:

```bash
cd backend
uv run pytest tests/test_github_pr_triage.py -v
```

13 tests, all should pass. It's part of CI, so the same checks run on every
push.

### Historical PRs from `gh api` (real LLM, real variability)

Fetch 50 closed PRs from any public repo, fire each one through:

```bash
# Once: log in to gh
gh auth login

# Fetch + run
examples/github_pr_triage/scripts/fetch_prs.sh anthropics/anthropic-sdk-python 50 \
  > /tmp/prs.json

# Persist runs to Postgres so the dashboard shows them
docker compose up -d postgres
cd backend && DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
  uv run alembic upgrade head && cd ..

DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
BEDROCK_MODE=live \
  examples/github_pr_triage/scripts/run_batch.sh /tmp/prs.json
```

Cost: ~$0.0002 per PR at Haiku 4.5 → ~$0.01 for 50 PRs. Bigger bodies cost
more (the whole PR JSON is in the prompt).

After the batch runs, query the results:

```bash
docker compose exec postgres psql -U workflow -d workflow -c "
  SELECT output->>'category', output->>'complexity', output->>'concern_count'
  FROM step_executions
  WHERE step_id = 'record_triage' AND state = 'completed'
  ORDER BY started_at DESC LIMIT 20;
"
```

Or open the dashboard: `http://localhost:4200/instances?workflow_id=github-pr-triage`.

### Live webhook (real-time, needs a public URL)

The webhook endpoint at `POST /api/triggers/webhook/github-pr-triage` is
exempt from user auth, so a public URL is enough to wire it to a real
GitHub repo:

1. Expose the backend: `cloudflared tunnel --url http://localhost:8000`
   gives you a free ephemeral HTTPS URL. (Or `ngrok`, or Tailscale Funnel.)
2. GitHub → repo Settings → Webhooks → Add webhook
   - Payload URL: `https://<your-tunnel>/api/triggers/webhook/github-pr-triage`
   - Content type: `application/json`
   - Events: just `Pull requests`
3. Open a PR. The workflow fires. The run is visible on the dashboard.

**Security note:** the webhook endpoint accepts any caller. Production
deployments should add HMAC verification (`X-Hub-Signature-256`) — this is
gap G2 in `docs/NEXT_STEPS.md`.

## Iterating on the rubric

The agent's behavior is shaped almost entirely by `agent_memory.md`. To
tune:

1. Run 50 real PRs through the workflow (per "Historical PRs" above).
2. Look at the outputs. Find a class of PR the agent mis-categorizes or
   under/over-flags.
3. Edit `agent_memory.md`. Note: every run after the edit gets a new
   `memory_hash` in its step output, so you can grep audit log to find
   "the first run after I tightened the chore rule."
4. Re-run the same 50 PRs. Diff the categories.

The `memory_hash` versioning was added precisely to make this loop legible.

## Why this workflow shape

- **One agentic step.** Easy to read and reason about. If the workload
  ever needs two LLM passes (e.g. one for category, one for concerns), we
  can split.
- **Deterministic `record_pr_triage` after.** Surfaces structured fields
  for SQL queries without re-parsing JSON. Same pattern as the eval loop
  in the PDF classifier example.
- **No outbound webhook *yet*.** Posting a comment back to GitHub is one
  more step (an agentic call to `ConnectorSendTool` with the
  `WebhookConnector`). Worth adding once the offline iteration plateaus
  and we trust the agent enough to comment on real PRs.

## Findings from the first real-world iteration

Fired 50 closed PRs (25 from `anthropics/anthropic-sdk-python`, 25 from
`fastapi/fastapi`) through the workflow four times, editing the rubric
between runs. ~$1.75 of Haiku 4.5 spend total.

**Finding 1 — `agent_memory.md` files were not auto-loaded at runtime
(fixed in G6).** The original `workflow.yaml` referenced "the agent
memory loaded above this prompt," but the engine's `MemoryManager` was
only populated when called explicitly, and nothing read adjacent example
memory files at load time. Every step output had `memory_hash: null`.
**Resolution:** `seed_memory_from_workflow_dir` now runs in both
`TriggerOrchestrator._register_one` and `tools/fire.py`. On a fresh
batch with the system_prompt block removed from the YAML, `memory_hash`
is non-null and behavior matches v4. The rubric in `agent_memory.md`
is now the single source of truth.

**Finding 2 — once loaded, the rubric works as designed.** v1/v2 had 78
and 86 total concerns across 50 PRs, with phrasing inconsistent across
runs and post-review state leaking in (`"PR closed without merge"`,
`"already merged"`). v3 (rubric inlined) dropped to 47 concerns, all
catalog-compliant, no state leaks.

**Finding 3 — `author_association` is the right signal for "external
contributor."** v3 over-flagged: 25/50 PRs got `from external contributor`
because the agent had no rule for who's a maintainer. v4 added
"`author_association` ∈ `{NONE, FIRST_TIME_CONTRIBUTOR}`" to the rule.
Result: 21/21 `NONE` PRs flagged, 0/16 `MEMBER` PRs flagged, 1/13
`CONTRIBUTOR` PRs flagged (single edge case). Near-perfect alignment.

**Finding 4 — cost per PR is dominated by the user message, not the
system prompt.** Adding a 3.5KB system prompt only bumped per-run cost
~6%. Bot-driven release PRs (with full changelogs in the body) are the
expensive runs. Median run was ~8K tokens, ~$0.009. After Finding 5's
fix slimmed the trigger payload, the user message is now just the PR
body + small metadata — bot-driven release PRs with long changelogs
remain the cost outliers, but everything else dropped substantially.
Observational; no further action at current scale.

**Finding 5 — listing PRs via `gh api .../pulls` returns null for
`additions`, `deletions`, `changed_files` (fixed).** The list endpoint
omits those stats; only the per-PR detail call populates them. The
fetcher script now does a second `gh api repos/X/Y/pulls/N` call per
PR and slims the resulting payload to just the fields the rubric in
`agent_memory.md` actually reads — number / title / body / user /
author_association / additions / deletions / changed_files / base /
head. The slim shape typically reduces payload size by ~95%, cutting
both Bedrock input tokens *and* the `workflow_instances.trigger_payload`
JSONB size in Postgres. (Also incidentally addresses Finding 4 — the
PR body is now the only large variable left in the user message.)

These findings are the kind of thing you can't get from a unit test —
which is the whole point of running validation workloads.

## Next graduation steps

The test is already in `backend/tests/` and runs in CI. Remaining steps
to take this from "validation workload" to "production":

1. Add an outbound-webhook step using `ConnectorSendTool` to post a PR
   comment back to GitHub. The agent has the categorization + concerns;
   one more step turns that into a comment body and a
   `POST /repos/{owner}/{repo}/issues/{number}/comments` call.
2. Add HMAC verification on the inbound webhook (gap G2 in
   `docs/NEXT_STEPS.md`).
3. Iterate on the rubric against ~100 real PRs (see "Historical PRs"
   above). Edit `agent_memory.md` until categories are stable.
4. If the workflow earns a place in production: copy it from
   `examples/github_pr_triage/` to wherever production definitions live
   (Postgres-backed import via `POST /api/workflows/import`, or just
   leave it in `examples/` and let the orchestrator auto-load on
   startup).
