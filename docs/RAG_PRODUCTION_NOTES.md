# Production RAG — notes and recommendations

Analysis of [Bhayani, "What Matters in Production RAG"](https://arpitbhayani.me/blogs/rag-production/), captured for the deferred Phase B (knowledge ingestion) work and for two small pull-forward items.

## Why this matters

Our platform does not currently do RAG. It runs LLM-driven workflows where the agent gets a task description + tool palette + (optional) Phase-A memory. Retrieval-augmented generation lives in **Phase B**: knowledge ingestion + contextual retrieval. Per `docs/LEARNING_IMPLEMENTATION.md`, that's a phase with 10 open research questions and no concrete workload pulling for it yet.

The article gives concrete answers to several of those questions. Most are useful as inputs *when Phase B starts*, not now. Two ideas in the article generalize to patterns we should pick up immediately.

## Relevant claims from the article

### Chunking is the silent failure surface

Fixed-size chunking "cuts sentences in half, separates questions from their answers in FAQ documents, and splits code across function boundaries." The article advocates:
- **Recursive splitting** — paragraphs → sentences → characters
- **Semantic chunking** — embed adjacent sentences, cut where cosine similarity drops below a threshold
- **Structure-aware splitting** — AST boundaries for code, heading boundaries for docs

Each chunk should carry `source_doc_id`, section heading, page number, creation timestamp, and a content hash.

### Document registry in Postgres

A table mapping `doc_id` → list of chunk vector IDs is the missing piece in most demos. Without it: deletes are best-effort, "partial updates create corrupted indexes where some documents are at version N, some at version N+1, and the seam between them is invisible to the retrieval layer."

### Content hashing avoids unnecessary re-embed

"If the hash matches what is in the registry, skip it entirely. Most 'updates' in practice are metadata changes that do not require re-embedding."

### Alias-based zero-downtime swap

Build the new index, validate it, atomically swap an alias. The old index stays available for rollback.

### Hybrid retrieval + reranking

Vector search alone misses keyword-strong queries; BM25 alone misses semantic equivalence. Best results: **hybrid search (vector + BM25) + cross-encoder reranking**.

### Tracing must include index version

End-to-end traces should have nested spans for embedding / vector search / rerank / prompt assembly / generation, *and* tag each retrieval span with the index version (or alias timestamp) so quality drops can be correlated with reindex events.

### LLM-as-judge for evaluation

Send `(question, retrieved_context, answer)` to a smaller, cheaper model with a rubric scoring faithfulness + relevance. Produces a queryable dataset linking retrieval quality to answer quality. Far cheaper to maintain than human-labeled eval sets.

### Embedding model shortlist (mid-2026)

- `text-embedding-3-large` (OpenAI, 3072-dim) — best general-purpose recall
- `bge-large-en-v1.5` (BAAI) — open-source, deployable locally
- `e5-mistral-7b-instruct` — instruction-tuned, strong on asymmetric retrieval

## What aligns with our existing design

| Article point | Our current state | Verdict |
|---|---|---|
| Content hashing avoids re-work | Bedrock record/replay keys responses on canonical-JSON hash | Same pattern, different layer. Validates direction. |
| Tracing tagged with version | Audit log captures every state transition with `workflow_instance_id` + `step_id`; we don't currently tag agent invocations with the memory version they saw | Principle is right; one small gap — see recommendation #2 below |
| Structured tracing across stages | `_audit("workflow_started" / "step_started" / "tool_call" / "step_completed" / ...)` already produces nested-equivalent records | Same shape; OpenTelemetry spans would be a future refinement, not a redesign |
| Postgres for registry-style metadata | `workflow_definitions`, `workflow_instances`, `step_executions`, `audit_log` all live in Postgres already | The document registry will sit cleanly alongside these tables when Phase B starts |

## Recommendations

### Pull forward now (small, useful, in scope)

**R1. LLM-as-judge eval loop for the PDF classifier.** *Effort: half a day.*

Add a second workflow under `examples/` (or extend the existing one) that, given a classified document, asks Haiku 4.5 to score the classification against a rubric (correct category? summary faithful to extracted text? key_fields supported?). Persist the scores alongside the original instance for queryability.

This is directly the article's pattern, scoped to one workflow we already have. Concretely useful: we can tune the classifier prompt over time against a real accuracy dataset instead of eyeballing it. Aligns with VISION's "learning from execution" pillar.

Open question worth deciding before building: whether the evaluator runs as a follow-up workflow (clean separation, easier to disable) or as an inline post-step on the classifier workflow (one instance, simpler dashboard view).

**R2. Memory versioning in agent audit entries.** *Effort: ~an hour.*

When `_run_agentic` loads `memory_text` from `MemoryManager`, also compute a hash (or read the mtime) and include it in the `tool_call` / `step_completed` audit detail. The same generalization the article applies to vector indexes: trace *which version* of the memory shaped the decision.

Concretely: when memory edits change agent behavior, the audit log lets us pinpoint which run was the first under the new memory. Zero cost otherwise.

### Capture as Phase B inputs (don't build now, but write down)

When Phase B starts — and only when there's a concrete workload demanding it — these are decisions already-thought-through:

**R3. Chunking strategy.** Default to recursive splitting (paragraph → sentence → character). For PDF inputs, prefer page + heading boundaries when the extraction tool can surface them. Reserve semantic chunking for cases where the recursive default underperforms in eval.

**R4. Document registry table.** Add `documents` and `document_chunks` tables next to existing schema. `documents` keys: `doc_id`, `source`, `content_hash`, `last_indexed_at`, `index_version`. `document_chunks` keys: `chunk_id`, `doc_id`, `vector_id`, `section_heading`, `page_number`, `text_preview`. Reindex flow: select chunks by `doc_id`, delete from vector store, re-embed, replace.

**R5. Content-hash short-circuit.** Before re-embedding, compare new content hash to registry. If match, do nothing. Most "updates" are metadata-only.

**R6. Alias-based deploy.** Each reindex builds to a new index name. Validate (count, sample queries, faithfulness eval). Flip an alias. Keep the previous index around for one rollback window.

**R7. Hybrid retrieval.** Vector + BM25, fused with reciprocal-rank fusion or weighted sum. Cross-encoder reranker on the top-N. Specific tooling (which vector DB, which reranker model) deferred until we know the corpus shape.

**R8. Index version in trace tags.** Every retrieval audit entry gets the index alias + version. Same shape as our current `step_id` tagging.

**R9. Embedding model choice.** Start with `bge-large-en-v1.5` if we want on-host control. Move to `text-embedding-3-large` if recall benchmarks justify the API dependency. Re-evaluate at every major model release.

### Out of scope

- **LangChain / Chroma as primary stack.** The article is skeptical and we have no use case demanding them today. If/when Phase B starts we should evaluate against `pgvector` (already an Alembic-ready extension we provisioned for) and a thin in-house retrieval layer first.
- **Knowledge graphs / GraphRAG / agentic retrieval loops.** Out of scope — separate research direction with its own open questions.
- **Switching observability to OpenTelemetry spans.** Our audit log + Prometheus metrics cover the same ground for now. Worth revisiting *if* we eventually need cross-service traces.

## Open questions surfaced

The article highlights but doesn't fully answer:

1. How do you re-evaluate embedding-model choice without re-embedding everything?
2. How do you size chunks for documents that mix prose + code + tables?
3. What's the right cadence for running the LLM-as-judge eval — every run, sampled, or batch nightly?
4. How do you handle "the user wants to ask a question about a document that hasn't been indexed yet" without a synchronous indexing path that blocks the conversation?

These belong in `docs/LEARNING_IMPLEMENTATION.md`'s open-questions section when we revisit Phase B.

## How to use this doc

- **If you're starting Phase B work**: read R3–R9 first; they're informed defaults you don't need to re-derive.
- **If you're touching agent memory or audit trails**: see R2 for a small consistency improvement.
- **If you're improving the PDF classifier**: see R1.
- **Otherwise**: nothing actionable; this is a reference.

## When this doc goes stale

- A newer / more concrete RAG-in-production reference lands → cite both, mark which supersedes which.
- Phase B work begins → migrate R3–R9 into the design corpus proper and link this doc as a reference, not a backlog.
- The article's specific tool / model recommendations become outdated (likely within 12 months given the pace).
