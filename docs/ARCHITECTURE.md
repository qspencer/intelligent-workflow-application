# Intelligent Workflow Platform — Architecture & Design

## Vision

A general-purpose agentic workflow platform where users define workflows as graphs of steps, each step either deterministic (run a function) or agentic (give an LLM a goal and tools, let it reason). An orchestrator agent monitors the entire system — all active workflows, resource usage, and agent behavior — and can intervene when things go wrong.

PDF document processing is the primary use case but the architecture is trigger-agnostic: any event source can start a workflow.

## Origin

This evolves from the PDF Action Automator prototype (see `/home/ubuntu/Dev/pdf-tool`). That prototype validated:
- PDF text extraction (native + OCR) works reliably
- AWS Bedrock LLMs can classify and extract data from documents via natural language
- A rule/action pipeline with priority ordering and chaining is useful
- Real-time WebSocket updates to a web UI provide good UX
- The action executors (webhook, file ops, API calls, CLI, GUI automation, browser automation) are reusable as agent tools

---

## Agent Hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR                              │
│  Singleton. Monitors all workflows, system health,          │
│  resource usage. Can intervene, pause, reprioritize,        │
│  spawn new agents. Learns from outcomes.                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────────┐                                       │
│  │  COST ANALYST    │  Singleton. Peer advisor to           │
│  │  AGENT           │  orchestrator. Observes all spend,    │
│  │                  │  identifies trends, recommends        │
│  │                  │  optimizations. Does not act           │
│  │                  │  unilaterally.                         │
│  └──────────────────┘                                       │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ Workflow     │  │ Workflow     │  │ Workflow     │         │
│  │ Agent        │  │ Agent        │  │ Agent        │         │
│  │ Instance 1   │  │ Instance 2   │  │ Instance 3   │         │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘         │
│         │                 │                 │               │
│    ┌────┴────┐      ┌────┴────┐      ┌────┴────┐          │
│    │ Step    │      │ Step    │      │ Step    │          │
│    │ Agents  │      │ Agents  │      │ Agents  │          │
│    └─────────┘      └─────────┘      └─────────┘          │
└─────────────────────────────────────────────────────────────┘
```

### Orchestrator

- Singleton agent running continuously
- Monitors all active workflow instances: progress, errors, token consumption, wall-clock time
- Detects anomalies: stuck workflows, looping agents, excessive token burn, repeated failures
- Can intervene: pause, retry, kill, reprioritize, escalate to human
- Manages concurrency: throttles if too many agents running simultaneously
- Enforces system-wide Bedrock API budget
- Tracks cross-workflow dependencies
- Learns from outcomes: "workflows of type X tend to fail at step Y"
- Provides system-wide status to the UI dashboard
- Receives and evaluates recommendations from the Cost Analyst Agent

### Cost Analyst Agent

- Singleton agent, peer advisor to the orchestrator
- Observes all token usage, model selection, execution timing, and spend across the entire system
- Does NOT act unilaterally — makes recommendations that the orchestrator (or human) approves/applies
- Runs on a cheap model (its own analysis shouldn't be expensive)
- Operates in two modes:
  - **Continuous monitoring**: lightweight, tracks spend in real time, alerts on anomalies
  - **Deep analysis**: periodic (daily/weekly) or on-demand, invokes LLM for complex reasoning

**What it does:**

| Function | Example |
|----------|---------|
| **Trend analysis** | "Spend increased 40% this week — driven by 3 new workflows using Sonnet for classification that Haiku could handle" |
| **Model recommendations** | "Based on 500 executions, step X produces identical results with Haiku vs Sonnet. Switching saves $0.08/execution ($240/month at current volume)" |
| **Waste identification** | "Workflow Y retries step 3 an average of 2.4 times. Fixing the root cause would save $180/month in wasted tokens" |
| **Budget forecasting** | "At current growth rate, you'll hit the monthly budget ceiling in 18 days. Recommend: switch low-priority workflows to batch processing" |
| **What-if modeling** | "If volume doubles next month (as it did last December), projected spend is $X. Here's a plan to stay within budget: [specific model swaps, batching, caching]" |
| **Optimization scoring** | "Here are this week's top 5 cost optimization opportunities, ranked by savings with quality impact assessment" |
| **Anomaly detection** | "Workflow Z suddenly costs 5x more per execution — an agent appears to be looping. Recommending orchestrator intervention." |
| **ROI reporting** | "This month: $450 in AI costs automated work that would have taken 120 human hours ($6,000 at loaded cost). ROI: 13x" |

**Relationship with orchestrator:**
- Cost analyst sends recommendations to orchestrator
- Orchestrator evaluates: does this conflict with reliability/correctness? If not, apply. If yes, weigh tradeoffs.
- For high-impact changes (restructuring a workflow, changing a model that affects quality), orchestrator escalates to human with the cost analyst's recommendation and its own assessment
- Cost analyst can flag urgent issues ("runaway spend") that the orchestrator should act on immediately

### Workflow Agent

- One per active workflow instance
- Manages execution of the workflow graph: decides which step to run next, handles branching
- Adapts to unexpected situations (step fails, content doesn't match expectations)
- Has moderate autonomy — can retry steps, skip optional steps, choose alternate paths
- Reports status to orchestrator
- Scoped to a single workflow definition + instance

### Step Agent

- One per agentic step execution
- Tightly scoped: has a specific goal, a set of available tools, and a token budget
- Executes until goal is met or budget exhausted
- Returns structured results to the workflow agent
- Cannot spawn other agents or affect other workflows

---

## Agent-Generated Code for Deterministic Steps

### Concept

For deterministic operations (data transformation, validation, formatting, routing logic, calculations), agents write Python code that is then executed directly — no LLM call on subsequent runs. The agent is a code author, not a perpetual reasoner.

```
┌──────────────────────────────────────────────────────────┐
│  First execution:                                        │
│                                                          │
│  User: "extract the invoice number from the filename"    │
│         │                                                │
│         ▼                                                │
│  Agent writes Python:                                    │
│    def extract_invoice_number(filename):                 │
│        match = re.search(r'INV-(\d+)', filename)         │
│        return match.group(1) if match else None          │
│         │                                                │
│         ▼                                                │
│  Validate → Test → Store                                 │
├──────────────────────────────────────────────────────────┤
│  Every subsequent execution:                             │
│                                                          │
│  Input → Python function → Output                        │
│                                                          │
│  (No LLM call. Milliseconds. Free.)                      │
└──────────────────────────────────────────────────────────┘
```

### When Agents Generate Code

| Situation | Example |
|-----------|---------|
| User describes a transformation | "Convert the date from DD/MM/YYYY to ISO format" |
| Agent identifies a repeated pattern | "I've done this same extraction 50 times — I'll write code for it" |
| Workflow designer marks a step as deterministic | Step type = `deterministic`, goal described in natural language, agent generates implementation |
| Optimization | Cost analyst recommends: "this agentic step always produces the same logic — codify it" |
| Orchestrator detects a codifiable pattern | "This agent's reasoning is identical every time — generate code" |

### Code Lifecycle

```
Generate → Validate → Test → Deploy → Monitor → Revise
```

1. **Generate**: Agent writes Python function(s) based on the step's goal and context
2. **Validate**: Static analysis — syntax check, restricted import check, type hints verified
3. **Test**: Run against mock world data or recorded inputs/outputs from previous agentic executions
4. **Deploy**: Store as the step's implementation; step type flips from `agentic` to `generated_code`
5. **Monitor**: Track success/failure rate; if failures increase, flag for revision
6. **Revise**: Agent rewrites the code when requirements change, edge cases appear, or errors occur

### Sandboxed Execution

Generated code runs in a restricted environment:

```python
SANDBOX_POLICY = {
    "allowed_imports": [
        "re", "json", "datetime", "decimal", "collections",
        "typing", "dataclasses", "math", "hashlib", "uuid"
    ],
    "denied_imports": [
        "os", "sys", "subprocess", "shutil", "socket",
        "requests", "urllib", "importlib", "__builtins__"
    ],
    "max_execution_time": "5s",
    "max_memory": "128MB",
    "filesystem_access": "none",  # use tools for file ops
    "network_access": "none",     # use tools for API calls
}
```

Key restrictions:
- No filesystem access (file operations go through the tool layer where permissions are enforced)
- No network access (API calls go through connectors where ACLs apply)
- No dangerous imports (subprocess, os, sys)
- Timeout and memory limits
- Code is executed in an isolated process/container

### Code Storage

```
/data/generated_code/
  workflows/
    invoice-processing/
      extract_invoice_number.py    — generated function
      extract_invoice_number.json  — metadata: author agent, timestamp, test results, version
      validate_amount.py
      format_output.py
