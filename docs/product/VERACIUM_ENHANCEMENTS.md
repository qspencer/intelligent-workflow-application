# Veracium Enhancements for the Intelligence Layer

## Context

Veracium (v0.2.1, MIT licensed) is the provenance-aware memory plug-in used by the Intelligent Workflow Platform's learned memory system. The workflow platform currently uses veracium in **write-only mode** — it stores observations about entities (email senders, vendors, document patterns) but never reads them back into agent context at execution time.

The Intelligence Layer requires three tiers of capability. This document assesses what veracium already provides, what it doesn't, and what enhancements (if any) are needed at the veracium level vs the workflow platform level.

---

## Current Veracium Capabilities (v0.2.1)

| Capability | Status | API |
|-----------|--------|-----|
| Per-entity typed graph (edges with provenance) | ✅ | `remember()` |
| Dated episodes (interaction history) | ✅ | `remember()` |
| Curated wiki (compiled from edges + episodes) | ✅ | Auto-recompiled after N writes |
| **Recall with token budget** | ✅ | `recall(user_id, query, token_budget=N)` |
| Query-matched subgraph + recent episodes | ✅ | `recall()` returns `edges`, `episodes`, `context` |
| Provenance/authorship (user/third_party/system) | ✅ | `EvidenceAuthor` enum |
| Third-party claim quarantine | ✅ | Structural; never assertable |
| Supersession with history retention | ✅ | Functional facts: one current, prior retained |
| Abstention gate (grounded vs unverified) | ✅ | `answer()` |
| Volatility-driven lifecycle | ✅ | `maintain()` — lapse/flag/consolidate |
| Explicit feedback (dispute/confirm) | ✅ | `dispute()`, `confirm()` |
| Token-budgeted recall with priority trimming | ✅ | facts → claim flags → wiki → episodes |
| Export/import (JSONL) | ✅ | `export_memory()`, `import_memory()` |
| Compliance erasure | ✅ | `forget()` |
| BYO model (Complete callable) | ✅ | Works with any LLM |
| Operation audit log | ✅ | Content-free JSONL |
| MCP server | ✅ | `remember` / `recall` / `answer` / `maintain` tools |

---

## Intelligence Layer Requirements vs Veracium

### Tier 1: Memory (Recall Injection)

**Goal:** When the email triage agent processes a message from sender X, inject "here's what I've observed about sender X from prior messages" into agent context.

| Requirement | Veracium provides? | Notes |
|------------|-------------------|-------|
| Entity-keyed retrieval | ✅ **Yes** | `recall(user_id, query)` — the query can be the entity name/address; the subgraph is entity-matched |
| Token-budgeted context | ✅ **Yes** | `recall(user_id, query, token_budget=500)` — priority trimming built in |
| Provenance-separated output | ✅ **Yes** | `Recall.grounded` vs `Recall.unverified` — agent sees both, flagged appropriately |
| Ready-to-inject text block | ✅ **Yes** | `Recall.context` is pre-rendered Markdown, ready to drop into a system prompt |
| Raw edges for custom rendering | ✅ **Yes** | `Recall.edges` + `Recall.episodes` — the workflow platform can render its own format if needed |

**Verdict: No veracium enhancement needed for Tier 1.** The `recall()` API already does exactly what the workflow platform needs. The gap is entirely on the workflow platform side — it needs to call `recall()` before agent execution and inject the result into the agent's context. This is the "G10" task in the workflow platform backlog.

**Workflow platform work:**
1. Before each agentic step, extract entities from the input (sender, vendor name, document type)
2. Call `mem.recall(user_id=entity_id, query=task_context, token_budget=allocated_budget)`
3. Inject `recall.context` into the agent's system prompt per the lost-in-the-middle ordering
4. Record which entities were consulted in the audit trail

---

### Tier 2: Pattern Recognition

**Goal:** Detect cross-execution patterns like "this step fails 30% for vendor X" or "this agentic step always produces the same logic."

| Requirement | Veracium provides? | Notes |
|------------|-------------------|-------|
| Aggregate queries across entities | ❌ No | Veracium is per-user/per-entity; no cross-entity analytics |
| Temporal queries (facts since date X) | ⚠️ Partial | Episodes are dated. Edges have timestamps. But no explicit "give me all edges written after date X" API. |
| Observation count / frequency | ⚠️ Partial | Reinforcement refreshes validity but doesn't expose "seen N times" as a queryable count |
| Bulk entity listing | ❌ No | No "list all user_ids" or "entities with N+ observations" API |
| Pattern/trend detection | ❌ No (out of scope) | Veracium is a memory store, not an analytics engine |

**Verdict: Tier 2 mostly lives outside veracium.** Pattern recognition queries the workflow platform's own execution data (audit logs, step outputs, cost attribution) — not the memory store. Veracium stores *what agents learned about individual entities*; Tier 2 needs *aggregate statistics across all executions*.

**However, two minor veracium enhancements would help:**

| Enhancement | Use case | Effort |
|-------------|----------|--------|
| `list_users(store)` or `Memory.list_entities()` | "Which entities have accumulated memory?" — needed to know what to recall proactively | Small (query distinct user_ids from the store) |
| Temporal edge query: `edges_since(user_id, after_date)` | "What did we learn about this vendor in the last week?" — supports change-detection | Small (WHERE timestamp > X on the existing schema) |

