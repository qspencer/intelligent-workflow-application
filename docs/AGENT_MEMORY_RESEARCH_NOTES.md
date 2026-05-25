# Agent-memory research notes

Reading notes on ten recent papers on LLM-agent memory and context, with
explicit links back to design choices in this platform. The papers were
selected by relevance score from the research-paper-triage workload
(v3 batch, score ≥ 4, May 2026 arXiv corpus). Methodology is reproducible
— same `agent_memory.md` rubric, same `data/arxiv_batch_50.json`.

The goal here is "what's worth carrying forward" — every paper is either
(a) something we should consider building when a workload pulls for it,
(b) validation of a design choice we've already made, or (c) a pattern
to capture in `docs/SEMANTICS.md` or `docs/RAG_PRODUCTION_NOTES.md`.

## Papers

### 1. DeferMem — Query-time evidence distillation via RL

> Yin, Tang. *"DeferMem: Query-Time Evidence Distillation via Reinforcement Learning for Long-Term Memory QA."* arXiv:2605.22411 (May 2026).

**Core idea.** Most RAG-style memory systems retrieve based on
similarity, then expect the downstream LLM to denoise the candidates.
DeferMem inverts the split: a lightweight retrieval layer pulls a
*deliberately broad* candidate set, then a separately-trained
"distiller" model rewrites that set into focused, query-conditioned
evidence at query time. Training uses DistillPO, an RL algorithm with
a decomposed-and-gated reward pipeline (validity → quality → task
correctness).

**Why it matters here.** When we eventually do Phase B (knowledge
ingestion), the "retrieve big, distill at query time" pattern is a
direct alternative to the current default in
`docs/RAG_PRODUCTION_NOTES.md` (vector + BM25 + cross-encoder rerank).
Worth adding to that doc as a third option to evaluate against the
hybrid+rerank baseline. Particularly relevant for long-document QA
where the relevant span is buried.

**Where it would land.** Not now. Future Phase B retrieval module.

---

### 2. ActiveGraph — Event-sourced reactive graphs

> Nakajima. *"The Log is the Agent: Event-Sourced Reactive Graphs for Auditable, Forkable Agentic Systems."* arXiv:2605.21997 (May 2026).

**Core idea.** Most agent frameworks treat the LLM loop as primary and
bolt logging on. ActiveGraph inverts: the append-only event log is the
*source of truth*, the working graph is a deterministic projection of
that log, and behaviors react to graph changes by emitting new events.
Three properties fall out: deterministic replay from the log, cheap
forking (branch a run at any event without re-executing the prefix),
and end-to-end lineage from goal to model call.

**Why it matters here.** We're already close to this design without
having named it. `audit_log` is our append-only event log, `step_executions`
+ `workflow_instances` are deterministic projections from it, and Bedrock
record/replay gives us deterministic replay of the LLM half. The third
property — forking — is the one we don't expose. A "fork this instance
at this step" affordance on the dashboard would be a small addition
that delivers a real superpower: re-run a workflow from a specific
audit-log point with a tweaked prompt or rubric, without redoing the
earlier steps.

**Where it would land.** Add "fork from step" as a new gap in
`docs/NEXT_STEPS.md` (G8?). The backend already has everything needed —
the `engine.resume(definition, instance_id)` path filters `already_done`
step IDs. A "fork" endpoint would clone an instance and set
`already_done` to all steps before the fork point.

Validation: this paper independently arrives at the architecture we
chose for different reasons. Worth citing in `docs/ARCHITECTURE.md`'s
D5/D6 sections.

---

### 3. Memory-R2 — Fair credit assignment for memory-augmented agents

> Yan, Bahloul, Nie et al. *"Memory-R2: Fair Credit Assignment for Long-Horizon Memory-Augmented LLM Agents."* arXiv:2605.21768 (May 2026).

**Core idea.** Training memory-augmented agents with RL has a
fundamental subtlety: different rollouts write different memories, so
their intermediate environments diverge. Group-relative methods like
GRPO assume rollouts share an environment. LoGo-GRPO combines local
group-relative comparisons (rollouts from the same intermediate
memory state) with the global trajectory-level signal. A progressive
curriculum extends horizon from 8 → 16 → 32 sessions.

**Why it matters here.** We don't train models. But the observation
that "memory turns past actions into part of the future environment"
is exactly why our `memory_hash` audit-log field exists — it captures
the environment shift caused by memory edits. The paper validates
that memory-as-environment is a real thing worth tracking.

**Where it would land.** Cite in `docs/LEARNING_IMPLEMENTATION.md`'s
Phase A status note as justification for why `memory_hash` matters.
No code change.

---

### 4. Mem-π — Generate memory instead of retrieving it

