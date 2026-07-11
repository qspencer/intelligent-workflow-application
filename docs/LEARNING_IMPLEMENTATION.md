# Memory & Knowledge System — Implementation Plan

## Overview

This document details the implementation approach for the platform's memory and knowledge systems: how agents persist and retrieve learned information, how the knowledge library is built and maintained, and how context is managed within LLM token budgets.

---

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                     CONTEXT ASSEMBLY                             │
│  Combines system prompt + retrieved knowledge + agent memory     │
│  + current task into a token-budgeted prompt                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────┐       ┌──────────────────┐               │
│  │  KNOWLEDGE       │       │  AGENT MEMORY    │               │
│  │  LIBRARY         │       │  STORE           │               │
│  │                  │       │                  │               │
│  │  Chunked docs    │       │  Per-agent .md   │               │
│  │  Vector index    │       │  files           │               │
│  │  Metadata index  │       │  Structured      │               │
│  │  Manifest        │       │  observations    │               │
│  └────────┬─────────┘       └────────┬─────────┘               │
│           │                          │                         │
│           ▼                          ▼                         │
│  ┌──────────────────────────────────────────────┐              │
│  │           RETRIEVAL LAYER                     │              │
│  │  Semantic search · Exact lookup · Recency     │              │
│  │  Rule matching · Contextual filtering         │              │
│  └──────────────────────────────────────────────┘              │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                     INGESTION PIPELINE                           │
│  Parse → Chunk → Contextualize → Embed → Index → Register      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase A: Agent Memory (MVP)

**Goal:** Agents persist observations across executions using structured Markdown files.

**Components:**

1. **MemoryManager class**
   - `load(agent_id: str, budget_tokens: int) → str` — load memory, truncated/summarized to fit budget
   - `append(agent_id: str, entry: MemoryEntry)` — add new observation
   - `summarize(agent_id: str)` — compress old entries when file exceeds size threshold

2. **Memory file format** (see LEARNING.md, Memory Storage Format section)
   - Structured Markdown with `## Recent Observations`, `## Learned Patterns`, `## Error History`
   - Each entry timestamped for recency weighting
   - File path: `/data/memory/{agent_type}/{agent_id}.md`

3. **Memory injection**
   - On agent startup, load memory and inject into system prompt
   - Position: after system instructions, before task input (high-attention zone)
   - If memory exceeds budget, summarize older sections, keep recent verbatim

4. **Memory compaction**
   - Trigger: file exceeds 4KB (~2000 tokens)
   - Strategy: use a cheap model to summarize entries older than 7 days into a "Historical Summary" section
   - Keep last 7 days verbatim

**Implementation effort:** ~2 days

**Status (Phase 2 complete):** Shipped in Week 5. Agent memory loads from
file-backed Markdown per the format below; engine prepends it to the system
prompt as "Prior agent memory". `_run_agentic` also computes a
`sha256:<16 chars>` hash of the loaded memory and emits it as `memory_hash`
on the agent step's output, so audit-log consumers can correlate behavior
changes with memory edits. Compaction (point 4) is not yet automated —
files are short enough that manual review remains practical.

---

### Phase B: Knowledge Ingestion Pipeline

**Goal:** Documents uploaded by admins are parsed, chunked, contextualized, and made retrievable.

> **Candidate building block (evaluated 2026-07-11):** `engram`
> (`~/Dev/engram`) — provenance-aware per-entity memory (typed graph +
> episodes, third-party quarantine, BYO-LLM). Covers the *learned user/
> environment memory* slice of Phases B+ rather than document ingestion.
> Deferral rationale, integration gaps, and the reopen trigger (email-triage
> validation against real mail) are logged in `docs/SEMANTICS.md` →
> "Deferred: engram".

**Components:**

1. **Document parser**
   - PDF → text (reuse existing `pdf_extract` tool)
   - HTML/web pages → Markdown (use markdownify or similar)
   - Plain text/Markdown → pass through
   - Output: clean Markdown text + source metadata

2. **Chunker**
   - Strategy selection based on content type (see ARCHITECTURE.md table)
   - Default: heading-based splitting, fallback to sliding window (~500 tokens, 50-token overlap)
   - Never split tables, code blocks, or list items mid-element
   - Output: list of `Chunk(content, source_ref, position)`