```

Each generated file includes:
- The function(s)
- Docstring explaining what it does and why
- Type hints for inputs and outputs
- The original natural language goal that produced it
- Version history (previous implementations retained)

### Hybrid Steps

A step can be partially codified:

```json
{
  "id": "process_invoice",
  "type": "hybrid",
  "deterministic_code": "extract_fields.py",
  "agentic_fallback": {
    "goal": "Handle edge cases that the code can't parse",
    "model": "anthropic.claude-3-haiku",
    "trigger": "when code raises UnhandledFormatError"
  }
}
```

The code handles the 95% case. When it encounters something unexpected, it raises a specific exception and an agent takes over for that instance. The agent's solution may then be codified for next time.

### The Optimization Loop

```
Agentic step (expensive, flexible)
       │
       │  After N successful executions with consistent logic
       ▼
Cost analyst recommends codification
       │
       ▼
Agent generates code based on its own past reasoning
       │
       ▼
Code validated against recorded inputs/outputs
       │
       ▼
Step becomes generated_code (cheap, fast, deterministic)
       │
       │  If new edge case appears
       ▼
Falls back to agentic (handles edge case)
       │
       ▼
Agent updates the code to handle the new case
       │
       ▼
Step returns to generated_code (now handles more cases)
```

This means workflows naturally get cheaper and faster over time without manual optimization.

---

## Knowledge System

> **Status (Phase 2 complete):** the knowledge system described in this
> section is **deferred** to Phases B–F of `docs/LEARNING_IMPLEMENTATION.md`.
> Phase A (agent memory: file-backed Markdown, prepended to system prompts)
> ships in Week 5; everything else here — knowledge ingestion, contextual
> retrieval, knowledge manifests, learning loops — remains forward-looking
> design captured here so it doesn't have to be re-derived when a workload
> pulls for it. `docs/RAG_PRODUCTION_NOTES.md` carries the concrete
> defaults gathered from external work for when Phase B starts.

### Concept

Agents operate with two layers of knowledge:

1. **General knowledge** — the LLM's training data (world knowledge, reasoning, language understanding)
2. **Specialized knowledge library** — a curated, searchable collection of domain-specific information that agents consult at runtime

This is similar to RAG but more structured. The knowledge library isn't just "chunks of text retrieved by similarity" — it's organized into distinct knowledge types, each with its own retrieval strategy and update mechanism.

### Knowledge Types

```
┌─────────────────────────────────────────────────────────────┐
│                    KNOWLEDGE LIBRARY                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │ DOMAIN          │  │ ORGANIZATIONAL  │                  │
│  │ KNOWLEDGE       │  │ KNOWLEDGE       │                  │
│  │                 │  │                 │                  │
│  │ Industry rules  │  │ Company policies│                  │
│  │ Regulations     │  │ Org structure   │                  │
│  │ Best practices  │  │ Vendor lists    │                  │
│  │ Standards       │  │ Approval limits │                  │
│  │ Terminology     │  │ Naming convent. │                  │
│  └─────────────────┘  └─────────────────┘                  │
│                                                             │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │ OPERATIONAL     │  │ LEARNED         │                  │
│  │ KNOWLEDGE       │  │ KNOWLEDGE       │                  │
│  │                 │  │                 │                  │
│  │ System configs  │  │ User prefs      │                  │
│  │ Connector docs  │  │ Past decisions  │                  │
│  │ Tool manuals    │  │ Error patterns  │                  │
│  │ API schemas     │  │ Optimizations   │                  │
│  │ Error codes     │  │ Env. patterns   │                  │
│  └─────────────────┘  └─────────────────┘                  │
│                                                             │
│  ┌─────────────────┐                                        │
│  │ REFERENCE       │                                        │
│  │ DATA            │                                        │
│  │                 │                                        │
│  │ Lookup tables   │                                        │
│  │ Mappings        │                                        │
│  │ Templates       │                                        │
│  │ Examples        │                                        │
│  └─────────────────┘                                        │
└─────────────────────────────────────────────────────────────┘
```

### Knowledge Categories

| Category | Contents | How it gets there | How agents use it |
|----------|----------|-------------------|-------------------|
| **Domain** | Industry regulations, standards, terminology, best practices | Admin uploads documents, points to URLs, or describes rules. System ingests and indexes. | Agent retrieves relevant domain rules before making decisions ("what are the compliance requirements for this document type?") |
| **Organizational** | Company policies, org chart, vendor lists, approval hierarchies, naming conventions, SLAs | Admin provides or system discovers from connected systems. Updated as org changes. | Agent checks policies before acting ("what's the approval threshold for this team?"), routes to correct people |
| **Operational** | System configuration, connector documentation, tool capabilities, API schemas, error code meanings | Auto-generated from system state. Updated as connectors/tools are added. | Agent knows what tools can do, what APIs accept, how to interpret errors without asking |
| **Learned** | User preferences, past decisions, error patterns, optimization history, environmental patterns | Accumulated automatically from the learning system (see LEARNING.md) | Agent applies past lessons ("last time this happened, the solution was X") |
| **Reference** | Lookup tables, code-to-name mappings, templates, few-shot examples, prompt patterns | Admin provides or system generates from observed patterns | Agent looks up values ("what's the GL code for office supplies?"), uses templates for consistent output |

### Retrieval Strategy

Not all knowledge is retrieved the same way:

| Strategy | When used | How it works |
|----------|-----------|--------------|
| **Semantic search** | Domain knowledge, organizational policies | Embed the query, find similar chunks (classic RAG) |
| **Exact lookup** | Reference data, mappings, lookup tables | Key-value retrieval, SQL query, or dictionary lookup |
| **Rule matching** | Policies with conditions | Evaluate conditions against current context, return matching rules |
| **Recency-weighted** | Learned knowledge, user preferences | Retrieve recent observations weighted higher than old ones |
| **Contextual** | Operational knowledge | Based on which tools/connectors are active in the current workflow, inject relevant docs |

### How Agents Consult the Library

Agents don't get the entire library in their context. Instead:

1. Before an agent executes, the system determines what knowledge is relevant based on:
   - The workflow type
   - The step's goal
   - The data being processed
   - The tools being used
   - The user/team involved

2. Relevant knowledge is retrieved and injected into the agent's context alongside its system prompt and memory.

3. Agents can also actively query the library mid-execution using a `knowledge_lookup` tool:
   - `knowledge_lookup("what is the approval policy for invoices over $10K?")`
   - `knowledge_lookup("vendor_code", key="ACME-001")`
   - `knowledge_lookup("error_code", key="SHAREPOINT_403")`

### Knowledge Management

**Adding knowledge:**
- Natural language: "Our policy is that any invoice over $5,000 requires VP approval"
- Document upload: upload a PDF of company policies, regulations, or procedures — system ingests and indexes
- URL reference: point to a wiki page or documentation site — system crawls and indexes
- Auto-discovery: system observes connected systems and builds operational knowledge automatically
- Learning: system accumulates knowledge from execution (see LEARNING.md)

**Updating knowledge:**
- "Actually, the threshold changed to $10,000 last month"
- Re-crawl URLs on a schedule
- Learned knowledge updates continuously
- Version history maintained — can see what the system "knew" at any point in time

**Scoping knowledge:**
- Some knowledge is system-wide (operational, reference)
- Some is tenant-specific (organizational, domain)
- Some is team-specific (team policies, team preferences)
- Some is workflow-specific (relevant only to invoice processing, not contract review)
- Agents only see knowledge within their scope

### Knowledge Quality

The system tracks knowledge quality:
- **Freshness**: when was this last verified? Is it potentially stale?
- **Confidence**: was this stated by an admin (high) or inferred from observation (lower)?
- **Usage**: how often is this knowledge retrieved? Is it actually useful?
- **Accuracy**: when agents use this knowledge, do outcomes improve or degrade?

Stale or low-quality knowledge is flagged for review. The system can ask: "I have a policy on file that says X, but recent behavior suggests Y. Which is correct?"

### Relationship to Agent Memory

Agent memory (D5) and the knowledge library are complementary:

| Agent Memory | Knowledge Library |
|-------------|-------------------|
| Per-agent, personal experience | Shared across agents |
| "Last time I did X, Y happened" | "The policy says Z" |
| Informal, narrative | Structured, categorized |
| Grows from execution | Grows from admin input + learning |
| Injected into system prompt | Retrieved on demand or pre-injected by relevance |

Both are consulted. Memory gives agents personal experience. The knowledge library gives them institutional knowledge.

### Knowledge Ingestion Pipeline

When documents enter the knowledge library (uploaded PDFs, crawled URLs, admin-provided text), they pass through a structured ingestion pipeline before becoming retrievable:

```
Source Document
     │
     ▼