> Wang, Wang, Nekoei et al. *"Mem-π: Adaptive Memory through Learning When and What to Generate."* arXiv:2605.21463 (May 2026).

**Core idea.** Instead of retrieving from a memory store, a separate
LM (vision-language model in this work) *generates* context-specific
guidance on demand, conditioned on the agent's current context. A
decision-content decoupled RL objective lets the model abstain when
generation wouldn't help, and produce concise guidance otherwise.
30%+ relative improvement on web navigation versus retrieval baselines.

**Why it matters here.** Our agent memory is static Markdown today —
the rubric is the same every call. Mem-π suggests a "rubric
generator" that adapts the rubric to the specific input. For PR
triage, this could mean: a small upstream LLM rewrites the rubric to
emphasize concerns relevant to *this PR's repo* (e.g. extra focus on
migrations for a database repo). The "abstain when not helpful" piece
is the cost-control insight — most calls don't need a tailored rubric.

**Where it would land.** Not now. Could become a workflow pattern
("rubric customizer step before triage step"). Document as a future
direction in `docs/LEARNING.md`.

---

### 5. MemGym — Memory-isolated evaluation for agents

> Xu, Wang, Mei et al. *"MemGym: a Long-Horizon Memory Environment for LLM Agents."* arXiv:2605.20833 (May 2026).

**Core idea.** Existing memory benchmarks evaluate multi-turn chat
retention. MemGym is a benchmark spanning tool-use dialogue, deep
research, coding, and computer use — and crucially, it reports
*memory-isolated* scores that decouple memory performance from
reasoning, retrieval, and tool-use ability. They also train MemRM, a
1.7B-parameter QLoRA-finetuned scorer to replace expensive Docker
rollouts.

**Why it matters here.** Our LLM-as-judge eval loop (`record_evaluation`
in the PDF classifier example) scores classification quality
end-to-end — we can't tell whether a low score reflects bad memory,
bad reasoning, or bad input parsing. MemGym's "memory-isolated"
methodology would inform a future eval design: run the same workflow
twice (with and without memory loaded), score the delta, attribute
performance specifically to memory.

**Where it would land.** Capture as a methodology note in
`docs/LEARNING_IMPLEMENTATION.md` Phase A: "when we have multiple
memory-using workflows, eval memory by ablating it." Not actionable
yet because we have one memory-shaped workload (paper triage). The
small-model-as-scorer trick (MemRM) is also worth noting as a
cost-control technique for the eval loop.

---

### 6. Auto-Dreamer — Offline memory consolidation

> Ye, Liu, Wang et al. *"Auto-Dreamer: Learning Offline Memory Consolidation for Language Agents."* arXiv:2605.20616 (May 2026).

**Core idea.** Complementary learning systems theory says memory needs
both fast online acquisition AND slow offline consolidation. Existing
agent-memory systems couple them. Auto-Dreamer decouples: a consolidator
operates on a "selected working region" of a typed memory bank as
read-only evidence, then synthesizes a compact replacement set that
supersedes the original. Trained via GRPO on end-to-end agent
performance. 12× smaller memory bank than baselines while improving
accuracy.

**Why it matters here.** Phase A memory in our system today is
*acquisition only*. As workloads run for weeks, the
`agent_memory.md` rubric stays static but accumulated observations
(if we ever start appending them) would grow unbounded. An offline
consolidator job — runs nightly, summarizes the past week of
observations, replaces the verbose section with a compact
summary — directly maps to the design here.

**Where it would land.** When we promote Phase A's `MemoryManager`
from "static rubric only" to "rubric + accumulating observations",
add a "compaction" step. The `MemoryManager.append()` API exists but
isn't used today; once it is, consolidation becomes a real concern.
Add to `docs/LEARNING_IMPLEMENTATION.md` Phase A scope, with
Auto-Dreamer cited as prior art.

The "typed memory bank" framing — different memory regions with
different consolidation rules — is also a useful architectural hint
when we expand beyond a single `agent_memory.md` file.

---

### 7. TriMem — Multi-granularity memory representation

> Sun, Zhu, Yao et al. *"Rethinking How to Remember: Beyond Atomic Facts in Lifelong LLM Agent Memory."* arXiv:2605.19952 (May 2026).

**Core idea.** Three coexisting representation granularities for the
same source material:
- **Raw dialogue segments** (anchored by source identifiers, for
  fidelity)
- **Extracted atomic facts** (for efficient retrieval)
- **Synthesized profiles** (for deep reasoning over scattered facts)

Plus TextGrad-based prompt optimization that iteratively refines the
extraction/profiling prompts via downstream response quality —
"lifelong evolution without any parameter updating."

**Why it matters here.** Two ideas worth separating:

