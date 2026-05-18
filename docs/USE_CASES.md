# Validation workloads

Catalog of real-world workloads worth mocking up to validate the platform.
This is not a product roadmap or customer-acquisition plan — it's a list of
"things we could build with today's primitives that would generate real
variable input for the engine and surface real edges no automated test will
catch."

## Why this doc exists

The platform's first concrete workload — `examples/pdf_classifier/` — is a
*demo*, not something anyone actually needs done. To take the project past
"runs end-to-end on a clean checkout" we need workloads that:

- have a real-world input source someone will look at the output of,
- exercise the LLM with variability automated tests can't simulate, and
- create iteration pressure on the eval rubric, agent memory, prompt design,
  and capability allowlists.

Without that, every additional feature is a guess. The PR-triage candidate
below has the most "free real inputs forever" leverage; the rest are good
seconds.

## What makes a good validation workload

- **Naturally variable inputs.** A new arXiv PDF is genuinely different
  from the last one; a webhook payload from a CI run is different every
  time. The engine handles fixed-shape inputs already; variability is
  where the eval loop earns its keep.
- **Visible outputs you'll iterate on.** If you don't read the result,
  you won't notice when the agent drifts.
- **Fits current capabilities.** No OAuth, no streaming, no email — see
  "What to skip" below.
- **Has near-ground-truth for eval scoring.** Either a "you know the
  right answer when you see it" check (invoice total matches receipt) or
  a faithfulness rubric (summary supported by source text).
- **Cheap to mock for tests.** A small fixture should produce a
  reproducible run.

## What the platform can do today

Useful as a quick capability map when sketching new workloads. Anything
not listed here either doesn't exist yet or is on the deferred list.

| Capability | Implementation |
|---|---|
| Filesystem trigger | `FilesystemTrigger` (watchdog) |
| Webhook trigger (inbound) | `WebhookTrigger` + `POST /api/triggers/webhook/<id>` |
| Schedule trigger | `ScheduleTrigger` (cron + interval) |
| Webhook send (outbound) | `WebhookConnector` via `ConnectorSendTool` |
| S3 read | `S3Connector` via `ConnectorQueryTool` |
| PDF extraction | `pdf_extract` stock function + tool |
| File read/write | `FileReadTool`, `FileWriteTool`, `append_file` |
| Routing by classification | `route_by_classification` stock function |
| LLM-as-judge eval | `record_evaluation` stock function |
| Agent memory (Phase A) | `MemoryManager` (file-backed Markdown), versioned via `memory_hash` |
| Cost reports | `/api/cost/by-{workflow,model,day}` |
| Live event stream | `/ws/events`, dashboard subscribes |

LLM is Claude Haiku 4.5 (cheap, fast) by default; switch the workflow YAML's
`model:` to a Sonnet 4.6 / Opus 4.x profile for harder tasks.

## Candidate workloads

### Filesystem-driven

**1. Invoice / receipt → expense tracker.** Drop receipts into an inbox,
agent extracts vendor / total / date / category, routes the PDF *and*
appends a structured row to a CSV. Need a small new deterministic
function (`append_csv` shaped like `append_file`). Real use:
end-of-quarter business-expense prep. Tests: PDF extraction variability,
structured-field reliability, eval-loop scoring with near-ground-truth.
Smallest delta from the existing classifier.

**2. Research paper triage.** Drop arXiv PDFs in a folder, agent reads
intro + abstract + conclusion, scores relevance against a user-interest
profile loaded via agent memory, routes to `read_now/` / `read_later/`
/ `skip/`. Real use: keeping up with a fast-moving field. Tests:
long-document handling, agent-memory shaping (interests change → memory
edits → behavior change → `memory_hash` proves it), eval scoring
without ground truth.

**3. Contract red-flag finder.** PDF contract → agent flags unusual
clauses (auto-renewal terms, IP assignment, broad indemnity, exclusive
jurisdiction, atypical termination), outputs a JSON report, routes the
original. Real use: pre-signature legal sanity check. Tests: targeted
reasoning over narrow domains, high-stakes accuracy, the kind of
workload where eval scores actually matter.

### Webhook-driven