Parse & Extract (PDF→text, HTML→markdown, etc.)
     │
     ▼
Chunk (split into semantically meaningful units)
     │
     ▼
Contextualize (generate summary per chunk — Anthropic "Contextual Retrieval" pattern)
     │
     ▼
Embed & Index (vector embeddings + metadata index)
     │
     ▼
Register in Knowledge Manifest (lightweight index of what exists)
```

**Chunking strategy:**

| Content Type | Chunking Approach |
|-------------|-------------------|
| Policy documents | By section/heading — each policy rule is one chunk |
| API documentation | By endpoint or concept — one chunk per operation |
| Long-form text (regulations, manuals) | Sliding window with overlap (~500 tokens, 50-token overlap) |
| Structured data (tables, lists) | Keep rows/items together — never split a table mid-row |
| Conversation/narrative | By topic shift or paragraph boundary |

**Contextual enrichment (per chunk):**

Each chunk is stored with an LLM-generated contextual summary that situates it within the source document. This prevents the "orphan chunk" problem where a retrieved chunk lacks context about what document it came from or what surrounds it.

```json
{
  "chunk_id": "policy-doc-chunk-042",
  "content": "Invoices exceeding $10,000 require VP-level approval...",
  "contextual_summary": "This chunk is from the Finance Team Approval Policy (v3, updated 2026-01). It describes the approval threshold for high-value invoices.",
  "source": { "document": "finance-approval-policy.pdf", "page": 3, "section": "Thresholds" },
  "metadata": { "category": "organizational", "scope": "finance-team", "freshness": "2026-01-15" },
  "embedding": [0.023, -0.041, ...]
}
```

**Knowledge manifest:**

A lightweight index (similar to the `/llms.txt` convention) that agents can consult to understand what knowledge exists without loading it all:

```markdown
# Knowledge Library Manifest