3. **Contextual enrichment**
   - For each chunk, call a cheap LLM (Haiku-class) with the full document + chunk, asking: "Summarize what this chunk is about and how it relates to the document"
   - Store the summary alongside the chunk
   - Cost: ~$0.001 per chunk (acceptable for ingestion-time processing)

4. **Embedding & indexing**
   - Generate vector embeddings per chunk (Bedrock Titan Embeddings or similar)
   - Store in Postgres with pgvector extension (HNSW index for sub-linear search)
   - Metadata index: chunk_id, source_document, category, scope, freshness, confidence

5. **Knowledge manifest**
   - Auto-generated Markdown file listing all knowledge sources
   - Updated on every ingestion
   - Agents receive this manifest in their context to know what's available

**Implementation effort:** ~5 days

---

### Phase C: Retrieval Layer

**Goal:** Given an agent's current context (workflow type, step goal, tools, user), retrieve the most relevant knowledge.

**Components:**

1. **Query builder**
   - Constructs a retrieval query from: step goal + current data summary + active tools
   - Applies scope filters: tenant, team, workflow type

2. **Multi-strategy retrieval**
   - Semantic: embed query, find top-K similar chunks (K=5 default)
   - Exact: key-value lookups for reference data
   - Rule: evaluate policy conditions against current context
   - Recency: weight recent observations higher (exponential decay, half-life configurable)

3. **Re-ranking**
   - After initial retrieval, re-rank by: relevance score × freshness × confidence × usage_frequency
   - Optional: LLM-based re-ranking for high-stakes decisions (expensive, use sparingly)

4. **Result assembly**
   - Deduplicate overlapping chunks
   - Order by injection priority (see Context Window Management in LEARNING.md)
   - Truncate to fit context budget

**Implementation effort:** ~4 days

---

### Phase D: Context Assembly Engine

**Goal:** Combine all context sources into a single, token-budgeted prompt that respects the lost-in-the-middle phenomenon.

**Components:**

1. **Token budget allocator**
   - Total agent token budget (from policy)
   - Reserve: system prompt (fixed), task input (variable), response space (~25% of budget)
   - Remaining: split between knowledge and memory (configurable ratio, default 60/40)

2. **Prompt assembler**
   - Position 1 (beginning): System prompt + agent identity + constraints
   - Position 2: Critical retrieved knowledge (top-ranked chunks)
   - Position 3: Agent memory (recent observations, learned patterns)
   - Position 4: Supporting knowledge (lower-ranked chunks)
   - Position 5 (end): Current task input + data

3. **Overflow handling**
   - If total exceeds budget: summarize supporting knowledge first, then memory, then reduce chunk count
   - Never truncate system prompt or task input

**Implementation effort:** ~3 days

---

### Phase E: Active Knowledge Queries (Tool)

**Goal:** Agents can query the knowledge library mid-execution via a tool call.

**Components:**

1. **`knowledge_lookup` tool**
   - Semantic mode: `knowledge_lookup(query="approval policy for invoices over $10K")`
   - Exact mode: `knowledge_lookup(key="vendor_code", value="ACME-001")`
   - Returns: relevant chunks with contextual summaries

2. **Budget accounting**
   - Each knowledge_lookup consumes tokens from the agent's budget (the retrieved text counts)
   - Agent is informed of remaining budget so it can decide whether to query again

**Implementation effort:** ~1 day

---

### Phase F: Learning Feedback Loop

**Goal:** Execution outcomes feed back into the knowledge/memory system automatically.

**Components:**

1. **Outcome observer**
   - After each workflow execution: record success/failure, user corrections, time, cost
   - Compare predictions to actuals

2. **Automatic memory updates**
   - On success: append pattern to step agent memory ("this approach worked for X")
   - On failure: append error pattern ("this failed because Y — next time try Z")
   - On user correction: append preference ("user prefers A over B")

3. **Knowledge quality tracker**
   - Track which retrieved chunks led to good vs. bad outcomes
   - Degrade confidence score for chunks that correlate with failures
   - Flag stale chunks (not updated in >90 days, or contradicted by recent observations)

**Implementation effort:** ~3 days

---

## Storage Decisions

The "Initial Storage" column for agent memory is what's shipping today
(Phase A complete). The rest of the table describes the storage shape
planned for Phases B–F, which remain deferred per `CLAUDE.md`'s
re-evaluation checkpoint.