**4. GitHub PR triage.** *(Recommended first build — see below.)*
Webhook on PR opened → agent reads title + body + diff stats → categorizes
(bug / feature / chore / docs), flags concerns (no description, no tests,
diff > N lines, touches load-bearing files) → posts a return comment via
`WebhookConnector`. Real use: solo-dev maintainer hygiene. Tests:
end-to-end webhook in *and* out (both directions of the connector
framework), unconstrained natural-language input variability, free
forever-on input stream from any repo you own.

**5. CI failure summarizer.** Webhook from a GitHub Actions failure →
agent reads the log tail → produces one-sentence cause + one-sentence
suggested fix → appends to a digest file or posts back to the PR. Real
use: faster diagnosis when builds break. Tests: log-format variability,
narrow-purpose reasoning, value even on confident wrong answers (still
faster than reading the whole log).

### Schedule-driven

**6. Weekly personal review.** Schedule fires Sunday evening → agent
reads a `notes/` folder of journal entries from the past week → produces
a digest highlighting patterns, recurring themes, and unfinished
commitments → appends to `reviews.md`. Real use: GTD-style hygiene.
Tests: schedule trigger over real wall-clock time, agent memory
accumulating across runs (the digest could itself become memory the
next week's run reads), the platform on a long-running deployment.

## Recommended starting workload

**GitHub PR triage (#4) — built.** Lives at `examples/github_pr_triage/`
with workflow YAML, agent memory rubric, five synthetic PR fixtures,
`gh api` helper scripts, and a 13-test replay-mode pytest suite at
`backend/tests/test_github_pr_triage.py`. The outbound `WebhookConnector`
half (posting the triage back as a PR comment) is the natural next step
once the offline-iteration loop plateaus — see the README's "Next
graduation steps."

Original reasoning, preserved because the same logic should apply to
picking the *second* workload:

- **Both directions of the connector framework get exercised.** Inbound
  webhook trigger + (planned) outbound `WebhookConnector` send. Every
  other candidate uses one or the other, not both.
- **Free, variable, forever-on input.** Any repo you own keeps producing
  PRs indefinitely. Other candidates need you to feed them inputs.
- **The output is something you'll actually read.** PR comments are
  inherently inspected; classification mistakes are visible.

## What to skip (capability gaps)

These workloads sound tempting but require capabilities the platform
doesn't have today. Each is on the deferred list per `docs/INTEGRATIONS.md`
or `CLAUDE.md`'s "Aggressively deferred" table — pull in only when a
customer or workload demands it.

| Tempting workload | Why deferred |
|---|---|
| Email triage (Gmail / Outlook) | OAuth + IMAP/Graph connector deferred |
| Slack message summarization | Slack connector deferred |
| Calendar prep / meeting briefs | Calendar connector doesn't exist |
| Real-time chat / streaming inputs | Engine is batch-shaped; streaming is a redesign |
| Image / video analysis | Bedrock multi-modal not wired; deferred |
| Direct DB query against external systems | No generic DB connector (S3 is the only data-source connector) |

## Process for adding more candidates

When a new candidate workload occurs to you:

1. Add it under the right trigger-driven section above with the same
   shape: brief description, real use, what it tests, capability gaps
   (if any).
2. If it requires capabilities the platform doesn't have today, file
   the gap under "What to skip" with a one-line note about which doc
   tracks the deferral.
3. If it's strong enough to displace the current "recommended first
   build," update that section and link to the conversation that drove
   the change.

## Once you pick one

Each candidate workload turns into:

1. A workflow definition YAML under `examples/<workload>/workflow.yaml`.
2. A seed agent memory file (the prompt isn't enough — most of these
   benefit from a Markdown rubric the agent reads each run).
3. A replay-mode pytest in `backend/tests/test_<workload>_workflow.py`
   that runs the whole pipeline against `FakeBedrock` + (where useful)
   committed recordings under `examples/<workload>/recordings/`.
4. A README in `examples/<workload>/` explaining how to run it live + a
   curl-line in `docs/MANUAL_TESTING.md`.
5. An update to `CLAUDE.md`'s status section once it's running real
   traffic.

That sequence is the same one the PDF classifier and the two example
workloads (webhook_echo, scheduled_health_report) already followed —
the platform should make the *fifth* such workload no different from
the second.
