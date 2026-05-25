# Semantic-layer / knowledge-graph / ontology decisions

How we represent meaning in the platform today, and what's been
considered but deferred. This is a single-purpose decision log; each
section names the option, what it would buy, the decision trigger that
would reopen it, and what to default to until then.

This doc exists because each of these tools sounds plausible in
isolation, and the project keeps not adopting them. Capturing the
"why not" once means future-us can re-derive the trade in a minute
instead of re-discovering it.

## Where meaning lives today

| Concern | Current representation |
|---|---|
| Platform concepts (workflow, step, agent, trigger, etc.) | Pydantic v2 models in `backend/src/workflow_platform/` |
| Static agent guidance (rubrics, categorization rules) | Structured Markdown in `examples/<workload>/agent_memory.md`, loaded into `MemoryManager` at workflow-load time (see G6) |
| Step outputs (PR triage scores, classification results, eval scores) | JSONB on `step_executions.output`, with deterministic parsers (`record_pr_triage`, `record_evaluation`) lifting structured fields |
| Cross-run analysis | Hand-rolled SQL + endpoints (`/api/cost/by-{workflow,model,day}`, `/api/escalations`) and Prometheus metrics (`workflow_runs_total` family) |
| Audit trail | `audit_log` table + `EventBus` mirror + frontend WS subscription |

This is enough for one engineer, one production workload, and ~10
distinct metrics. Below: what we'd add when that stops being enough.

---

## Deferred: formal ontology

**What it is.** An explicit schema (OWL/RDF or a structured Markdown
spec) of every platform concept and the relationships between them —
beyond what the Pydantic models implicitly encode.

**What it would buy.** Mostly one thing: letting an LLM compose
workflow definitions from natural language. With a formal ontology in
the prompt, the model knows exactly which trigger types exist, which
tools each step type can use, which capabilities gate which tools, etc.

**Why we don't have it today.** The Pydantic schemas + docstrings + the
`agent_memory.md` rubric pattern give the model enough structure for
the current single-workload use case. The marginal benefit of formal
RDF over typed Python doesn't justify the maintenance burden.

**Decision trigger to reopen.** The day generative-UI work moves out of
`docs/GENERATIVE_UI.md`'s deferred state and into active development.
That work asks the LLM to *produce* workflow YAMLs from prose. An
explicit ontology becomes load-bearing then.

**Until then, default to:** Pydantic models with rich docstrings. Add
new platform concepts as classes, not as RDF triples.

---

## Deferred: knowledge graph

**What it is.** A graph database (Neo4j, AGE, Memgraph) or
graph-as-data-layer pattern that stores entities and relationships
explicitly, supporting multi-hop traversal queries.

**What it would buy.** Two distinct things, evaluated separately:

1. **Cross-workflow analytical traversals.** "Show me every workflow
   whose audit log calls a tool that's only available when capability
   X is granted" — a 4-hop query. Tabular SQL on JSONB gets awkward
   here.
2. **GraphRAG-style document retrieval in Phase B.** When the
   knowledge-ingestion pipeline lands, entity-rich content (legal
   contracts, scientific papers, regulatory text) is recall-sensitive
   in ways vector embeddings alone miss. Modeling entities + relations
   in a graph supplements vector retrieval.

**Why we don't have it today.** Every analytical query we actually
write is either (a) "instances of workflow X" — SQL — or (b)
"distribution of <field> in step output" — SQL on JSONB. We have not
yet hit a query that wants multi-hop traversal. And Phase B itself is
deferred per the re-evaluation checkpoint.

**Decision trigger to reopen.** Either:
- We accumulate three or more analytical queries that genuinely span
  workflow → tool → connector → capability → audit-entry-style paths.
  At three, that's a pattern. (We're at zero.)
- Phase B work begins with an entity-rich document corpus. Then GraphRAG
  becomes a real option to evaluate against pure pgvector + BM25 +
  cross-encoder reranking (the current Phase B default per
  `docs/RAG_PRODUCTION_NOTES.md`).

**Until then, default to:** Postgres + JSONB + pgvector when Phase B
starts. Reach for graph storage only after benchmarking against vector
+ keyword + rerank shows real recall gaps on the actual corpus.

---

## Deferred: semantic layer

**What it is.** A definitions layer (dbt-sl, Cube, AtScale, Looker LookML)
that codifies business metrics once and serves them consistently across
UI, API, alerts, and exploratory queries.

**What it would buy.** Single-source-of-truth for metrics: "average
concerns per PR triaged" defined once, queryable identically from
Postgres SQL, the dashboard, the cost API, and Prometheus. Avoids
metric drift between surfaces.

**Why we don't have it today.** We have ~10 metrics across 4 cost
endpoints + 6 Prometheus metric families. One production workload (PR
triage), one engineer, no analyst seat. The overhead of a semantic
layer (a new dependency, a new query language, a new build step in CI)
exceeds the benefit at this scale.

**Decision trigger to reopen.** Either:
- A second or third real workload lands and we find ourselves defining
  overlapping metrics — e.g. "concern count" in PR triage vs. "issue
  count" in some other workload — that should be one concept.
- Metric drift between surfaces becomes a real bug ("the API says X,
  the dashboard says Y, the Prometheus counter says Z").

**Until then, default to:** hand-rolled `CostReportService`-style
aggregation, with care to define each metric in exactly one place
(currently `backend/src/workflow_platform/cost/`).

---

## How to use this doc

- **If you're tempted to add an ontology / graph DB / semantic layer:**
  re-read the relevant section above. The trade is captured;
  re-derive only if reality has changed (e.g. a new workload, an
  emerging gap). Updating this doc with what changed is part of
  reopening the decision.
- **If you're starting Phase B:** read the knowledge-graph section
  alongside `docs/RAG_PRODUCTION_NOTES.md`. The defaults compose:
  vector + keyword + rerank first; graph only if the corpus needs it.
- **If you're adding a new metric:** define it in exactly one place,
  cross-link it from any other place that exposes it. When you find
  yourself defining the same metric twice, that's the signal to
  reopen "semantic layer."

## When this doc goes stale

- A decision trigger above fires → update the relevant section: move
  it from deferred to active, link to where it's implemented.
- A new option not in this doc gets considered → add it with the same
  shape (what / what it buys / decision trigger / default until then).
- The defaults stop being defaults (e.g. we're already on pgvector
  but the doc still treats it as Phase B) → update.