| Component | Initial Storage | Scale-up Path |
|-----------|----------------|---------------|
| Agent memory | Markdown files on disk (loaded into app memory per execution) | Postgres `jsonb` with full-text search |
| Knowledge chunks | Postgres + pgvector (HNSW index) | Dedicated vector DB (Pinecone, Qdrant) if >500K chunks |
| Knowledge manifest | Auto-generated .md file, cached in app memory | Same (lightweight, always fits in memory) |
| Embeddings | pgvector HNSW index | Same — scales to millions with partitioning |
| Metadata | Postgres | Same |
| Budget counters | Application in-memory (Python dict), flushed to Postgres periodically | Redis if multiple backend processes need shared counters |
| Active agent state | Application in-memory for execution lifetime | Redis for distributed coordination at high concurrency |

**Why not a separate vector DB initially:** pgvector with HNSW gives sub-10ms queries up to ~500K vectors. Adding a separate system (Pinecone, Qdrant) introduces network latency, operational complexity, and cost. Only justified when vector count or query concurrency exceeds what a single Postgres instance can serve.

---

## Cost Estimates

| Operation | Model | Cost per unit | Notes |
|-----------|-------|---------------|-------|
| Contextual summary (ingestion) | Haiku-class | ~$0.001/chunk | One-time at ingestion |
| Embedding generation | Titan Embeddings | ~$0.0001/chunk | One-time at ingestion |
| Memory compaction/summarization | Haiku-class | ~$0.002/agent/week | Triggered by size threshold |
| Knowledge retrieval | None (vector math) | Free | CPU-only at query time |
| LLM re-ranking (optional) | Haiku-class | ~$0.001/query | Only for high-stakes decisions |

At 1000 chunks ingested and 100 workflow executions/day: ~$0.50/day for the entire knowledge system.

---

## Research Gaps & Open Questions

### 1. Optimal Chunk Size

**Question:** What's the ideal chunk size for this platform's use cases (policies, API docs, vendor-specific patterns)?

**Current assumption:** ~500 tokens with 50-token overlap for prose; heading-based for structured docs.

**What we need:** Empirical testing with real documents from target users. Chunk size affects both retrieval precision (smaller = more precise) and context coherence (larger = more self-contained). The Anthropic contextual retrieval paper suggests the contextual summary partially mitigates small-chunk incoherence, but we haven't validated this.

**Research approach:** Benchmark retrieval accuracy at 200, 500, and 1000 token chunk sizes against a test set of queries.

---

### 2. Embedding Model Selection

**Question:** Which embedding model gives the best retrieval quality for our mixed-content knowledge base (policies + code + structured data)?

**Options:**
- AWS Bedrock Titan Embeddings (convenient, integrated)
- Cohere Embed v3 (strong multilingual, available on Bedrock)
- Open-source (e5-large, BGE) for self-hosted deployments

**What we need:** Comparative evaluation on our content types. Embedding models vary significantly in performance across domains.

**Research approach:** Create a test set of 50 queries with known-relevant chunks. Evaluate recall@5 and precision@5 across models.

---

### 3. Memory Compaction Quality

**Question:** When we summarize old memory entries, how much actionable information is lost?

**Current assumption:** A cheap model can compress 7+ day old entries without losing critical patterns.

**Risk:** Subtle patterns (e.g., "vendor X sends invoices with wrong dates every quarter-end") might be lost in summarization because they seem unimportant in isolation.

**Research approach:** Run compaction on synthetic memory files, then test whether agents still make correct decisions that depend on the compacted information.

---

### 4. Context Budget Allocation Ratio

**Question:** What's the optimal split between knowledge and memory in the context budget?

**Current assumption:** 60% knowledge / 40% memory.

**Variables:** This likely varies by step type (extraction steps need more domain knowledge; routing steps need more memory of past decisions).

**Research approach:** A/B test different ratios per step type and measure outcome quality.

---

### 5. Retrieval Trigger Strategy

**Question:** Should knowledge retrieval happen only at agent startup, or also mid-execution when the agent encounters something unexpected?

**Current design:** Both (pre-injection + `knowledge_lookup` tool). But when should the system proactively inject vs. wait for the agent to ask?

**Risk:** Pre-injecting too much wastes context budget. Relying on the agent to ask means it might not know what it doesn't know.

**Research approach:** Compare three strategies: (a) aggressive pre-injection, (b) minimal pre-injection + tool, (c) hybrid with manifest-guided pre-injection. Measure task success rate and token usage.

---

### 6. Cross-Agent Knowledge Sharing

