# LLM Evaluation Framework for Workflow Scaffolding

## Problem

The Intelligent Workflow Platform uses LLMs to translate natural language descriptions into executable workflow definitions. This is the core UX differentiator — users describe what they want, the system builds it. But LLM calls cost money, and the default scaffold model directly impacts:

1. **Product economics** — every workflow creation costs tokens. At scale, this is significant.
2. **User perception** — if the model produces a bad workflow, the user blames the product, not the model.
3. **Model lock-in** — without an objective benchmark, we can't confidently switch models when cheaper options appear.

## Solution

An evaluation suite that scores LLMs on their ability to produce correct, well-structured workflow definitions from natural language descriptions. This lets us:

- **Default to the cheapest model that passes** — reduce cost without sacrificing quality
- **Detect regressions** — know immediately when a model update changes scaffold quality
- **Switch models confidently** — when a new model drops at half the price, run the eval and know in minutes whether it's viable
- **Prove quality claims** — "we tested 12 models on 50 real workflow descriptions" is publishable content

## How It Works

### The Test Suite

50 natural language workflow descriptions spanning:
- Simple unambiguous requests (file moves, scheduled tasks)
- Medium complexity requiring inference (multi-step with branching, tool selection)
- Complex/ambiguous requests requiring judgment (underspecified, needs clarification)
- Edge cases and traps (destructive actions, bulk operations, security concerns)

Each test case defines the input (what the user says) and expected outputs (trigger type, step structure, tools used, behavioral expectations).

### The Scoring Model

Three automated layers plus one LLM-judged layer:

| Layer | What it checks | How it's scored | Cost |
|-------|---------------|-----------------|------|
| **L1: Structural validity** | Does the output parse as a valid WorkflowDefinition? Edges valid? References resolve? | Pass/fail (automated) | Free |
| **L2: Structural correctness** | Correct trigger type? Reasonable step count? Right step types (deterministic vs agentic)? Required tools present? | Per-criterion pass/fail (automated) | Free |
| **L3: Intent capture** | Does the workflow accomplish what the user described? Are the goals well-specified? | 1-5 scale (LLM-as-judge using a stronger model) | ~$0.01/case |
| **L4: Quality/nuance** | Handles ambiguity well? Picks efficient step types? Avoids unnecessary complexity? | 1-5 scale (LLM-as-judge) | ~$0.01/case |

### Pass Criteria

A model is **suitable as the default scaffold model** if:
- **Zero** L1 hard fails (every output must parse)
- **≥90%** L2 structural correctness (trigger + step types + tools)
- **≥3.5** average L3 intent capture score
- **≥3.0** average L4 quality score

A model is **excellent** if all L2 criteria are ≥95% and L3/L4 averages are ≥4.0.

### Running the Eval

```
For each model under test:
  For each test case:
    1. Feed the user description + the tool/function catalog to the model
    2. Parse the output as a WorkflowDefinition (L1)
    3. Check structural criteria against expected values (L2)
    4. Feed the output + original description to the judge model (L3, L4)
    5. Record scores
  
  Compute aggregates:
    - L1 pass rate
    - L2 per-criterion scores + overall
    - L3 mean + distribution
    - L4 mean + distribution
    - Total cost of the eval run per model
```

### Output

A model leaderboard:

```
Model                    L1    L2     L3    L4    Cost/scaffold  Verdict
─────────────────────────────────────────────────────────────────────────
Claude Haiku 4.5         100%  94%    4.2   3.8   $0.003         ✅ Pass
Claude Sonnet 4.6        100%  98%    4.7   4.5   $0.015         ✅ Excellent
Llama 3.3 70B            96%   82%    3.4   3.1   $0.001         ❌ Fail (L1)
Mistral Large            100%  90%    3.8   3.5   $0.008         ✅ Pass
GPT-4o Mini              100%  88%    3.6   3.3   $0.002         ⚠️ Borderline
```

### Operational Cadence

- **On new model release:** run the full suite, update the leaderboard
- **Monthly:** re-run the current default model to detect drift
- **On eval suite expansion:** re-run all models on the new cases
- **In CI:** optionally gate PRs that change the scaffold prompt/catalog on a passing eval