## Domain Knowledge
- finance-regulations.md — Tax and compliance rules for invoice processing
- vendor-standards.md — Approved vendor qualification criteria

## Organizational
- approval-policy.md — Approval thresholds by team and amount
- org-chart.md — Reporting structure and delegation rules

## Reference
- vendor-codes.json — Vendor ID to name mapping (1,247 entries)
- gl-codes.json — General ledger code lookup table
```

Agents receive the manifest in their context and can request specific sections via `knowledge_lookup` rather than having everything pre-injected.

**Context injection ordering (lost-in-the-middle mitigation):**

Research shows LLMs attend most strongly to the beginning and end of their context, with degraded attention to middle sections. When injecting knowledge into agent prompts:

1. **System prompt** (beginning) — agent identity, goals, constraints
2. **Critical knowledge** — the most relevant retrieved chunks (highest similarity + highest importance)
3. **Agent memory** — recent relevant experiences
4. **Supporting knowledge** — additional context, lower-priority chunks
5. **Current task input** (end) — the actual data/request being processed

This ordering ensures the most decision-relevant information occupies the high-attention positions.

---

| Concept | Description |
|---------|-------------|
| **Workflow Definition** | A DAG (directed acyclic graph) of steps with a trigger. Stored as JSON. Defines the template. |
| **Workflow Instance** | A running execution of a workflow definition, triggered by an event. Has state, context, history. |
| **Step** | A node in the workflow graph. Either `deterministic` (runs a function) or `agentic` (LLM with tools). |
| **Trigger** | What starts a workflow instance: file drop, webhook, schedule, API call, manual, or another workflow's output. |
| **Tool** | A capability available to agents: file ops, API calls, browser automation, database queries, CLI, PDF extraction, etc. |
| **Context** | Shared state within a workflow instance. Steps read from and write to context. Accumulates as the workflow progresses. |
| **Policy** | Constraints on agent behavior: token budgets, allowed tools, required approvals, timeout limits. |

---

## Workflow Definition Schema (draft)

```json
{
  "id": "invoice-processing",
  "name": "Invoice Processing",
  "description": "Extract, validate, and route incoming invoices",
  "trigger": {
    "type": "file_watch",
    "config": { "folder": "/incoming/invoices", "pattern": "*.pdf" }
  },
  "steps": [
    {
      "id": "extract",
      "type": "deterministic",
      "function": "pdf_extract",
      "config": { "ocr_fallback": true },
      "outputs": ["text", "page_count", "is_scanned"]
    },
    {
      "id": "analyze",
      "type": "agentic",
      "goal": "Determine document type, extract key fields (vendor, amount, date, PO number), and flag any anomalies",
      "tools": ["llm_analyze", "lookup_vendor_db"],
      "model": "anthropic.claude-3-sonnet",
      "policy": { "max_tokens": 10000, "max_tool_calls": 5 },
      "outputs": ["document_type", "fields", "anomalies"]
    },
    {
      "id": "route",
      "type": "agentic",
      "goal": "Based on the extracted fields and anomalies, decide: approve automatically, send for human review, or reject",
      "tools": ["check_policy_limits", "lookup_approval_history"],
      "model": "anthropic.claude-3-haiku",
      "policy": { "max_tokens": 3000 },
      "outputs": ["decision", "reason"]
    },
    {
      "id": "execute",
      "type": "deterministic",
      "function": "action_dispatcher",
      "config": {
        "on_approve": { "actions": ["file_move", "notify_accounting"] },
        "on_review": { "actions": ["create_approval_request"] },
        "on_reject": { "actions": ["notify_sender", "archive"] }
      },
      "depends_on_context": ["route.decision"]
    }
  ],
  "edges": [
    { "from": "extract", "to": "analyze" },
    { "from": "analyze", "to": "route" },
    { "from": "route", "to": "execute" }
  ],
  "policies": {
    "max_total_tokens": 50000,
    "timeout_minutes": 10,
    "retry_on_failure": 2
  }
}
```

---

## Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Backend | Python, FastAPI, async | Proven in prototype, good async support for concurrent agents |
| Agent framework | Custom thin layer on Bedrock `converse` API with tool use | Full control, no heavy dependencies, matches our needs exactly |
| Workflow engine | Custom async DAG executor | Supports conditional branching, parallel steps, agent-driven decisions |
| LLM | AWS Bedrock (model configurable per agent) | Multi-model: cheap for simple tasks, powerful for complex reasoning |
| Frontend | Angular | Workflow designer, agent monitoring dashboard, live logs |
| Real-time | WebSocket | Proven in prototype for live status updates |
| Persistence | Postgres + pgvector, JSON for definitions | Handles concurrency, multi-tenancy, vector search, audit at scale. In-memory caching at application layer for hot paths (budget counters, active agent state). |
| Triggers | Plugin architecture | PDF watcher first, webhook/schedule/API added later |

---

## What We Take from the Prototype

The prototype is small (~830 LOC of backend Python) and shaped as a rules-engine + single-prompt LLM classifier — not as an agent framework. After per-component review, the verdicts:

| Prototype Component | Verdict | Reason |
|--------------------|---------|--------|
| PDF extraction service (PyMuPDF + pytesseract auto-detect, ~40 lines) | **Port as-is** | Battle-tested logic; wraps cleanly as a `pdf_extract` tool. |
| Action executors (`notification`, `file_operation`, `data_extraction`, `api_call`, `cli_command`, `gui_automation`, `web_automation`) | **Reference, don't lift** | The implementations (httpx webhooks, file ops, Playwright steps, CLI subprocess) are useful as call-shape references. The prototype's `ActionExecutor.execute(config, context)` interface with `$key` template substitution is the wrong shape for LLM tool-use, which needs structured `parameters_schema` and capability-checked execution. Rebuild around the new `Tool` interface using these as references. |
| Bedrock service (`converse` API wrapper, 62 lines) | **Don't port** | A single hardcoded "analyze a PDF" prompt returning JSON. No tool-use loop, no streaming, no token counting, no multi-turn. The agent framework needs all of these; the only reusable insight is "use Bedrock `converse`." |
| Folder watcher (33 lines) | **Don't port — rewrite** | Trivially thin `watchdog` wrapper that hardcodes `.pdf`. The new system needs configurable patterns and a generic trigger-plugin shape. |
| WebSocket real-time updates | **Don't port — rewrite** | `set[WebSocket]` broadcast-to-all with no auth, no per-user scoping, no backpressure. The new system streams agent reasoning per authenticated user — a different shape entirely. |
| FastAPI app structure | **Reference layout only** | Generic enough that the patterns are obvious; nothing project-specific to lift. |
| Angular component patterns | **Reference, don't lift** | The new UI is redesigned around workflow graphs, agent traces, and (eventually) generative components. Borrow scaffolding conventions, not components. |

Net: one file ports wholesale (`pdf_extractor.py`); the rest is fresh code informed by references. The prototype's value at this stage is in what it *validated* (PDF extraction reliability, Bedrock works for classification, WebSocket UX is good), not its code.

---

## Decisions Made

### D1: Orchestrator is an LLM agent

The orchestrator is a full LLM-powered agent, not a deterministic rule engine. It can reason about novel situations, correlate failures across workflows, and make nuanced decisions about intervention. It runs on a powerful model (e.g., Claude Sonnet) since it handles complex system-wide reasoning.

To manage cost, the orchestrator operates in two modes:
- **Passive monitoring**: lightweight polling loop checks metrics (deterministic, free)
- **Active reasoning**: when metrics cross thresholds or anomalies are detected, the LLM is invoked to decide what to do

> **Status (Phase 2 complete):** only the passive-monitoring half is
> implemented (`MonitoringService` with four deterministic checks emitting
> `alert_*` audit entries). The active-reasoning LLM mode is deferred until
> there's enough running-workflow signal to make its decisions worth
> making — see the re-evaluation checkpoint in `CLAUDE.md`.

### D2: Natural language workflow authoring with live GUI

Users create workflows by describing them in natural language. The system (via the orchestrator or a dedicated authoring agent) translates the description into a workflow definition and populates the GUI in real time.

The GUI shows the resulting workflow graph and allows direct manipulation — users can drag nodes, edit step configurations, add/remove steps, and adjust policies. Changes in the GUI update the underlying definition immediately.

JSON/YAML export/import is available for power users and CI/CD integration, but is not the primary authoring experience.

### D3: Configurable cost control with configurable limit actions

Cost control operates at three levels:

| Level | Budget | When hit... (configurable) |
|-------|--------|---------------------------|
| **Step agent** | Max tokens per invocation | Fail the step / ask workflow agent for guidance / extend with approval |
| **Workflow instance** | Max tokens per execution | Pause and notify / fail the instance / escalate to orchestrator |
| **System-wide** | Max spend per hour/day/month | Pause non-critical workflows / pause all / notify admin only / hard stop |

What happens at each limit is configurable per workflow and globally:
- `notify` — alert but continue
- `pause` — stop and wait for human decision
- `degrade` — switch to cheaper models and continue
- `stop` — terminate immediately
- `escalate` — ask orchestrator to decide

### D4: Enterprise security and permissions

The platform must be deployable in IT-managed environments. This requires:
- Integration with existing identity providers (not a custom user database)
- Granular permissions for both humans and agents
- Audit trail for compliance
- Network/data boundary controls

---

## Security, Permissions & IT Integration

### Authentication

| Method | Use Case |
|--------|----------|
| OIDC / OAuth 2.0 | Primary. Connect to corporate IdP (Okta, Azure AD, AWS IAM Identity Center, Keycloak) |
| SAML 2.0 | Enterprise SSO where OIDC isn't available |
| API keys | Service-to-service, CI/CD, webhook triggers |
| mTLS | Agent-to-agent communication (internal) |

The platform does NOT manage passwords. It delegates authentication entirely to the configured IdP.

### Human Permissions (RBAC)

| Role | Can do |
|------|--------|
| **Admin** | Everything. Manage users, configure IdP, set system policies, view all workflows |
| **Workflow Designer** | Create/edit/delete workflow definitions, configure triggers, set step policies |
| **Operator** | Start workflows manually, view status, approve/reject human-in-the-loop steps, retry failed steps |
| **Viewer** | Read-only dashboard access, view workflow history and logs |
| **Auditor** | Read-only access to full audit trail, agent reasoning logs, cost reports |

Roles map to IdP groups. No local role assignment needed if groups are configured in the IdP.

### Agent Permissions (Capability-Based)

Agents are not trusted by default. Each agent has an explicit capability set that limits what it can do:

```json
{
  "agent_id": "workflow-agent-invoice-processing",
  "capabilities": {
    "tools": ["pdf_extract", "llm_analyze", "file_move", "notify_webhook"],
    "file_access": {
      "read": ["/incoming/invoices/*", "/config/vendor-list.json"],
      "write": ["/processed/invoices/*", "/archive/*"]
    },
    "network": {
      "allowed_hosts": ["accounting.internal.corp", "hooks.slack.com"],
      "denied_hosts": ["*"]
    },
    "api_calls": {
      "allowed_methods": ["GET", "POST"],
      "allowed_urls": ["https://erp.internal/api/invoices/*"]
    },
    "can_spawn_agents": false,
    "can_modify_workflow": false,
    "max_tokens_per_call": 10000,
    "max_tool_calls_per_step": 20,
    "requires_approval_for": ["file_delete", "api_call_external"]
  }
}
```

Key principles:
- **Least privilege**: agents get only the tools and access they need
- **No implicit trust**: a step agent cannot access files or APIs not in its capability set
- **Escalation over assumption**: if an agent needs something outside its permissions, it asks (workflow agent → orchestrator → human)
- **Orchestrator has elevated but not unlimited permissions**: even the orchestrator operates within a defined capability boundary (configurable by admin)

### Agent Permission Inheritance

```
System Policy (admin-defined ceiling)
  └── Workflow Definition (designer sets per-workflow limits)
       └── Step Definition (designer sets per-step limits)
            └── Runtime Agent (intersection of all above)