**Question:** When one agent learns something useful, how quickly and reliably should it propagate to other agents?

**Example:** Step agent for "extract" learns that Vendor X changed their invoice format. The "analyze" step agent also needs this information.

**Current design:** Learned knowledge goes into per-agent memory. Shared knowledge goes into the knowledge library. But the boundary is unclear — who decides when a per-agent observation becomes shared knowledge?

**Research approach:** Define promotion criteria (confidence threshold, observation count, cross-agent relevance score). Test with simulated multi-agent scenarios.

---

### 7. Staleness Detection Without Ground Truth

**Question:** How do we detect that stored knowledge is outdated when we don't have an external source of truth?

**Example:** A policy chunk says "threshold is $5K" but the actual threshold changed to $10K. If no one tells the system, how does it notice?

**Possible signals:**
- Agent decisions based on this chunk are being overridden by users
- New observations contradict the stored knowledge
- The source document's URL returns different content on re-crawl

**Research approach:** Build a contradiction detector that flags when recent agent behavior diverges from stored knowledge. Evaluate false-positive rate.

---

### 8. Vector Index Scaling

**Question:** At what point does pgvector on a single Postgres instance become a bottleneck, and what's the migration path to a dedicated vector DB?

**Variables:** Chunk count, concurrent queries, embedding dimensionality, index type (flat vs. HNSW), Postgres instance size.

**Current assumption:** pgvector HNSW index handles up to ~500K chunks with sub-10ms queries on a properly sized instance. Beyond that, consider a dedicated vector DB (Qdrant, Pinecone) or Postgres partitioning.

**Research approach:** Load test with synthetic embeddings at 10K, 50K, 100K, 500K chunks. Measure query latency at p50/p95/p99 under concurrent agent load.

---

### 9. Multi-Modal Knowledge

**Question:** How do we handle knowledge that isn't text — diagrams, screenshots of forms, scanned documents with layout significance?

**Current design:** Everything is converted to text at ingestion. But some information (form layouts, org chart diagrams, process flowcharts) loses meaning in text conversion.

**Options:**
- Multi-modal embeddings (CLIP-style) for image chunks
- LLM-generated text descriptions of visual content at ingestion time
- Store images as references, retrieve and pass to multi-modal LLMs at query time

**Research approach:** Evaluate information loss on 20 representative visual documents. Compare text-only retrieval vs. multi-modal approaches.

---

### 10. Privacy-Preserving Learning Across Tenants

**Question:** Can we learn aggregate patterns across tenants (e.g., "invoices from Vendor X commonly have format Y") without leaking tenant-specific data?

**Current design:** All learning is per-tenant, never shared.

**Opportunity:** Aggregate patterns could improve the system for all tenants (similar to how spell-checkers learn from aggregate usage).

**Constraints:** Must be provably privacy-preserving. Differential privacy? Federated learning? Or simply not worth the complexity?

**Research approach:** Identify 5 categories of learnings that would benefit from cross-tenant aggregation. Evaluate whether differential privacy techniques preserve utility at our scale.

---

## Dependencies

| This component | Depends on |
|---------------|------------|
| Agent memory (Phase A) | Agent framework (IMPLEMENTATION_PLAN Phase 1.2) |
| Knowledge ingestion (Phase B) | PDF extraction tool, persistence layer |
| Retrieval layer (Phase C) | Ingestion pipeline, embedding model selection |
| Context assembly (Phase D) | Retrieval layer, agent framework token counting |
| Active queries (Phase E) | Tool framework, retrieval layer |
| Learning feedback (Phase F) | Workflow engine outcome tracking, memory manager |

---

## Success Metrics

| Metric | Target | How measured |
|--------|--------|-------------|
| Retrieval relevance | >80% of retrieved chunks rated "useful" by the agent's task outcome | Track whether retrieved knowledge correlates with successful step execution |
| Context utilization | <5% of injected tokens are "wasted" (never referenced in agent reasoning) | Analyze agent outputs for references to injected context |
| Memory-driven improvement | Agents make 30% fewer errors on repeated task types after 10 executions | Compare error rates for first vs. Nth execution of same workflow type |
| Ingestion cost | <$0.01 per document ingested | Track Bedrock costs during ingestion |
| Retrieval latency | <200ms p95 for knowledge queries | Instrument retrieval layer |
| Knowledge freshness | <5% of retrieved chunks are stale (contradicted by recent observations) | Staleness detection system (once built) |
