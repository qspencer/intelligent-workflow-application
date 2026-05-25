# Research Paper Triage — agent memory

Loaded by the engine into the `triage` step's system prompt under
"Prior agent memory" (per the G6 auto-load mechanism). Edit freely;
the `memory_hash` recorded on each run lets you correlate behavior
changes with edits.

## Reader interest profile

The reader is building an agentic workflow platform and cares about
papers that inform memory architecture, context management, and how
LLM agents retrieve / reason over their state.

### Strongly relevant — score 4–5

- **Agent memory architectures.** Persistent / episodic / hierarchical
  memory for LLM agents. Papers like MemGPT, MemoryBank, Generative
  Agents, A-Mem.
- **Context window management.** Long-context handling, context
  compression, sliding-window strategies, "lost in the middle" effects.
- **Retrieval-augmented generation.** RAG architectures, hybrid
  retrieval, reranking, evaluation of retrieval quality, KV-cache
  reuse, query rewriting.
- **Tool use + memory interaction.** How agents combine tool calls
  with stored knowledge (Toolformer-style, ReAct variants, deliberate
  reasoning over retrieved context).
- **Production agent systems** with substantive memory or context
  design (architecture papers from industry).

### Tangentially relevant — score 2–3

- LLM agents in general (planning, multi-step reasoning) without
  memory/context being the focus.
- Evaluation benchmarks where memory is one dimension among many.
- Long-context LLM training (e.g. context-window extension methods) —
  helpful background but not the application.
- General RAG surveys — useful for orientation but light on novel
  methods.

### Methodology only — score 2

Papers introducing techniques that *could apply* to memory/context but
whose primary contribution is elsewhere — e.g. a new fine-tuning method
demonstrated on a memory task. Score by how directly the technique
transfers.

### Out of scope — score 0–1

- Computer vision, robotics, RL-as-game-playing, low-level training
  optimizations, speech, multimodal that isn't text+memory focused.
- Pure theory (e.g. expressivity of transformers) without a memory
  or retrieval connection.
- Position / opinion pieces without empirical or architectural
  contribution.

### Unclear — score null or any

If the abstract is too brief, too jargon-heavy without context, or
the topic genuinely doesn't fit the catalog above, use
`relevance_bucket: "unclear"` and set the score to whatever your best
read is.

## Relevance bucket catalog

Pick exactly one:
- `directly_relevant` — agent memory / context / retrieval is the focus.
- `tangentially_relevant` — agents or LLMs, but not memory/context focus.
- `methodology_only` — useful technique whose primary contribution is
  elsewhere.
- `out_of_scope` — doesn't fit the reader's interests.
- `unclear` — abstract too vague to decide; flag for human eyeball.

## Relevance score (0–5)

- **5** — must read. Directly on topic, novel contribution.
- **4** — should read. Directly relevant, possibly incremental.
- **3** — could read. Tangentially relevant, useful background.
- **2** — skim only. Methodology might transfer; topic isn't quite.
- **1** — skip unless context-specific reason to look.
- **0** — out of scope.

## Tag catalog

Pick from this catalog; don't invent variants. 0–3 tags per paper is
typical. Empty list is a valid answer.

- `survey` — survey / review paper.
- `position` — position / perspective / opinion piece.
- `benchmark` — introduces or focuses on an evaluation benchmark.
- `case_study` — **only** use when the paper reports observations from
  a real-world deployment with actual end users or production traffic.
  Concrete tests for `case_study`:
  - Does the paper describe a *system actually used by people other
    than the authors* and report what happened?
  - Are there *quotes, user studies, or production metrics* from a
    real deployment?
  - Is the paper's *main contribution* "here is what we learned from
    deploying X," not "here is the method we built"?

  If you can't say yes to all three, it is NOT a case_study — it's
  `empirical`. A paper that introduces a method and evaluates it on
  a benchmark (even a "real-world" benchmark, even one drawn from a
  real domain like e-commerce or research-paper generation) is
  `empirical`, not `case_study`. Multi-agent system papers, framework
  papers, and "we built X and ran experiments" papers are
  `empirical`. Reserve `case_study` for the rare paper whose
  contribution IS the deployment observations themselves.
- `theoretical` — primarily formal / theoretical analysis.
- `empirical` — primarily empirical study with new results. This is
  the default tag for most research papers introducing a method +
  evaluation. Do not also tag `case_study` unless the paper genuinely
  describes a deployment.
- `tutorial` — tutorial or pedagogical resource.

Tags are not mutually exclusive *in principle*, but in practice most
papers warrant 0–2 tags. `empirical` and `case_study` together is
usually wrong (pick one).

Do NOT use:
- Free-form tags like `interesting` or `well-written`.
- Tags for topics — those go in `key_concepts`.

## key_concepts

Up to 5 short noun phrases naming the specific techniques, systems,
or ideas the paper centers on. Examples: `"MemGPT"`, `"long-term memory"`,
`"hybrid retrieval"`, `"in-context learning"`, `"hierarchical memory"`,
`"tool-augmented reasoning"`, `"KV-cache compression"`. These are the
discovery hooks for later search; pick the ones a future reader would
grep for.

## Output discipline

Respond with **only** a JSON object on one line — no prose, no fences:

```
{"relevance_score": <0-5>, "relevance_bucket": "<one of the five>",
 "summary": "<one short sentence; don't restate the title>",
 "key_concepts": [<list of short noun phrases>],
 "tags": [<catalog tags; [] if none>]}
```

`summary` should describe what the paper does in a way that helps the
reader decide whether to read it — its angle, not its title.

If the abstract is missing or unparseable, return
`relevance_score: 0`, `relevance_bucket: "unclear"`,
`summary: "abstract unavailable or unparseable"`, empty lists.

## What this rubric is NOT for

- Quality / novelty assessment beyond the bucket logic. We're sorting,
  not peer-reviewing.
- Predicting citation impact or popularity.
- Replicating reviewer-style critique. The output is for one reader's
  reading queue, not a venue's accept/reject.
