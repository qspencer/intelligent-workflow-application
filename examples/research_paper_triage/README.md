# Research paper triage example

Receives an arXiv paper's metadata, scores its relevance against a
reader-interest profile loaded from `agent_memory.md`, and records the
structured triage result. Second validation workload after the GitHub
PR triage example — same shape, different domain. See
`docs/USE_CASES.md` for the candidate catalog and why this one came
next.

## What you get

For each paper fired through the workflow, `step_executions.output` for
the `record_triage` step contains:

```json
{
  "parse_ok": true,
  "relevance_score": 5.0,
  "relevance_bucket": "directly_relevant",
  "summary": "Hierarchical memory tiers for LLM context management.",
  "key_concepts": ["MemGPT", "virtual memory", "long context"],
  "concept_count": 3,
  "tags": ["empirical"],
  "tag_count": 1
}
```

Queryable directly from Postgres:

```sql
SELECT
  output->>'relevance_bucket'                AS bucket,
  (output->>'relevance_score')::numeric      AS score,
  output->>'summary'                         AS summary,
  output->>'key_concepts'                    AS concepts
FROM step_executions
WHERE step_id = 'record_triage' AND state = 'completed'
  AND (output->>'parse_ok')::bool = true
ORDER BY (output->>'relevance_score')::numeric DESC, started_at DESC;
```

## Files in this directory

| File | What it is |
|---|---|
| `workflow.yaml` | Webhook trigger + agentic `triage` step + deterministic `record_triage` step. |
| `agent_memory.md` | Reader-interest profile + relevance scoring + bucket / tag catalog + output discipline. **Edit this to retune the agent.** Auto-loaded via the G6 mechanism; `memory_hash` on each run lets you correlate output drift with the edit. |
| `fixtures/*.json` | Five hand-crafted paper payloads covering: directly relevant (MemGPT-style), tangentially relevant (agent planning, no memory), methodology only (RAG eval), out of scope (computer vision), survey (long-context). Used by the pytest. |
| `data/arxiv_batch_50.json` | 50 real arXiv papers fetched on `cs.AI`/`cs.CL`/`cs.LG` × "agent + memory/context/retrieval" topics, slimmed via `fetch_papers.sh`. Committed for reproducibility — re-fire the same set across rubric iterations to compare. Regenerable via the fetcher. |
| `scripts/fetch_papers.sh` | Query arXiv API, slim to a JSON array matching the trigger payload shape. Default query targets AI / agent memory / context papers. |
| `scripts/run_one.sh` | Fire a single paper JSON through the workflow via `tools/fire.py`. |
| `scripts/run_batch.sh` | Fire an array of paper JSON objects, one at a time. |

The replay-mode pytest lives at `backend/tests/test_research_paper_triage.py`
and runs in CI. It reads the YAML, agent memory, and fixtures here
directly, so any edit is picked up on the next test run.

## Three ways to fire papers at the workflow

### Synthetic fixtures (offline, no AWS — fastest iteration)

```bash
cd backend
uv run pytest tests/test_research_paper_triage.py -v
```

13 tests, all should pass. Same checks run in CI.

### Real arXiv papers (live LLM, real variability)

Fetch 50 closed papers from arXiv on agent memory + context topics:

```bash
# One-time: get an arXiv-friendly working dir
docker compose up -d postgres
cd backend && DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
  uv run alembic upgrade head && cd ..

# Re-fetch a fresh batch if you want, or use the committed snapshot
# at data/arxiv_batch_50.json for reproducibility across iterations:
# examples/research_paper_triage/scripts/fetch_papers.sh 50 > /tmp/papers.json

DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
BEDROCK_MODE=live \
  examples/research_paper_triage/scripts/run_batch.sh \
  examples/research_paper_triage/data/arxiv_batch_50.json
```

Cost: ~$0.0003 per paper at Haiku 4.5 → ~$0.015 for 50 papers (abstracts
are 1–3KB, smaller than PR bodies). Customize the search query via the
second positional arg to `fetch_papers.sh`.

After the batch runs:

```bash
docker compose exec postgres psql -U workflow -d workflow -c "
  SELECT
    output->>'relevance_bucket' AS bucket,
    COUNT(*) AS n,
    AVG((output->>'relevance_score')::numeric) AS avg_score
  FROM step_executions
  WHERE step_id = 'record_triage'
  GROUP BY 1 ORDER BY 2 DESC;
"
```

Dashboard view: `http://localhost:4200/instances?workflow_id=research-paper-triage`

### Continuous (webhook-driven)

