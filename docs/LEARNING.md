# Intelligent Workflow Platform — Adaptive Learning System

## Principle

The system should require less human input over time, not more. Every interaction, every workflow execution, and every environmental change is a learning opportunity. The platform continuously builds a model of: what users want, how their environment works, and how to execute workflows better.

---

## Three Learning Dimensions

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  │
│   │   LEARN FROM  │  │   LEARN FROM  │  │   LEARN FROM  │  │
│   │    USERS      │  │  ENVIRONMENT  │  │   EXECUTION   │  │
│   │               │  │               │  │               │  │
│   │ Preferences   │  │ System state  │  │ Success/fail  │  │
│   │ Goals         │  │ Data patterns │  │ Timing        │  │
│   │ Corrections   │  │ Availability  │  │ Cost          │  │
│   │ Habits        │  │ Relationships │  │ Error patterns│  │
│   │ Feedback      │  │ Changes       │  │ Optimizations │  │
│   └───────────────┘  └───────────────┘  └───────────────┘  │
│                                                             │
│                    ┌───────────────┐                         │
│                    │  UNIFIED      │                         │
│                    │  KNOWLEDGE    │                         │
│                    │  BASE         │                         │
│                    └───────┬───────┘                         │
│                            │                                │
│              ┌─────────────┼─────────────┐                  │
│              ▼             ▼             ▼                  │
│     Better defaults  Proactive    Fewer errors             │
│     Less asking      suggestions  Lower cost               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Dimension 1: Learning from Users

### What the system observes

| Signal | What it reveals |
|--------|----------------|
| How users phrase requests | Vocabulary, mental models, level of technical comfort |
| What users correct | Where the system's assumptions were wrong |
| What users approve without changes | Where the system's defaults are right |
| What users dismiss or ignore | What's not useful to them |
| When users intervene in workflows | Where automation isn't trusted yet |
| What users ask about | Gaps in transparency or documentation |
| Timing patterns | When users are active, when they want to be left alone |
| Repeated manual actions | Automation opportunities the system should suggest |

### What the system learns

**Preferences:**
- "This user always wants invoice summaries posted to #finance, not #general"
- "This user prefers detailed explanations; that user wants one-line summaries"
- "This team reviews everything over $5K; that team's threshold is $20K"

**Communication style:**
- "This user says 'ship it' to mean approve. That user says 'LGTM'."
- "This user wants to be notified on Slack. That user prefers email."
- "This user works 6am–2pm. Don't send non-urgent alerts after 2pm."

**Goals and intent:**
- "When this user says 'handle the invoices,' they mean: extract, validate against PO, file to SharePoint, notify accounting"
- "This team's goal is zero manual document processing by Q3"
- "This user is experimenting — don't auto-apply their workflows to production yet"

**Trust calibration:**
- "This user has approved 50 consecutive agent decisions — increase autonomy"
- "This user overrides agent decisions frequently — ask more, decide less"

### How it's stored

Per-user and per-team preference profiles, updated continuously:

```
/data/learning/users/
  sarah.md        — preferences, corrections, communication style
  finance-team.md — shared team preferences, thresholds, goals
```

Structured as observations with timestamps so the system can weight recent behavior over old behavior.

### How it's applied

- Orchestrator and workflow agents consult user profiles when making decisions
- Defaults adapt: new workflows for Sarah auto-configure based on her history
- Communication adapts: notification verbosity, channel, timing all personalized
- Suggestions adapt: only suggest things aligned with stated/observed goals

---

## Dimension 2: Learning from the Environment

### What the system observes

| Signal | What it reveals |
|--------|----------------|
| Connected system schemas | What data exists, what fields are available, what types are used |
| Data patterns | "Invoices from Vendor X always have a PO number in field 3" |
| System availability | "SharePoint is slow on Monday mornings" / "This API has a 100 req/min limit" |
| Data relationships | "This vendor ID in the invoice maps to this record in Salesforce" |
| Volume patterns | "50 invoices arrive between 9–10am, then 5/hour the rest of the day" |
| Schema changes | "Salesforce added a new required field last week" |
| Seasonal patterns | "Invoice volume doubles in December (fiscal year end)" |
| Failure correlations | "When the VPN is slow, SharePoint connector times out" |

### What the system learns

**System topology:**
- Map of all connected systems, their capabilities, their constraints
- Relationships between systems ("vendor ID in invoices = account ID in Salesforce")
- Data flow patterns ("invoices arrive in SharePoint → get processed → results go to Slack + database")

**Operational patterns:**
- Peak hours per system (schedule heavy work during off-peak)
- Rate limits and throttling thresholds (stay under them proactively)
- Reliability patterns ("this API fails ~2% of the time; always retry once")
- Latency expectations ("SharePoint file upload takes 2–5 seconds; if it takes 30, something's wrong")