```

An agent can never exceed the most restrictive policy in its chain.

### Audit Trail

Every action is logged:

```json
{
  "timestamp": "2026-05-05T12:00:00Z",
  "actor": { "type": "agent", "id": "step-agent-extract-001", "workflow_instance": "inv-2026-0042" },
  "action": "tool_call",
  "tool": "file_move",
  "params": { "source": "/incoming/inv-042.pdf", "destination": "/processed/inv-042.pdf" },
  "result": "success",
  "policy_check": { "passed": true, "rule": "file_access.write includes /processed/*" }
}
```

Audit logs are:
- Immutable (append-only)
- Queryable (by actor, action, time range, workflow)
- Exportable (for compliance tools, SIEM integration)
- Retained per configurable policy (default: 90 days, configurable to years)

### IT Integration Points

| Integration | How |
|-------------|-----|
| Identity Provider | OIDC/SAML configuration in admin settings |
| SIEM / Log aggregation | Audit log export via syslog, webhook, or S3 |
| Secrets management | AWS Secrets Manager / HashiCorp Vault for API keys, credentials |
| Network policies | Agent network ACLs respect corporate firewall rules |
| Data classification | Workflow definitions can tag data sensitivity; agents respect classification boundaries |
| Backup / DR | Workflow definitions and audit logs exportable; state reconstructable from event log |

---

## Decisions Made (continued)

### D5: Agent Memory

Agents have persistent memory across workflow instances. Memory is scoped by agent level:

| Agent | Memory Scope | Contains |
|-------|-------------|----------|
| Orchestrator | System-wide | Cross-workflow patterns, failure history, optimization learnings |
| Workflow Agent | Per workflow definition | Past instance outcomes, common failure points, vendor-specific quirks |
| Step Agent | Per step definition | Extraction patterns that worked, edge cases encountered |

**Initial implementation**: text files (one per agent identity) read on agent startup and appended after execution. Format: structured log entries (timestamp, event, context, outcome, lesson).

```
/data/memory/
  orchestrator.md
  workflows/
    invoice-processing.md
    contract-review.md
  steps/
    invoice-processing/
      extract.md
      analyze.md
      route.md