The webhook endpoint at `POST /api/triggers/webhook/research-paper-triage`
accepts the same payload shape. Wire it to a scheduled cron that runs
`fetch_papers.sh` and POSTs the deltas if you want a daily / weekly
reading queue. (No connector for that yet — would be a small follow-up.)

## Iterating on the rubric

The agent's behavior is shaped by `agent_memory.md`. To retune:

1. Run 50 real papers through the workflow per "Real arXiv papers" above.
2. Look at the outputs (`bucket`, `score`, `key_concepts`). Find a class
   of paper the agent misjudges — e.g. "RL with memory" being scored
   too high, or "tool use" papers being missed.
3. Edit `agent_memory.md`. The `memory_hash` recorded on each run lets
   you grep audit log for "first run after the rubric edit."
4. Re-run the same 50 papers. Diff the bucket distributions.

## Why this workflow shape

- **One agentic step.** The abstract carries enough signal for relevance
  scoring; no need for multi-pass reasoning at triage time.
- **Deterministic `record_paper_triage` after.** Surfaces structured
  fields for SQL queries without re-parsing JSON. Same pattern as the
  eval loop in the PDF classifier and the PR triage example.
- **Metadata-driven, not PDF-driven.** arXiv abstracts are designed for
  triage. Downloading + extracting PDFs would 10x the cost without
  changing categorization quality.

## Findings from the first real-world iteration

Fired the committed `data/arxiv_batch_50.json` (50 real papers from
`cs.AI` / `cs.CL` / `cs.LG` × agent-memory / context / retrieval
topics) through the workflow three times, editing only the
`agent_memory.md` rubric between runs. ~$0.44 of Haiku 4.5 spend
total, ~$0.003 per paper.

**Finding 1 — categorization is sharp on first run.** No parse
failures across 150 runs. v1 bucket distribution looked plausible
(56% directly_relevant, 28% tangentially_relevant, 12% out_of_scope,
4% methodology_only). Spot checks showed the agent correctly rejected
papers that matched on a keyword but missed on topic — e.g. "Memory-
Induced Supra-Competitive Outcomes Between Deep RL Agents in Optimal
Trade Execution" was tagged `out_of_scope` despite "Memory" in the
title, because it's about RL agents in finance, not LLM memory. The
G6 auto-loaded rubric was clearly driving behavior (memory_hash
non-null).

**Finding 2 — `case_study` was systematically over-applied.** v1
tagged 27/50 (54%) of papers as `case_study`, including pure research
papers like "DeferMem: Query-Time Evidence Distillation". The rubric
had said "industry or applied case study" — the agent interpreted
"applied" broadly. Two rounds of tightening (v2: explicit "academic
paper ≠ case study"; v3: three concrete tests with "real-world
deployment / actual users / production traffic") dropped it to 8 then
4. The remaining 4 are mostly borderline (MOSS, HANA, SimGym — all
have deployment-adjacent language in the abstract that ambiguates).

**Finding 3 — bucket distribution is stable across rubric edits.**
v1→v3 saw only small bucket shifts (directly_relevant 28→26,
out_of_scope 6→3, tangentially_relevant 14→20). Likely sampling
noise from the LLM. Tag adherence to the catalog was perfect across
all three rounds — `empirical`, `case_study`, `benchmark` were the
only tags used; no slug-style invented variants, no free-form tags.

**Finding 4 — cost per paper is ~3× lower than PR triage.** Abstracts
are smaller than PR bodies (~1-3KB vs ~5-10KB), and there's no
enrichment overhead. $0.003/paper at Haiku 4.5 = $0.30 per
hundred-paper iteration. Effectively free at solo-dev volumes.

**Finding 5 — irreducible ambiguity exists at the abstract level.**
Some categorization edges (case_study vs empirical for
deployment-adjacent papers; methodology_only vs tangentially_relevant
for methods that *might* apply) genuinely can't be resolved without
the full paper. Future graduation step: a second agentic pass that
reads the PDF for borderline cases.

## Next graduation steps

The test is already in `backend/tests/` and runs in CI. To take this
from "validation workload" to something useful:

1. **Score outputs into reading-queue files.** A deterministic step
   after `record_triage` could append titles to `read_now.md` /
   `read_later.md` / `skipped.md` files based on the bucket.
2. **Scheduled fetch + dedup.** A weekly cron fetches new papers,
   skips ones already triaged (Postgres dedupe on `arxiv_id`), fires
   the rest. Needs a small deterministic dedup step.
3. **Iterate the rubric** against a few hundred real papers per the
   loop above. The rubric in this repo is a starting point shaped for
   one specific reader profile — expect to retune.