These are convenience methods. The workflow platform could also query the SQLite store directly (it's a documented schema), but proper API methods are cleaner.

---

### Tier 3: Autonomous Optimization

**Goal:** The system acts on detected patterns — switching models, generating code, adjusting thresholds.

| Requirement | Veracium provides? | Notes |
|------------|-------------------|-------|
| Store optimization decisions | ✅ Could use `remember()` with `author=SYSTEM` | "Switched step X to Haiku based on 200 observations" |
| Track decision outcomes | ✅ Could use `remember()` + supersession | "Haiku switch saved $240/mo; quality unchanged" |
| Read back for future decisions | ✅ `recall()` | "What optimization decisions have I made for this workflow?" |

**Verdict: No veracium enhancement needed for Tier 3.** The optimization engine lives entirely in the workflow platform. Veracium can optionally be used to store optimization *decisions* as system-authored facts (an audit of what was changed and why), but this is optional — the workflow platform's own audit log may be more appropriate.

---

## The One Critical Integration Gap

The workflow platform's current veracium integration has `wiki_recompile_after_writes` disabled (set to 0). This means recall renders the raw subgraph directly without a compiled wiki view.

**For Tier 1 to work well at scale, this needs reconsideration:**

- With 5 observations about an entity → raw subgraph is fine
- With 50 observations → the compiled wiki provides a coherent, deduplicated summary that fits in a token budget far more efficiently

**Recommendation:** Enable wiki recompilation (set `wiki_recompile_after_writes` to 8 or so) once entities accumulate enough observations. The LLM cost is one compile call per 8 writes — routed through BedrockClient, so it's metered and auditable within the workflow platform's cost infrastructure.

---

## Veracium Roadmap Items Relevant to the Intelligence Layer

Several items on veracium's deferred roadmap align with intelligence layer needs:

| Veracium roadmap item | Intelligence layer relevance | Priority for this project |
|----------------------|------------------------------|--------------------------|
| **Proactive recall** (query=None, surface follow-ups/unresolved/dated commitments) | High — could surface "vendor X's contract expires next week" without being asked | High |
| **Procedural outcome-tracking** (times-used / last-outcome on work-knowledge edges) | High — directly maps to "this approach worked/failed N times" | High |
| **Background memory-quality audit** (contradiction/staleness/redundancy sweep) | Medium — catches conflicting facts before they confuse agents | Medium |
| **Neo4j/Postgres Store adapter** | Medium — the workflow platform uses Postgres; sharing a store reduces operational complexity | Medium |
| **Embedding fallback for non-entity recall** | Low — the workflow platform handles its own vector search via pgvector | Low |
| **Access scopes + sensitivity tags** | Low — the workflow platform has its own capability model | Low |

---

## Summary of Required Enhancements

### Veracium enhancements (small)

| # | Enhancement | Tier | Effort | Description |
|---|------------|------|--------|-------------|
| V1 | `Memory.list_entities()` | 2 | ✅ **Shipped in 0.2.1** (`0dc62f7`) | Return distinct user_ids with observation counts; enables proactive recall and pattern detection |
| V2 | `edges_since(user_id, after_date)` | 2 | ✅ **Shipped in 0.2.1** (`0dc62f7`) | Temporal query for change detection ("what's new about entity X?") |
| V3 | Proactive recall (roadmap item) | 1+ | 1 week | `recall(user_id, query=None)` surfaces dated commitments, follow-ups, unresolved items — the "what should I know before starting?" query |
| V4 | Procedural outcome-tracking (roadmap item) | 2 | 1-2 weeks | Track times-used / last-outcome on work-knowledge edges; enables "this approach worked 47/50 times" |
| V5 | Postgres Store adapter (roadmap item) | Ops | 1 week | Eliminate the separate SQLite file; share the workflow platform's Postgres instance |

### Workflow platform work (the real gap)

| # | Work | Tier | Effort | Description |
|---|------|------|--------|-------------|
| W1 | Recall injection before agent execution | 1 | 3-5 days | Extract entities from input → `recall()` → inject into prompt |
| W2 | Enable wiki recompilation | 1 | 1 day | Set `wiki_recompile_after_writes=8` once entity observations accumulate |
| W3 | Pattern detection service | 2 | 2-3 weeks | Aggregate query over execution data (audit logs, step outputs); surface patterns |
| W4 | Insights panel in dashboard | 2 | 1 week | UI to display detected patterns and recommendations |
| W5 | Model recommendation engine | 2-3 | 2 weeks | model × step-type × outcome correlations → cheaper model suggestions |
| W6 | Proactive suggestion engine | 3 | 2 weeks | "You've handled N of these manually" + optimization digest |
| W7 | Auto-model degradation | 3 | 1 week | When budget_action=degrade, swap to next-cheapest and retry |

---

## Key Insight

**Veracium is already capable of supporting Tier 1 (recall injection) with zero changes.** The `recall()` API with `token_budget` is exactly what the workflow platform needs. The gap is entirely in the workflow platform's integration — it writes but doesn't read.

For Tier 2, the work is primarily in the workflow platform (pattern detection over its own execution data). Veracium's small enhancements (entity listing, temporal queries, outcome tracking) make the pattern detection more capable but aren't blockers.

For Tier 3, veracium isn't involved — autonomous optimization is workflow engine logic that acts on patterns detected by Tier 2.

**Net: veracium needs ~1-3 weeks of enhancement work (V3-V5 — V1 and V2 already shipped in 0.2.1). The workflow platform needs ~8-10 weeks (W1-W7). The critical path is the workflow platform integration, not veracium.**