```

Memory is included in the agent's system prompt (or as context) at startup. As memory files grow, we'll need summarization or a retrieval mechanism — but text files are the right starting point for simplicity and debuggability.

### D6: Parallel Step Execution with Budget Inheritance

Steps can execute in parallel. Token budgets follow an inheritance chain with split-by-default:

```
System budget (admin-configured)
  └── Workflow budget (designer-configured, or inherits from system)
       └── Step budget (designer-configured, or inherits from workflow)
            └── Parallel agents split their parent's budget equally (unless overridden)
```

Resolution logic:
1. If the step has an explicit budget → use it
2. Else if the workflow has a budget → split among parallel steps
3. Else use the system budget → split among active workflows → split among parallel steps

Override: any level can set `"budget_mode": "dedicated"` with an explicit token count, bypassing the split logic.

### D7: Agent-to-Agent Communication (Escalation Chain)

Agents can request help from the next level up. Requests bubble upward until resolved:

```
Step Agent → Workflow Agent → Orchestrator → Human Operator
```

A help request includes:
- What the agent was trying to do
- What went wrong or what it's uncertain about
- What it needs (guidance, permission, more budget, different tools)

Each level can:
- Resolve the request (provide guidance, grant permission, extend budget)
- Pass it up (if it can't resolve or doesn't have authority)
- Deny it (agent must handle the failure)

The human operator is the final escalation point. Escalations to humans are delivered via the configured notification channels (dashboard, Slack, SMS, email — see D10 below).

### D8: Workflow Graph Mutations with Approval

Workflow agents CAN propose modifications to the graph at runtime. But modifications require approval:

| Mutation Type | Approval Required |
|--------------|-------------------|
| Add an optional logging/notification step | Auto-approved (low risk) |
| Skip an optional step | Workflow agent can self-approve, logged |
| Add a new processing step | Orchestrator approval |
| Remove a required step | Human approval |
| Change step tools or permissions | Human approval |
| Modify another workflow | Denied (never allowed) |

The approval matrix is configurable per workflow. Designers can mark steps as `required` (can't be skipped/removed without human approval) or `optional` (agent can skip if it determines the step isn't needed).

All mutations are logged in the audit trail with the agent's reasoning for proposing the change.

### D9: Configurable Agent Transparency

Agent reasoning is always captured but displayed at configurable verbosity:

| Level | Shows |
|-------|-------|
| **Summary** (default) | One-line status per step: "Extracted 5 fields from invoice" |
| **Reasoning** | Agent's decision rationale: "Chose to route to human review because amount exceeds $50K policy" |
| **Full trace** | Complete chain-of-thought, all tool calls, all LLM inputs/outputs |

UI supports click-through: summary view → click a step → reasoning view → click "show full trace" → raw LLM conversation.

Configurable per user role:
- Viewers see summary only
- Operators see summary + reasoning
- Designers and admins can access full trace
- Auditors can access full trace (read-only)

### D10: Intelligent Error Handling Dashboard

The dashboard is the primary monitoring interface but must not overwhelm:

**Intelligent summarization:**
- The orchestrator (as an LLM) summarizes system status in natural language: "12 workflows running normally. 1 paused awaiting approval. Invoice #2026-0042 failed at extraction — likely a corrupted PDF."
- Errors are grouped and deduplicated: "3 workflows failed with the same Bedrock timeout in the last 5 minutes" (not 3 separate alerts)
- Severity classification: info / warning / error / critical

**Notification channels for serious errors (configurable per severity):**
- Dashboard alert (always)
- Email
- Slack message
- SMS / text message
- Webhook (for PagerDuty, OpsGenie, etc.)
- Push notification (future)

**User actions from the dashboard:**
- Retry failed step
- Skip failed step and continue
- Pause workflow
- Kill workflow
- Provide input (answer an agent's question)
- Approve/reject a proposed mutation

### D11: Testing — Replay Mode + Mocked Execution

**Replay mode:**
- Record all LLM calls (inputs + outputs) during a real workflow execution
- Replay the workflow using recorded responses — deterministic, free, fast
- Useful for regression testing: "does this workflow change break existing behavior?"

**Mock mode:**
- Execute workflows against mocked systems: fake file system, mock APIs, sample documents
- Agents run with real LLM calls but tools operate on sandboxed/mocked resources
- Useful for development and validation before deploying to production

**Dry-run mode (from prototype):**
- Execute the workflow graph but simulate all tool calls without side effects
- Returns "would have done X" at each step
- Useful for quick validation of workflow structure

Further testing strategies (property-based testing, agent behavioral contracts, etc.) to be designed later.

### D12: Workflow Versioning

When a workflow definition changes, the behavior for in-flight instances is configurable:

| Strategy | Behavior | Use When |
|----------|----------|----------|
| `run_to_completion` | In-flight instances finish with the version they started on | Default. Safe. No surprises. |
| `immediate_replace` | In-flight instances are stopped and restarted with new version | Urgent fixes, security patches |
| `drain_and_replace` | No new instances start on old version; in-flight finish; then old version is retired | Graceful rollout |
| `parallel_run` | New instances use new version; old instances finish on old version; both versions active temporarily | A/B testing, gradual migration |

Default is `run_to_completion`. Configurable per workflow definition.

Version history is retained. Rollback to any previous version is supported.

### D13: Deployment — SaaS First, Self-Hosted Available

**SaaS (initial focus):**
- Multi-tenant, hosted by us
- Tenant isolation at the data layer (separate databases/storage per tenant)
- Shared compute with per-tenant resource limits
- Managed upgrades, monitoring, backups
- AWS-hosted (ECS/Fargate for backend, S3 for storage, RDS/DynamoDB for state)

**Self-hosted (for security-conscious organizations):**
- Same codebase, different deployment configuration
- Docker Compose for simple deployments
- Helm chart for Kubernetes (EKS/ECS/on-prem)
- Customer provides their own AWS account (for Bedrock access) or connects to their own LLM endpoint
- Customer manages their own IdP integration
- No data leaves the customer's environment

Both deployment modes use the same API surface and UI. The difference is infrastructure and data residency.

---

## Development Approach: Test-Driven Development

Every feature is built test-first. Tests are written that fail, then code is written to make them pass, then refactored.

### TDD by Layer

| Layer | Test Strategy |
|-------|--------------|
| Agent framework | Unit tests: tool-use loop, budget enforcement, capability checks, memory load/append. Mock the Bedrock API — assert on what's sent and how responses are handled. |
| Workflow engine | Unit tests: DAG execution order, parallel step spawning, context accumulation, conditional edge evaluation, pause/resume, retry. No LLM needed — engine is deterministic. |
| Orchestrator | Unit tests: escalation routing, budget inheritance resolution, anomaly detection thresholds. Mock the LLM for reasoning tests — assert on decisions given specific system states. |
| Connectors | Contract tests: mock the external API, verify trigger/send/query behavior matches the expected protocol. No real external calls in unit tests. |
| Permissions | Unit tests: capability intersection, role checks, inheritance chain resolution. Pure logic, highly testable. |
| Cost metering | Unit tests: token counting, budget tracking, limit action triggers. Pure math. |
| Agent decisions | Replay tests: record a real LLM response once, then test deterministically against it. Verifies that surrounding logic (parsing, routing, tool dispatch) works correctly regardless of LLM variability. |
| UI | Component tests: verify behavior (clicks, state changes, data binding), not pixel rendering. |

### Test Execution Order per Feature

1. Write failing test(s) that define the expected behavior
2. Write the minimum code to pass
3. Refactor while keeping tests green
4. Add edge case tests
5. Integration test combining the new feature with existing components

### What We Don't TDD

- LLM prompt quality — evaluated empirically with a benchmark suite of inputs and human-judged outputs
- Visual design — tested manually or with snapshot tests after implementation
- Performance — benchmarked separately, not part of the red/green cycle

---

## Innovation: Simulated Environments for Agent Testing

### The Problem

Before an agentic workflow touches real systems (production SharePoint, live databases, actual email), we need confidence it will behave correctly. Traditional dry-run mode ("would have done X") tells you the structure is right but doesn't test whether the agent's reasoning works against realistic data. And you can't safely test against production.

### The Solution: Mock Worlds

A **mock world** is a simulated environment that looks real to agents but is entirely sandboxed. Agents interact with it using the same tools and connectors they'd use in production — they don't know it's simulated.

```
┌─────────────────────────────────────────────────────┐
│                    MOCK WORLD                        │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────┐  │
│  │ Virtual      │  │ Virtual      │  │ Virtual  │  │
│  │ SharePoint   │  │ Database     │  │ Email    │  │
│  │              │  │              │  │ Inbox    │  │
│  │ /invoices/   │  │ vendors      │  │          │  │
│  │  inv-001.pdf │  │ ┌──┬───┬──┐ │  │ 3 emails │  │
│  │  inv-002.pdf │  │ │id│name│..│ │  │          │  │
│  │              │  │ └──┴───┴──┘ │  │          │  │
│  └──────────────┘  └──────────────┘  └──────────┘  │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │ Virtual      │  │ Virtual      │                 │
│  │ Slack        │  │ File System  │                 │
│  │              │  │              │                 │
│  │ #finance     │  │ /processed/  │                 │
│  │ #general     │  │ /archive/    │                 │
│  └──────────────┘  └──────────────┘                 │
└─────────────────────────────────────────────────────┘
```

### How It Works

1. **Define a mock world** — either manually ("create a SharePoint with 5 sample invoices") or by snapshotting a real system ("clone the current state of our SharePoint library, anonymized")

2. **Swap the connector layer** — the agent framework routes tool calls through mock connectors instead of real ones. The mock connectors implement the same interface but operate on in-memory/local state.

3. **Run the workflow** — agents execute with real LLM calls (they reason for real) but all side effects land in the mock world. The agent believes it's talking to SharePoint — it's actually talking to a dict.

4. **Inspect results** — after execution, examine the mock world's state. Did the agent file the invoice correctly? Did it post the right message to the mock Slack channel? Did it corrupt anything?

5. **Assert on outcomes** — write tests against mock world state: `assert mock_sharepoint.files["/processed/inv-001.pdf"] exists` / `assert mock_slack.messages["#finance"][0].contains("Invoice from Acme")`

### Mock World Capabilities

| Capability | Description |
|-----------|-------------|
| **Seeding** | Populate with sample data: files, database rows, emails, messages |
| **Snapshotting** | Clone real system state (with anonymization/redaction) for realistic testing |
| **Behavior simulation** | Simulate failures: "SharePoint returns 503 on the 3rd request" / "database has a 2-second latency" |
| **State inspection** | Query the mock world's state at any point: what was created, modified, deleted |
| **Deterministic replay** | Same seed + same LLM responses = same outcome. Reproducible tests. |
| **Scenario generation** | LLM-generated test scenarios: "create a mock world with edge cases for invoice processing" (meta — use AI to test AI) |
| **Time simulation** | Fast-forward time to test scheduled triggers, timeouts, SLA breaches |

### Why This Is Novel

Most workflow/automation tools test with either:
- **Dry-run** (no real execution, just "would do X") — doesn't test agent reasoning against real data
- **Staging environments** (copy of production) — expensive, stale, hard to maintain, can't simulate failures

Mock worlds give you:
- Real agent reasoning (LLM calls happen for real)
- Realistic data (seeded or snapshotted from production)
- Zero risk (nothing touches real systems)
- Failure injection (test how agents handle errors)
- Deterministic assertions (test outcomes, not just structure)
- Cheap and fast (no infrastructure, runs locally)

### Integration with TDD

Mock worlds are the testing primitive for agentic workflows:

```python
def test_invoice_workflow_routes_high_value_to_approval():
    # Arrange: create a mock world with a $50K invoice
    world = MockWorld()
    world.sharepoint.add_file("/invoices/big-one.pdf", sample_invoice(amount=50000))
    world.database.seed("vendors", [{"id": 1, "name": "Acme", "approved": True}])

    # Act: run the workflow against the mock world
    result = run_workflow("invoice-processing", world=world, llm=recorded_responses)

    # Assert: high-value invoice went to approval, not auto-processed
    assert world.slack.messages["#finance"][0].contains("awaiting approval")
    assert world.sharepoint.files.get("/processed/big-one.pdf") is None  # not yet filed
    assert result.steps["route"].output["decision"] == "review"
