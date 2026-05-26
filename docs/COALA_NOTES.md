# CoALA + the Knowledge layer — applied to this platform

Notes on the **CoALA** cognitive-architecture framework for language
agents (Sumers, Yao, Narasimhan, Griffiths, Princeton, TMLR 2024,
[arXiv:2309.02427](https://arxiv.org/abs/2309.02427)) and the more
recent **"Missing Knowledge Layer"** critique (Roynard, 2026,
[arXiv:2604.11364](https://arxiv.org/abs/2604.11364)) — and how both
frameworks map onto what this platform has actually built.

Companion to `docs/AGENT_MEMORY_RESEARCH_NOTES.md` (which covers ten
2026 research papers on specific techniques). This doc is the
**framework-level lens**: a vocabulary for *talking about* future
memory decisions, not a list of techniques to consider.

## What CoALA is

CoALA proposes that any well-designed language agent can be described
along **three orthogonal dimensions**:

### 1. Memory — working + three long-term types

- **Working memory** — "active and readily available information as
  symbolic variables for the current decision cycle." Ephemeral; the
  current conversation / prompt / scratchpad.
- **Episodic memory** — "experience from earlier decision cycles":
  past trajectories, training input/output pairs, event flows.
- **Semantic memory** — "agent's knowledge about the world and
  itself": world facts and retrieved knowledge.
- **Procedural memory** — "implicit knowledge stored in the LLM
  weights" *plus* "explicit knowledge written in the agent's code."

### 2. Action space — internal vs external

- **Internal actions** mutate or read memory:
  - *Retrieval* — read from long-term memory into working memory.
  - *Reasoning* — process working memory to generate new information.
  - *Learning* — write information to long-term memory.
- **External actions** (grounding) — execute against the world and
  receive feedback as text. Physical systems, human dialogue, digital
  interfaces.

### 3. Decision procedure — a structured loop

Plan (reasoning + retrieval propose / evaluate / select candidate
actions) → Execute (grounding or learning) → Observe (feedback into
working memory) → repeat.

### Why it matters as a *vocabulary*

CoALA's primary contribution isn't a recipe — it's the language. It
turns "should our agent remember things?" into the concrete questions
"which type of memory, with which update mechanism, accessed by which
internal-action category?" That precision is what lets you avoid
building four different ad-hoc mechanisms when one would do, or
collapsing two genuinely different mechanisms into one.

## Roynard's critique: the missing knowledge layer

CoALA's taxonomy *names* semantic vs episodic but doesn't separate
their persistence semantics. Both are "long-term memory" with no
formal difference in update mechanism, decay behavior, or ownership
scope.

Roynard's example: "LoRA achieves 95% of full fine-tuning quality"
(a permanent claim that should be **superseded** when newer evidence
arrives) and "user corrected me about LoRA yesterday" (an ephemeral
experience that should **decay** unless reinforced) inhabit the same
architectural slot in CoALA. They shouldn't.

Roynard proposes **four layers, each with distinct persistence
semantics**:

| Layer | Definition | Persistence | Update mechanism | Scope |
|---|---|---|---|---|
| **Knowledge** | "What is true" | Indefinite, supersession | Append-only + provenance | Shared |
| **Memory** | "What happened" | Ebbinghaus decay | Bi-temporal event sourcing | Per-agent |
| **Wisdom** | "What works" | Durable, revision-gated | Evidence-threshold review | Multi-source |
| **Intelligence** | "Capacity to reason" | Ephemeral (inference-time) | N/A | Per-invocation |

The four boundary cases Roynard uses to disambiguate are clarifying:

- "User prefers dark mode" → **Knowledge** (verifiable fact; supersession on change)
- "When the user asks about UI, check theme preferences first" → **Wisdom** (behavioral directive; evidence-gated)
- "The user mentioned dark mode yesterday" → **Memory** (ephemeral observation; should decay)
- "Gradient clipping above 1.0 destabilizes ResNet-50 training" → both **Knowledge** (the fact) *and* **Wisdom** (the derived "set clipping ≤ 1.0" directive), with different persistence

The convergence evidence section of Roynard is the strongest part:
nine independent sources (DeepMind's Cognitive Framework, Karpathy's
LLM KB, Claude Code's 4-type taxonomy, the BEAM benchmark's near-zero
contradiction-resolution scores, several practitioner threads) all
hit the same gap without coordinating.

## Where this platform sits — CoALA / Roynard lens

Mapping our existing primitives, honestly:

| CoALA category | What we have today | Notes |
|---|---|---|
| **Working memory** | The user-message JSON dump: trigger payload + prior step outputs, plus the system prompt | `_build_user_message` in `engine/executor.py`. Naive — the whole context every call. CoALA prescribes "smarter context selection lands with knowledge retrieval (Phase B+)." We've left a comment to that effect already. |
| **Procedural memory (code)** | The workflow YAML + `FunctionRegistry` + `ToolCatalog` + the engine code itself | Explicit, version-controlled, code-reviewed. The CoALA-orthodox part of our stack. |
| **Procedural memory (LLM weights)** | Whatever Haiku 4.5 brings to the table | Out of our control. |
| **Semantic memory** | `agent_memory.md` per workflow, auto-loaded into every agentic step's system prompt | Hand-curated, static, overwrite-on-load. This is the disputed slot in Roynard's terms — see below. |
| **Episodic memory** | `audit_log` + `step_executions` in Postgres, immutable, queryable | Strong: bi-temporal-ish (we have `timestamp` + `occurred_at`), append-only, per-instance scoped. `memory_hash` audit field captures which rubric a run saw. |
| **Internal action: retrieval** | Trivial — agent sees full prior-step context, no selective retrieval | Phase B item; deferred per `BUILD_PLAN.md`. |
| **Internal action: reasoning** | Haiku's iteration loop within an agentic step (`max_iterations`) | The default. |
| **Internal action: learning** | `MemoryManager.append()` exists, isn't used today | The "operator edits the rubric in git" loop is the *only* learning path the platform exercises. The agent itself doesn't write to memory. |
| **External action (grounding)** | `Tool` ABC + connectors (file, S3, webhook, Gmail) | Capability-gated, audited. The strongest part of our stack. |
| **Decision procedure** | Workflow YAML *is* the plan; the engine executes it; the agent's iteration loop handles dynamic tool-use within an agentic step | DAG-shaped, deterministic at the workflow level, agentic only inside the steps that ask for it. Hybrid by design. |

In Roynard's four-layer lens, more pointed:

| Roynard layer | What we have | Honest status |
|---|---|---|
| **Knowledge** | None as a typed layer. Facts that the operator wants the agent to know live in `agent_memory.md` alongside everything else. | We don't separate "true things" from "behavioral directives." `agent_memory.md` for invoice extraction contains both ("Discount is the dollar amount between Subtotal and Shipping" — a fact about the data) and ("Your response MUST start with `{`" — a behavioral directive). |
| **Memory** | `audit_log` is the immutable substrate, but we don't have a decay/reinforcement mechanism over it. Queries are point-in-time. | No agent-side "memory" in Roynard's ephemeral-observation sense. The closest thing is the conversational context within a single agentic step's iteration loop, which is per-invocation and thus closer to working memory. |
| **Wisdom** | `agent_memory.md` is the *de facto* wisdom layer — rubrics tightened over iterations against real data (PR triage v1→v4, paper triage v1→v3, invoice extraction v1→v3). | Manually curated. No automated revision-gating; no evidence threshold; updates happen via `git commit` and the operator's judgment. This is fine *and* it's exactly what Roynard says happens before you build a real Wisdom layer. |
| **Intelligence** | Bedrock + the agent iteration loop. Ephemeral per-invocation. | Conforms cleanly. |

### The big picture

We have **Procedural memory and Episodic memory done well**, a
**hand-rolled hybrid of Semantic and Wisdom** stuffed into a single
file, and **no Knowledge layer** in Roynard's strict sense.

This isn't a bug — it's the right place for a single-engineer
platform with four validation workloads. The Knowledge/Wisdom split
matters when you have multiple agents sharing facts across time
windows, automated feedback loops promoting behavioral patterns, or
multi-tenant deployments where one user's preferences shouldn't bleed
into another's. None of which we have today.

## Recommendations

Three concrete (cheap to do, would land cleanly), three speculative
(wait for a pull), one principled non-do.

### C1 — Adopt CoALA's vocabulary in the docs (cheap, high leverage)

The single biggest gain from this lens is **vocabulary discipline**.
Replace ambiguous "memory" mentions in `docs/ARCHITECTURE.md`,
`docs/LEARNING.md`, and `docs/LEARNING_IMPLEMENTATION.md` with the
specific CoALA category being referenced. When `agent_memory.md` is
mentioned, the doc should specify *what kind of memory it is in CoALA
terms* (semantic with wisdom-shaped content, overwrite-on-load,
operator-curated). Future-you reading "we need to fix memory" then
has the right question to ask: which kind?

Effort: ~30 min of doc editing. Worth doing when this doc lands.

### C2 — Annotate `agent_memory.md` sections by Roynard layer (cheap, useful)

Today the rubric files mix Knowledge (dataset shape, field semantics)
and Wisdom (extraction discipline, output format rules). A simple
convention — `## Knowledge` / `## Wisdom` H2 sections in each
`agent_memory.md` — would let future-me see at a glance which parts
of the rubric should be supersession-updated (data changed → fact
changed) vs revision-gated (behavior tuning → keep history of what
worked). Doesn't require any code change today. Pays off if/when we
build the automated revision loop in S2 below.

Effort: 15-30 min per existing `agent_memory.md` (4 files).

### C3 — Capture the "memory category" question in the workload checklist (cheap)

`docs/USE_CASES.md` has a "process for adding more candidates"
section. Add one line: *"Which CoALA memory type does each part of
this workload live in?"* Forces explicit thought before the rubric
file becomes a kitchen sink.

Effort: 5 min. Belongs in the same commit as C1.

### S1 — `MemoryManager.append()` + offline consolidation (speculative)

The auto-load mechanism (G6) handles *overwrite*. The `MemoryManager`
class has an `append()` method that nothing calls. Auto-Dreamer (paper
#6 in `AGENT_MEMORY_RESEARCH_NOTES.md`) and TriMem (#7) both validate
the same pattern: accumulate observations fast, consolidate slowly.

This is the natural Phase A→B graduation. When we have a workload
that produces signal worth accumulating across runs (e.g. "the PR
reviewer corrected this triage category" feedback), `append()` lights
up, and a nightly "consolidate the past week's observations into a
compact summary that supersedes the verbose section" job becomes
real.

Don't build it now — no current workload pulls. Document the pattern
so future-me knows where to look.

### S2 — Wisdom-layer revision-gating, Roynard-style (speculative)

If C2 makes the Wisdom/Knowledge split visible in `agent_memory.md`,
the next obvious step is *evidence-gated revision* of the Wisdom
sections. Roynard's tiered model — "predictions" (single episode,
free to churn), "core patterns" (3+ corroborations, stable),
"anchors" (10+ cycles without contradiction, resist modification) —
maps onto what we already have: every rubric edit is a commit, every
run carries a `memory_hash`, every step output is queryable in
Postgres. The infrastructure to compute "how many runs has this
rubric edit survived" already exists.

This becomes real when we have automated downstream feedback (LLM-as-
judge eval running against a stable ground-truth set), not just
operator-eyeball iteration. Wait for it.

### S3 — Knowledge layer with supersession + provenance (speculative)

The case for a real Knowledge layer (Roynard's strict sense) shows
up the moment we want **shared facts across agents/workflows with
explicit provenance**. Examples:

- A customer-data dictionary that the email triage workflow, the
  invoice workflow, and a future contract-review workflow all
  consult ("customers in the Workspace primary alias are
  qrsconsulting@; intelligent.workflow.engine@ is the bot alias").
- A "the rubric thinks X but the human reviewer disagreed" facts
  log that future rubric iterations can consult.

Today: not needed. We have one operator, four workflows, every fact
inline in the matching `agent_memory.md`. When the first cross-
workflow shared fact appears, a small Knowledge store (probably JSON
or SQLite, append-only with `superseded_by` pointers and `source`
attribution) lands. Don't pre-build it.

### N1 — *Don't* implement CoALA's "agent writes to memory" affordance

CoALA explicitly lists "Learning" as an internal action category — the
agent writes to its own long-term memory. We deliberately don't expose
this today, and we shouldn't until we have a forcing reason.

MemAudit (paper #9 in `AGENT_MEMORY_RESEARCH_NOTES.md`) is the
counterweight: the moment an agent can write to its own memory,
memory-injection becomes a real attack class. For a single-engineer
platform with operator-curated rubrics, the security/clarity benefit
of "the rubric only changes via `git commit`" massively outweighs the
ergonomic loss of "the agent could write its own learnings." The
ActiveGraph forking pattern (R1 in the research notes, now built)
already gives us a clean "branch from a step, try a different rubric"
workflow without any agent-writable memory.

When this DOES become interesting: multi-tenant case + automated
feedback loop, with adequate write-isolation, provenance tracking,
and audit-friendly attribution. None of which is on the near horizon.

## How this doc relates to the other memory docs

- `docs/LEARNING.md` — the three learning dimensions (users,
  environment, execution). The "execution" dimension is what Roynard's
  Memory and Wisdom layers operationalize. Doesn't need a rewrite;
  C1's doc-vocabulary alignment touches this.
- `docs/LEARNING_IMPLEMENTATION.md` — Phases A-F. Phase A is "static
  rubric + memory_hash audit" = CoALA Semantic memory with no
  decay/supersession. S1 would push Phase A toward Roynard's Memory
  layer (consolidation). S2 → Wisdom. Phase B (knowledge ingestion)
  → Knowledge.
- `docs/AGENT_MEMORY_RESEARCH_NOTES.md` — ten 2026 papers on
  specific techniques. CoALA + Roynard are the *framework* this doc
  zooms out to. The two cross-reference each other; this doc adds
  the vocabulary, the research notes add the techniques.
- `docs/SEMANTICS.md` — the formal-ontology / knowledge-graph
  decision log. If S3 (Knowledge layer) ever becomes real, that
  layer's design lives in `SEMANTICS.md`, not here.

## How this doc goes stale

- A real Knowledge / Wisdom / Memory-decay implementation lands → move
  the implementation details to the relevant `LEARNING_IMPLEMENTATION.md`
  phase section; keep this doc as the framework lens it is.
- CoALA or Roynard gets superseded by a new framework paper → add a
  section for the newer framework; don't rewrite this one.
- The research-notes corpus gets a second reading pass that includes
  CoALA in its papers list → consolidate, don't duplicate.

## Sources

- [CoALA paper, arXiv:2309.02427](https://arxiv.org/abs/2309.02427)
  ([HTML full text](https://arxiv.org/html/2309.02427v3))
- [Roynard, "The Missing Knowledge Layer in Cognitive Architectures
  for AI Agents," arXiv:2604.11364](https://arxiv.org/pdf/2604.11364)
- [alphaXiv CoALA overview](https://www.alphaxiv.org/overview/2309.02427)
- [Cognee blog: CoALA explained](https://www.cognee.ai/blog/fundamentals/cognitive-architectures-for-language-agents-explained)
- [DEV community: The 7-Layer Memory Architecture Behind Modern AI Agents](https://dev.to/mahmoudz/the-7-layer-memory-architecture-behind-modern-ai-agents-5060)
  (practitioner perspective; treat as background, not authoritative)
- This codebase: `docs/AGENT_MEMORY_RESEARCH_NOTES.md` (companion),
  `docs/LEARNING.md`, `docs/LEARNING_IMPLEMENTATION.md`,
  `docs/ARCHITECTURE.md` D5/D6 sections.