1. **Multi-granularity memory.** Today our agent_memory.md is one
   file per agent. For richer workloads, separate stores for "pinned
   rubric" / "recent observations" / "distilled patterns" would let
   each evolve independently. The auto-load mechanism (G6) could be
   extended to load multiple files into a structured prompt section.
2. **Prompt optimization via downstream feedback.** TextGrad-style
   self-tuning of the rubric is striking — the rubric improves itself
   based on whether downstream answers were good. For our case: the
   PR-triage agent's `agent_memory.md` could auto-tune from "did the
   reviewer agree with the flag?" feedback. This is a long-horizon
   feature but very aligned with the platform's "learning from
   execution" pillar in `docs/VISION.md`.

**Where it would land.** Multi-granularity: a `MemoryManager` extension
in Phase B-ish timeframe. Prompt optimization: speculative; document
as a future direction.

---

### 8. PEEK — Context map as an orientation cache

> Gu, Zhang, Khattab et al. *"PEEK: Context Map as an Orientation Cache for Long-Context LLM Agents."* arXiv:2605.19932 (May 2026).

**Core idea.** For workloads that repeatedly operate over the same
external context (a doc corpus, a code repo), maintain a
constant-sized "context map" in the prompt that gives the agent
persistent orientation. Three modules: a Distiller extracts knowledge
from inference-time signals, a Cartographer translates it into
structured edits, a priority-based Evictor enforces a fixed token
budget. 6.3–34% improvement at 1.7–5.8× lower cost; works on Codex.

**Why it matters here. This is the most directly applicable paper.**
Our agent_memory.md is exactly a context map — a small,
operator-curated artifact in the prompt that gives the agent
orientation about the workload. The differences:

- **Operator-curated vs. learned.** Our rubric is hand-edited; PEEK's
  is maintained by a programmable cache policy that watches the agent
  in action.
- **Static vs. evolving.** Our rubric is overwrite-on-load (G6);
  PEEK's evolves under a token budget.

The natural evolution of our G6 work is toward something PEEK-shaped:
the agent observes which concerns get flagged, which categories the
reviewer corrects, and the cache policy edits the rubric over time.

**Where it would land.** Add a new gap in `docs/NEXT_STEPS.md`:
"learned rubric updates from accumulated feedback." Out of scope until
we have downstream feedback (a reviewer marking concerns as
correct/incorrect, or an LLM-as-judge eval over time). PEEK's
three-module Distiller / Cartographer / Evictor split is a useful
architectural sketch when we get there.

---

### 9. MemAudit — Auditing poisoned agent memory

> Tan, Yao, Jin et al. *"MemAudit: Post-hoc Auditing of Poisoned Agent Memory via Causal Attribution and Structural Anomaly Detection."* arXiv:2605.23723 (May 2026).

**Core idea.** Memory-augmented agents are vulnerable to memory
injection: an adversary writes malicious records through normal
interaction, which later get retrieved and steer the agent. Existing
defenses focus on online filtering. MemAudit is *post-hoc*: given
observed harmful behavior, identify which stored memories were
responsible. Uses two signals — counterfactual memory influence
(causal contribution to harmful outputs) + memory consistency graph
(structurally anomalous entries). Reduces attack success from 70% →
0% on QA, 83% → 0% on reasoning agents.

**Why it matters here.** Not a concern today — our memory is
operator-written only, and the platform is single-tenant. But the
concern becomes real if/when:

- We add per-user accumulated memory (multi-tenant case).
- We add tool-use that lets the agent *write* to its own memory
  (autonomous reflection).
