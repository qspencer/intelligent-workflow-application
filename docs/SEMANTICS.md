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
RDF over typed Python doesn't justify the maintenance burden. (Talisman's
["Choosing the Right Graph"](https://jessicatalisman.substack.com/p/choosing-the-right-graph)
frames the same trade: RDF/OWL earns its keep for cross-organization
publishing and formal reasoning — neither of which "structure in the prompt
for our own LLM" triggers. Typed Python clears the bar.)

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

Performance is also less of a draw than the marketing implies. Graph-DB
benchmarks are notoriously rigged: the famous Neo4j "1,135× faster"
figure was measured against an *unindexed* MySQL; with proper indexing,
Postgres/MySQL match or beat Neo4j at one- to two-hop depths, and modern
RDF triple stores answer billion-triple queries in milliseconds on
commodity hardware. Our hypothetical traversals are shallow, so the
Postgres + JSONB default very likely holds without a dedicated graph DB.

**If you reopen, pick the *kind* of graph first.** Per Talisman,
["Choosing the Right Graph"](https://jessicatalisman.substack.com/p/choosing-the-right-graph)
(May 2026), "knowledge graph" conflates two technologies with different
economics:
- **RDF / semantic-web** (triples, global IRIs, open-world assumption,
  OWL/RDFS reasoning, SPARQL federation) — right when the dominant
  problem is *meaning, cross-organization integration, formal reasoning,
  or linked-open-data publishing*.
- **Labeled property graph** (Neo4j / Memgraph / AGE; Cypher/GQL,
  first-class edges with arbitrary properties, closed-world) — right when
  the dominant problem is *operational multi-hop traversal, rich edge
  attributes, or developer velocity*.

Both of our buys (#1 cross-workflow traversal, #2 GraphRAG) are LPG-shaped
operational needs; neither publishes a graph across org boundaries or runs
a description-logic reasoner, so RDF/OWL would be overkill. If we reopen,
evaluate an LPG (or just Postgres — see above), not a triplestore. Caveat:
this article is a *classical* graph lens — it says nothing about vectors,
RAG, or embeddings, so for #2 it answers "which graph," not "graph vs.
pgvector." That comparison stays in `docs/RAG_PRODUCTION_NOTES.md`.

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

## Adopted: veracium (provenance-aware per-entity agent memory)

**What it is.** [`veracium`](file:///home/ubuntu/Dev/veracium) — formerly named
`engram` (renamed upstream 2026-07-12; older commits and notes use the old
name) — a local Python library (v0.1.0, source-install, MIT) giving agents
durable per-user memory:
typed graph edges + dated episodes as store of record, supersession instead
of erasure, and — its distinctive property — *authorship as a security
control*: third-party content (received email, external docs) is structurally
quarantined as claims-by-the-claimant, never facts-about-the-user, with an
abstention gate on recall. BYO-LLM (`Complete` callable with per-role model
routing), embedded SQLite, pluggable store. Evaluated hands-on 2026-07-11;
working demo at `~/Dev/veracium-demo` (installed, ran end-to-end, quarantine
behavior verified against a live model).

**What it would buy.** The memory dimension the platform *doesn't* have:
today's `MemoryManager` holds operator-curated rubrics per agent step
(static guidance); veracium would add *learned, per-entity* memory across runs
— exactly `LEARNING.md`'s users/environment dimensions and the deferred
Phase B+ territory. Sharpest fit: **email triage**, where per-correspondent
memory and the received-mail-is-untrusted quarantine map one-to-one onto
concerns the platform already treats as first-class (cf. `EvidenceAuthor.
THIRD_PARTY` vs. our own trigger-delivers-untrusted-payload posture).

**The trigger fired (2026-07-12/13).** The email-triage live validation
(`examples/email_triage_live/`, ~1 day live + a 107-message two-week
historical batch) produced two of the three named failure classes:
*repeat-sender inconsistency* (Covetrus 4× fyi / 1× spam; a newsletter
re-classified from scratch 32×) and the *quarantine case* (a "Security
alert" email's claim asserted as fact in the triage summary, in an inbox
that also contained a real spoofing warning). The third candidate —
awaiting-reply correctness — was reclassified by the design review as a
*sent-mail-state* gap, not a memory-shaped one: veracium only helps there
once something observes the user's replies. Not counted as evidence.

**Design review (2026-07-13): adopt, write-only first slice, three
conditions.** The maturity objection was stale (veracium reached PyPI at
0.1.6 with active security fixes); dependency posture is an exact-version
PyPI pin, no vendoring. The conditions, all implemented:

1. **COALA N1 stands** — the *engine* writes observations after a
   successful run; agents have no memory-write tool. Email-derived content
   enters as `THIRD_PARTY` (claims quarantined, never user facts); every
   write lands a `memory_observed` audit entry with a content hash and
   provenance counts, mirroring `memory_hash`.
2. **Dry-run isolation** via an ephemeral scratch DB discarded with the
   run — the observe path executes for real (live Bedrock distill, audit)
   but the real store is never touched. Rejecting memory-enabled dry runs
   would have gutted the C6 trust wedge; a World-mediated store was judged
   premature.
3. **All veracium LLM calls route through `BedrockClient`** (a sync
   `Complete` adapter that hops back onto the event loop), so they inherit
   cost metering, audit, record/replay, and land in instance token/cost
   totals. The SQLite thread-safety question resolved: veracium's store is
   `check_same_thread=False` + lock, and the service serializes observes.

**What's implemented (the write-only slice).**
`workflow_platform.memory.learned.LearnedMemoryService` + an opt-in
`learned_memory:` block on `WorkflowDefinition` (`user_id` +
templated `observations`, each with `author` / `event_type` /
`date_from` / `ref_from`). The engine ingests observations in
`_observe_learned_memory` after a run COMPLETEs; failures audit
(`memory_observe_failed`) and never fail the run. The curated-wiki
recompile is disabled (`wiki_recompile_after_writes=0`) so the write path
is exactly one cheap-tier distill call per observation. Store:
`WORKFLOW_PLATFORM_LEARNED_MEMORY_DB` → default `.memory/learned.db`.
First consumer: `examples/email_triage_live/` (mail as `third_party`,
triage verdict as `system`). Pinned by `tests/test_learned_memory.py`.

**Slice 2 (recall injection) landed 2026-07-17** — see
`docs/NEXT_STEPS.md` §G10 for the implementation record (verbatim fence
injection + normalized entity keys, per the veracium dev session's
security requirements). **Still explicitly out (each needs its own
pull):** sent-mail observation (would make awaiting-reply decidable), the
Postgres-backed store (decided contribute-later, triggers recorded in
NEXT_STEPS), outcome-event emission (waiting on veracium 0.3.x merging
the validated PR #9), and the MCP surface. Note the standing caveat from
the knowledge-graph section: adopting an embedded typed graph *partially*
reopens that deferral — if cross-workflow traversal queries materialize,
re-read it.

**Rubric memory is unchanged:** `MemoryManager` structured-Markdown
rubrics remain the operator-curated guidance channel; veracium adds the
*learned* users/environment dimension alongside, per `LEARNING.md`. (The
"all learning artifacts are structured Markdown" statement in LEARNING.md
is superseded in part — recorded there.)

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