## First Calibration Measurement (2026-07-19)

The judge-calibration amendment (anchor L3/L4 against a human-labeled
subset before trusting judge scores) has its first data point, from the
email-triage workload: over 154 human-labeled real messages, a blind
Sonnet judge agreed with the human 90.3% of the time — while the
production Haiku agent (iterated rubric + per-entity recall) agreed
99.4%, and the judge missed the one genuine agent error. Two standing
conclusions: (1) judge scores on classification task types carry ~10%
noise — screening signal, never truth or an auto-apply gate; (2) domain
assets beat raw model capability — the cheapest-passing-model strategy
can outperform, not just cost less. Recalibrate per task type as
corpora are labeled.

## Strategic Value

### Internal
- Eliminate guesswork about model selection
- Reduce scaffold cost by 5-10x (Sonnet → Haiku) when the eval proves it's safe
- Regression safety net for model updates

### External (marketing)
- Publishable benchmark: "Which LLMs can actually build workflows from natural language?"
- Demonstrates rigor to enterprise buyers ("we test this systematically")
- Content asset: blog post series on model quality across providers

### Product feature (future)
- Automatic model selection: the platform picks the cheapest passing model per-user-description-complexity
- User-visible quality guarantee: "your workflows are built by a model that scores 94% on our validation suite"

## Scope Extension (Future)

The same eval framework applies to:
- **Per-step execution models** — "which model can triage emails / classify documents / route decisions?" (bigger cost impact since these run on every execution, not just creation)
- **Conversational interface** — "which model handles status queries / troubleshooting / workflow modification?"
- **Cost analyst recommendations** — "which model produces actionable optimization suggestions?"

Start with the scaffold eval. Extend the framework to execution models once the pattern is proven.

## Connection to the Cost Analyst Module

The eval suite is not just a quality gate — it's the **foundation of intelligent cost management** (see `PRODUCT_SPECIFICATION.md` differentiator D4 and `GAP_ANALYSIS.md` Priority 2.5).

```
┌─────────────────────────────────────────────────────────────┐
│                    EVAL SUITE                                │
│  50+ test cases scored across multiple models               │
│  Result: Model Capability Matrix                            │
│  (model × task_type → pass/fail + score + $/1M tokens)      │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                 COST ANALYST MODULE                          │
│  Correlates the capability matrix with execution history:   │
│                                                             │
│  "Step X uses Sonnet ($3/M) for task_type=classification.   │
│   Haiku passes the eval at 94% for this task type ($1/M).   │
│   At current volume (200 runs/month), switching saves $X."  │
│                                                             │
│  Recommendations surface in the dashboard.                  │
│  Human approves → model config updated.                     │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              GRACEFUL DEGRADATION                            │
│  During budget pressure, the engine auto-selects the        │
│  cheapest passing model per step from the matrix.           │
│  No human approval needed — eval already proved quality.    │
└─────────────────────────────────────────────────────────────┘
```

This makes the eval suite a **revenue-generating asset**: it reduces LLM costs for every customer, and the cost reduction is provable and attributable. "We saved you $X this month by using eval-validated cheaper models" is a retention metric.

### Task Type Taxonomy

The cost analyst needs to classify each step into a `task_type` to look up the right eval scores. Initial taxonomy:

| Task type | Description | Example steps |
|-----------|-------------|---------------|
| `scaffold` | NL → workflow definition translation | The initial workflow creation call |
| `classification` | Categorize input into predefined buckets | Email triage, document routing, PR categorization |
| `extraction` | Pull structured data from unstructured input | Invoice field extraction, action item extraction |
| `summarization` | Condense long content into a short summary | Report summarization, meeting notes |
| `generation` | Produce new content (drafts, responses) | Email reply drafting, code review comments |
| `routing` | Decide where something should go | Approval routing, escalation decisions |
| `analysis` | Evaluate, compare, or assess | Contract review, sentiment analysis, anomaly diagnosis |

Each task type gets its own eval subset. The cost analyst looks up: "for task_type=classification, which models pass at ≥90% L2 and ≥3.5 L3?" and recommends the cheapest one.