**Data understanding:**
- Document type signatures ("PDFs from Acme Corp always have the invoice number on page 1, top right")
- Field extraction patterns ("dates from this vendor are DD/MM/YYYY, not MM/DD/YYYY")
- Anomaly baselines ("normal invoice amount from this vendor is $1K–$10K; $100K is suspicious")

### How it's stored

```
/data/learning/environment/
  systems/
    sharepoint-finance.md    — capabilities, patterns, reliability history
    salesforce-crm.md        — schema, relationships, rate limits
  data-patterns/
    invoices.md              — vendor-specific extraction patterns, anomaly baselines
    contracts.md             — structure patterns, key field locations
  topology.md               — system relationships, data flow map
```

### How it's applied

- Agents use environmental knowledge to make better decisions without asking
- Scheduling: heavy processing during off-peak hours
- Proactive error avoidance: "SharePoint is responding slowly — queue non-urgent uploads"
- Better extraction: "I know this vendor's date format — no need to guess"
- Anomaly detection: "this invoice is 10x the normal amount from this vendor — flag it"
- Schema change adaptation: "Salesforce added a field — update the workflow to include it"

---

## Dimension 3: Learning from Execution

### What the system observes

| Signal | What it reveals |
|--------|----------------|
| Step success/failure rates | Which steps are reliable, which are fragile |
| Execution time per step | Where bottlenecks are, what's getting slower |
| Token usage per step | Which agents are expensive, which are efficient |
| Model performance per task | Which model handles which task best |
| Retry patterns | What fails transiently vs. persistently |
| Error types and causes | Root cause patterns across workflows |
| Agent reasoning quality | When agents make good vs. bad decisions |
| Workflow outcomes | Did the workflow achieve its goal? Did the user have to intervene? |

### What the system learns

**Efficiency optimizations:**
- "Step X always succeeds on the first try — reduce its retry budget to zero"
- "Haiku handles this classification task as well as Sonnet — use Haiku (90% cheaper)"
- "These two sequential steps could run in parallel — they don't depend on each other"
- "This step takes 500ms deterministically — don't waste an agent on it, make it a function"
- "Caching the vendor lookup saves 3 seconds and $0.002 per workflow"

**Error prevention:**
- "When the input PDF is over 50 pages, step 3 times out — increase its timeout or split the document first"
- "Invoices from Vendor X fail extraction 30% of the time because of their unusual format — use a specialized prompt"
- "When Bedrock returns a 429, waiting 5 seconds and retrying always works — don't escalate immediately"
- "Monday morning failures correlate with SharePoint maintenance window — delay processing until 10am"

**Quality improvements:**
- "The routing agent makes better decisions when given the vendor's payment history — add that to its context"
- "Extraction accuracy improved from 85% to 97% after we added few-shot examples to the prompt"
- "Users override the agent's decision 20% of the time for invoices in the $4K–$6K range — the threshold should be $6K, not $5K"

**Workflow evolution:**
- "This workflow has been running for 3 months. Based on outcomes, here's what I'd change: [specific suggestions]"
- "A new pattern has emerged: 15% of invoices now include a 'sustainability surcharge' field that we're not extracting — should I add it?"
- "This workflow is now handling 3x the volume it was designed for — here's a plan to optimize it for scale"

### How it's stored

```
/data/learning/execution/
  workflows/
    invoice-processing.md   — performance history, optimization log, error patterns
    contract-review.md      — same
  models/
    model-performance.md    — which model works best for which task type
  optimizations/
    applied.md              — optimizations that were applied and their results
    proposed.md             — optimizations the system wants to suggest
```

### How it's applied

- Orchestrator periodically reviews execution data and proposes optimizations
- Optimizations are applied automatically if low-risk (switch to cheaper model, adjust timeout) or proposed to user if higher-risk (restructure workflow, change logic)
- Error prevention is proactive: "I've seen this pattern before — taking preemptive action"
- Model selection becomes data-driven: "for this task type, Haiku succeeds 99% of the time at 1/10th the cost"

---

## The Learning Loop

```
Execute workflow
       │
       ▼
Observe outcomes ──────────────────────┐
       │                               │
       ▼                               ▼
Update knowledge base          Compare to predictions
       │                               │
       ▼                               │
Adjust behavior ◄──────────────────────┘
       │
       ▼
Execute next workflow (better)
```

Every workflow execution makes the next one:
- **Faster** (skip unnecessary steps, use cached results, parallelize)
- **Cheaper** (use cheaper models where possible, avoid retries)
- **More accurate** (better prompts, better context, better routing)
- **More autonomous** (fewer escalations, fewer questions, more confident decisions)

---

## Safeguards

Learning must not introduce instability or drift:

| Risk | Safeguard |
|------|-----------|
| Learning wrong lessons from bad data | Minimum observation count before acting on a pattern (don't generalize from one instance) |
| Optimizing for cost at the expense of quality | Quality metrics tracked alongside cost; optimization only applied if quality is maintained |
| Drifting from user intent | Periodic "alignment check" — system summarizes what it's learned and asks user to confirm |
| Compounding errors | Learning is versioned; if outcomes degrade after an optimization, auto-rollback |
| Privacy/security | User behavior observations are per-tenant, never shared across tenants; anonymized in aggregate analytics |
| Over-personalization | Team-level defaults override individual quirks for shared workflows |
| Stale knowledge | Observations decay over time; recent data weighted more heavily; explicit "forget this" command available |

---

## User-Facing Learning Features

| Feature | Description |
|---------|-------------|
| **"What have you learned?"** | User can ask the system to summarize its knowledge about them, their workflows, their environment |
| **"Forget this"** | User can tell the system to discard a specific learned behavior |
| **"Why did you do that?"** | System explains which learned pattern drove a decision |
| **Optimization proposals** | System periodically suggests improvements: "I could save you $30/month by switching step 3 to a cheaper model — quality would be identical based on 200 past executions" |
| **Learning dashboard** | Visual showing what the system has learned, confidence levels, and impact on performance/cost |
| **Alignment reviews** | Scheduled check-ins: "Here's what I think your goals are and how I've been optimizing toward them — is this right?" |

---

## Memory Storage Format

> **Superseded in part (2026-07-13).** This section's "all learning
> artifacts are structured Markdown" no longer holds universally: the
> *learned per-entity* memory dimension (users/environment) is now a typed
> graph + episodes in SQLite via the adopted `veracium` library — see
> `docs/SEMANTICS.md` → "Adopted (write-only slice): veracium" for the
> decision record and conditions. The rationale below still governs
> **operator-curated rubric memory** (`MemoryManager`, `agent_memory.md`)
> and any artifact meant for direct prompt injection or human editing.
> The split is deliberate: Markdown where humans author and models read
> verbatim; a provenance-typed store where facts accumulate across runs
> and trust levels must be structural, not stylistic.

All learning artifacts (agent memory, user profiles, environment observations) use **structured Markdown** as the storage format. This is intentional:

- LLMs parse Markdown more reliably than JSON or XML for narrative/contextual information
- Headings create natural section boundaries that aid retrieval
- Human-readable and debuggable without tooling
- Compatible with contextual chunking (each heading = a chunk boundary)

**Memory file structure:**

```markdown
# Agent Memory: invoice-processing/extract

## Recent Observations
- [2026-05-01] Vendor ACME-001 switched to a new invoice template. Field positions changed.
- [2026-04-28] OCR accuracy drops below 90% on invoices scanned at <200 DPI.

## Learned Patterns
- Invoices from vendor-group "utilities" always have PO number on page 1, line 3.
- Multi-page invoices from ACME require concatenating line items across pages.

## Error History
- [2026-04-15] Failed on password-protected PDF from new vendor. Resolution: prompt user for password, cache for future.
```

This format allows:
1. Selective retrieval — load only "Recent Observations" if context budget is tight
2. Recency weighting — timestamps enable decay/prioritization
3. Contextual chunking — each `##` section is a natural chunk with clear semantics

---

## Contextual Retrieval Integration

When the learning system stores observations, it follows the **Contextual Retrieval** pattern (see ARCHITECTURE.md, Knowledge Ingestion Pipeline):

1. Each learned observation is a chunk
2. A contextual summary is generated that situates the observation: what agent learned it, under what conditions, and how confident the system is
3. Observations are embedded for semantic search but also indexed by agent ID, workflow type, and recency

This means when an agent starts a new execution, the system can retrieve the most relevant past learnings — not just by text similarity, but by structural relevance (same workflow, same vendor, same error type).

---

## Context Window Management

The learning system is aware of the **lost-in-the-middle** problem: LLMs attend poorly to information in the middle of long contexts. This affects how learned knowledge is injected:

**Injection priority order:**
1. Most recent, most relevant learned patterns (high-attention position: near the beginning)
2. Error prevention rules (critical — placed early)
3. General preferences and historical context (middle — acceptable if deprioritized)
4. The current task data (high-attention position: at the end)

**Budget management:**
- Each agent has a context budget (subset of its token budget) allocated for memory/knowledge
- If learned knowledge exceeds the budget, the system summarizes older entries and keeps recent ones verbatim
- Summarization uses a cheap model (Haiku-class) to compress while preserving actionable detail
- The Mem0 pattern applies: store granular facts, retrieve only what's relevant, reduce token usage by ~90% vs. injecting everything
