# Positioning note — agents vs. rules engines, and where this platform actually stands

Status: **proposal / framing input** (2026-07-18), same posture as the rest of
`docs/product/` — not adopted policy. Source: an external passage on agent
theory Quentin shared, an initial assessment of it against this application,
and an engineering-session sharpening of that assessment. Kept because the
framing is close to landing-page-grade and the corrections are the part
nobody else's copy will have.

## The external framing (paraphrased)

> Rules engines ask "what rule should fire next, given this input?" Agents
> ask "what action should I take to get closer to my goal?" An agent knows
> its goal, its context, and its available actions, and reasons over them —
> deciding what to do rather than executing canned responses. Agentic
> systems are iterative, not reactive: they adapt when the world changes,
> coordinate with other agents, and manage their own goals without a human
> rewriting rules.

The one-sentence version — *rule that fires next* vs. *action that gets me
closer to the goal* — is the clearest articulation of the distinction we've
seen, and the goal/context/actions triple maps exactly onto this platform's
agentic step: a natural-language `goal`, context accumulated from prior
steps + injected memory, and a capability-allowlisted tool set.

## Three corrections that make it true (and ours)

**1. "The system manages its goals" is the part we deliberately don't do.**
Our agents pursue fixed, human-written goals per step; the DAG decides what
runs next, deterministically; the LLM-driven orchestrator that would manage
goals has stayed deferred through three re-evaluation checkpoints. That is a
trust decision, not a maturity gap: you can audit *"did the agent achieve
the goal a human wrote,"* but "did the agent choose good goals" is a
different and much harder audit. The trust wedge (dry-run, per-tool-call
forensics, capability boundaries, live cost meters) works *because* goals
are pinned. Positioning implication: we sell goal-directed reasoning
**inside human-pinned goals** — the passage's endpoint is the thing our
architecture argues against, at least until production signal makes
orchestrator-level reasoning worth its audit cost.

**2. Agents and rules are better at different things — and the economics
decide.** "Move file from A to B" needs a function call, not goal-directed
reasoning. The hybrid deterministic/agentic engine exists because agents are
expensive, slow, and unpredictable exactly where a problem is well-defined;
our third production workload (DMARC ingest) is fully deterministic — zero
tokens — because the judgment content is zero. Honesty note for any copy:
the "system figures out which is which over time" codification loop is a
`LEARNING.md` concept, **not built** — today a human does that codification.
Pure-agent competitors are right about architecture and wrong about
economics; we shouldn't make the mirrored overclaim.

**3. "Adapts without a human rewriting rules" is true only with a feedback
substrate — and we've measured the gap.** Agents adapt *within* an
execution (retry, different tool). Across executions they repeat the same
mistakes with equal confidence unless something connects outcomes to future
context. That something is the memory layer, and the platform's own
validation quantifies its worth:

- Pre-recall, the triage agent misjudged the same senders identically on
  every encounter; the Google-notice misfire was fixed by a rubric edit +
  per-sender recall, not by the agent "getting smarter."
- **Cheap Haiku + rubric + per-sender recall: 99.4% agreement with human
  labels (153/154). A blind Sonnet judge — a strictly more capable model
  with no memory substrate: 90.3%.** Context-plus-feedback-loop beat raw
  model capability by nine points at a fraction of the cost.

That number is the single strongest evidence for the thesis, and it is ours
to cite (methodology in `docs/product/LLM_EVAL_FRAMEWORK.md` + the triage
validation record in `examples/email_triage_live/README.md`).

Refinement worth keeping: even within-execution adaptation is **bounded on
purpose** — budgets, timeouts, retry caps, capability intersection.
Adaptation is a per-layer dial, not a switch, and every notch of freedom
costs predictability that governance surfaces must buy back.

## The synthesis (candidate landing-page shape)

Traditional automation = rules engines. AI-native startups = agents alone
(hallucinate, cost, forget). This platform = **goal-directed agents, inside
human-pinned goals, on a learning substrate, behind governance you can
see**: agents + memory + cost intelligence + capability boundaries. The
empty quadrant in `COMPETITIVE_LANDSCAPE.md` (governance × intelligence) is
this sentence as a market map.

## If adopted into copy

- The 99.4/90.3 claim must link its methodology and stay dated (one
  mailbox, 154 messages, 2026-07); it is evidence, not a benchmark.
- Don't claim the codification loop until it exists (correction 2).
- "Manages its own goals" never appears as a promise; "your goals, its
  judgment" is the honest version of the same energy.