- We integrate user-content that the agent stores ("remember that I
  prefer X").

Memory injection is a class of vulnerability that doesn't exist in
the static-rubric model but would appear immediately when memory
becomes writable from anything but a trusted operator. Worth flagging
in `docs/ARCHITECTURE.md`'s security section so it's not surprising
later.

**Where it would land.** A footnote in `docs/ARCHITECTURE.md`'s
security discussion. Not actionable code-wise until memory becomes
externally writable.

---

### 10. PARPO + PSGM — Personalized agentic RL

> Zhang, Li, Huang et al. *"From Correctness to Preference: A Framework for Personalized Agentic Reinforcement Learning."* arXiv:2605.23382 (May 2026).

**Core idea.** Same query, different users, different right answers.
Generic rewards can't capture heterogeneous preferences. PARPO
(Personalized Anchor Reward-Decoupled Policy Optimization) separates
generic task rewards from personalized preference rewards, using
user-specific anchors. PSGM (Preference-Aligned Skill Evolution Graph
Memory) provides personalized retrieval over a skill graph.

**Why it matters here.** Our platform is single-operator today. RBAC
exists (Admin / Designer / Operator / Viewer / Auditor) but every
authenticated user sees the same agent behavior. The day this
platform serves multiple end users with different preferences (e.g.,
two teams using the same PR triage workflow but with different
sensitivities — "we care about test coverage", "we care about
performance regressions"), the framing here applies: don't fork the
workflow per-team, fork the rubric/memory per-team.

**Where it would land.** Out of scope until we have a multi-team
customer. When that lands, the architectural choice is
"per-user-or-team agent memory directories" — likely
`<base>/<team>/<workflow>/<step>.md` keyed by an `actor_id`. Add to
`docs/INTEGRATIONS.md`'s tenancy section as a future design note.

---

## Cross-cutting themes

Three patterns showed up in more than one paper:

**Theme A — memory has two-speed dynamics.** Both Auto-Dreamer (#6)
and TriMem (#7) separate fast acquisition from slow consolidation;
PEEK (#8) implements a similar cache-policy split. This argues for
keeping our `MemoryManager.append()` (fast) and
`MemoryManager.write_raw()` (replace) APIs distinct even when both
become commonly used — the underlying storage is the same Markdown
file, but the operations have different ergonomics.

**Theme B — memory is part of the environment, not just an input.**
Memory-R2 (#3) makes this explicit (memory turns the agent's past
into its future environment). Our `memory_hash` audit-log field
captures this — it's the right primitive. Other systems that don't
have this primitive will be unable to debug behavior drift the way
ours can.

**Theme C — retrieval is wasteful when generation works.** Mem-π
(#4), PEEK (#8), and DeferMem (#1) all argue against
similarity-based-retrieval-only architectures. Mem-π generates,
PEEK caches, DeferMem distills. None of them are "vector DB and call
it done." When we get to Phase B, we should plan to evaluate at
least one non-retrieval-only approach against the
`docs/RAG_PRODUCTION_NOTES.md` default of vector + BM25 + rerank.

## Concrete recommendations

Three actionable items that come out of this reading:

**R1 — Fork-from-step affordance** (from paper #2). Add a
"fork this instance at step X" button on the dashboard. Backend
already supports it via `engine.resume`. Small platform addition that
delivers a real superpower for rubric/prompt iteration: try the same
input from step N with a tweaked rubric, without redoing steps 1..N-1.
File as G8 in `docs/NEXT_STEPS.md`.

**R2 — Capture memory-isolated eval as a methodology note** (from
paper #5). Add a one-paragraph note to
`docs/LEARNING_IMPLEMENTATION.md` Phase A: "to evaluate whether
memory is helping, run the same workflow twice (with and without
memory loaded), score the delta." We can do this today by passing
`memory=None` to the engine for the control run. Cheap to wire up
when there's a memory-using workflow whose effectiveness we want to
quantify.

**R3 — Cite this corpus where it informs an existing decision.** Two
specific spots:
- `docs/ARCHITECTURE.md` D5/D6: cite ActiveGraph (#2) as independent
  validation of the event-log-as-source-of-truth design.
- `docs/RAG_PRODUCTION_NOTES.md`: add a section "alternatives to
  pure retrieval" referencing DeferMem (#1), Mem-π (#4), and PEEK
  (#8) as approaches to evaluate alongside the default hybrid + rerank
  stack in Phase B.

**Out of scope deliberately:** prompt-tuning the rubric automatically
(#7's TextGrad), per-user agent memory (#10), poisoned-memory auditing
(#9). Each one becomes interesting when a specific platform direction
pulls for it — they're captured here so we don't have to re-discover
them.

## How this doc goes stale

- A new paper supersedes one of the entries above → add it; keep the
  prior entry only if its idea is distinct.
- A "would land here" recommendation actually lands → strike through
  the recommendation, add the commit / PR ref.
- Phase B starts → consolidate the retrieval-related entries (#1, #4,
  #8) into `docs/RAG_PRODUCTION_NOTES.md` and remove the duplication.
- We do a second reading pass on a different corpus → fork this doc,
  don't merge — these are scoped to "agent memory" specifically.

## Methodology

Papers were selected via the validation workflow at
`examples/research_paper_triage/`, fired against
`data/arxiv_batch_50.json` (50 May-2026 arXiv papers from
`cs.AI` / `cs.CL` / `cs.LG` matching agent + memory/context/retrieval
keywords). The v3 rubric scored each on a 0–5 relevance scale; the
top 10 by score (all in the `directly_relevant` bucket) are read
here. Reproducible by re-running the workflow and joining
`step_executions.output` on a fresh fetch — see the example README.

Abstracts only (no full-PDF reads). For most papers, the abstract
carries the core idea; if a recommendation here gets prioritized,
the corresponding paper is worth reading in full before building.