```

### Mock World as a Product Feature

This isn't just for our internal testing — it's a feature we expose to users:

- **"Test my workflow"** button in the UI: runs the workflow against a mock world and shows results
- **Sandbox mode for new workflows**: first N executions automatically run against a mock world before going live (Anti-Goal #4: sandbox first)
- **What-if scenarios**: "what would happen if this invoice was $100K instead of $5K?"
- **Training mode**: new users can experiment with workflows without risk
- **Compliance validation**: "prove this workflow handles PII correctly" — run against mock data containing PII, verify it's redacted in outputs

---

## Next Steps

1. Write detailed implementation plan with task breakdown and phasing
2. Scaffold the project structure
3. Build the agent framework (Bedrock tool-use loop with capability enforcement)
4. Build the workflow engine (async DAG executor with parallel support)
5. Build the orchestrator (LLM agent with passive monitoring + active reasoning)
6. Build the permission/auth layer (OIDC integration, RBAC, agent capabilities)
7. Build agent memory system
8. Port PDF extraction and action executors as tools
9. Build the UI (natural language authoring + live workflow graph + intelligent dashboard)
10. Build cost control and budget enforcement
11. Build notification system (Slack, SMS, email, webhook)
12. Build testing infrastructure (replay, mock, dry-run modes)
