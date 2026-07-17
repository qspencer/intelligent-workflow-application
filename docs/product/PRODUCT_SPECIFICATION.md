# Intelligent Workflow Platform — Product Specification

## Executive Summary

An AI-powered workflow automation platform that combines the integration breadth of Zapier, the visual power of Make, the self-hosting freedom of n8n, and the agentic intelligence of Relevance AI — with enterprise governance that none of them fully deliver.

Users describe what they want in natural language. The system builds it, executes it, and gets better over time.

**Target market:** Technical and semi-technical teams at organizations with 50–5,000 employees who need automation that goes beyond "if-this-then-that" but can't justify $7K+/year enterprise iPaaS contracts.

**Positioning:** The first workflow platform where AI is the engine, not a bolt-on.

---

## Core Feature Set (Parity Requirements)

These capabilities are table-stakes. Every successful competitor has them. Without these, the product is not viable.

### P1. Visual Workflow Builder

**What competitors offer:** Make leads with a polished infinite canvas. Zapier has a linear step editor. n8n has a functional node graph.

**Our parity requirement:**
- Canvas-based workflow graph (nodes + edges)
- Drag-and-drop step creation and connection
- Visual data flow between steps
- Conditional branching with visual routing
- Parallel path visualization
- Step configuration panels (click node → edit)
- Real-time execution visualization (highlight active step, show data flowing)

**Parity target:** Make's visual quality with n8n's technical depth.

---

### P2. Integration Library

**What competitors offer:** Zapier: 8,000+. Make: 2,000+. n8n: 1,100+.

**Our parity requirement:**
- 100+ first-party connectors at launch (Tier 1 systems)
- Generic HTTP/webhook connector (covers any API)
- OAuth 2.0 flow support for standard SaaS auth
- Connector SDK for building new integrations
- Bidirectional: every connector works as trigger AND action

**Launch connectors (Tier 1):**
- Microsoft 365 (SharePoint, Outlook, Teams)
- Google Workspace (Drive, Gmail, Sheets)
- Slack
- Salesforce
- Jira
- HubSpot
- S3 / AWS services
- Generic REST/Webhook
- PostgreSQL / MySQL
- SMTP/IMAP (generic email)

**Parity target:** n8n's breadth at launch, with a path to Make-level coverage via SDK + community.

---

### P3. Trigger System

**What competitors offer:** All support webhook, schedule, polling, and manual triggers.

**Our parity requirement:**
- Webhook triggers (incoming HTTP)
- Schedule/cron triggers
- Event-driven triggers (file created, record updated via connectors)
- Manual triggers (API call, UI button)
- Workflow-to-workflow triggers (one workflow's output starts another)

**Parity target:** Feature-complete with Make/n8n. No gaps.

---

### P4. Execution Engine

**What competitors offer:** All handle sequential and parallel execution, retries, error handling.

**Our parity requirement:**
- DAG execution (topological ordering with parallel branch support)
- Conditional edges (if/else branching)
- Loop/iterator support (process arrays item-by-item)
- Error handling per step (retry, skip, fail, custom handler)
- Timeout enforcement (per-step and per-workflow)
- Pause/resume capability
- Execution history with full input/output per step

**Parity target:** n8n's execution model with Make's error handling UX.

---

### P5. Authentication & Security

**What competitors offer:** Enterprise plans include SSO, RBAC, audit logs.

**Our parity requirement:**
- OIDC/SAML SSO integration
- Role-based access control (Admin, Designer, Operator, Viewer)
- Credential vault (encrypted storage for API keys, tokens)
- Audit trail (who did what, when, queryable)
- OAuth 2.0 token management (automatic refresh)

**Parity target:** Workato/Power Automate governance level, available from day one (not enterprise-gated).

---

### P6. Monitoring & Observability

**What competitors offer:** Execution logs, error alerts, basic dashboards.

**Our parity requirement:**
- Execution log with step-by-step detail
- Error notifications (configurable channel: email, Slack, webhook)
- Workflow status dashboard (running, failed, paused counts)
- Execution history with search/filter
- Basic metrics (success rate, avg duration, error rate per workflow)

**Parity target:** n8n's execution detail with a cleaner presentation.

---

### P7. Team Collaboration

**What competitors offer:** Shared workspaces, role-based access, workflow sharing.

**Our parity requirement:**
- Shared workspace with workflow ownership
- Role-based access to workflows (view, edit, execute)
- Version history on workflow definitions
- Comments/annotations on workflows (optional v2)

**Parity target:** Make Teams tier level.

---

### P8. Self-Hosted Deployment

**What competitors offer:** Only n8n offers self-hosting (fair-code license, Docker).

**Our parity requirement:**
- Docker Compose for single-machine deployment
- Helm chart for Kubernetes
- Customer provides their own LLM access (Bedrock, OpenAI, local)
- All data stays on customer infrastructure
- Same features as cloud version (no feature-gating)

**Parity target:** n8n's self-hosting ease, but with full feature parity (not a stripped "community edition").

---

## Key Differentiators

These are capabilities that no competitor fully delivers. They define our unique position in the market.

### D1. Natural Language as the Primary Interface

**Gap exploited:** Lindy has natural language but limited integrations/governance. Zapier/Power Automate have Copilots but they only assist in building traditional flows — they don't replace the configuration interface.

**What we do differently:**
- Conversation IS the primary creation method, not an assistant within a traditional UI
- "When a new invoice PDF arrives in our SharePoint folder, extract the vendor name and amount, check it against our approved vendor list, and if it's under $5,000 auto-approve it. Otherwise send it to Sarah for review."
- The system builds the workflow in real-time, showing the graph forming as it interprets the description
- Iterative refinement: "Actually, also CC the finance team on anything over $10,000"
- Status inquiries: "What's happening with today's invoices?"
- Troubleshooting: "Why did that last invoice fail?"

**Why this wins:** Zero learning curve. Non-technical users can automate complex processes without understanding workflow concepts. Technical users can iterate faster than drag-and-drop allows.

---

### D2. Hybrid Deterministic + Agentic Steps

**Gap exploited:** Traditional platforms are 100% deterministic (can't handle unexpected situations). AI-native platforms are 100% agentic (expensive, slow, unpredictable). Nobody blends both in one workflow graph.

**What we do differently:**
- Each step in a workflow is explicitly typed: `deterministic` (runs a function — fast, free, predictable) or `agentic` (LLM with tools — flexible, reasoning-capable)
- The system recommends which type based on the task: file moves are deterministic, document classification is agentic
- Agentic steps have defined goals, tools, and budgets — they're not unbounded
- Hybrid steps: code handles the 95% case, agent handles exceptions

**Why this wins:** 10x cheaper than fully-agentic platforms (most steps don't need an LLM). 10x more capable than fully-deterministic platforms (agents handle the unexpected). Users get the right tool for each part of the workflow.

---

### D3. Self-Improving Workflows (Adaptive Learning)

**Gap exploited:** Zero competitors learn from execution outcomes. All are static — they execute exactly what you configured, forever.

**What we do differently:**
- **Agent memory:** Agents remember past successes and failures. "Last time this vendor's PDF failed extraction because of their unusual format — using a different approach this time."
- **Automatic optimization:** After N successful executions with consistent logic, agentic steps are automatically codified into deterministic functions (free, instant).
- **Cost analyst:** A dedicated agent observes all spend and recommends optimizations. "Step 3 produces identical results with a cheaper model. Switching saves $240/month."
- **Threshold learning:** "Users override the agent's decision 20% of the time for invoices in the $4K-$6K range — the threshold should be $6K, not $5K."
- **Proactive suggestions:** "You've manually handled 20 of these this month — want me to automate it?"

**Why this wins:** Workflows get cheaper, faster, and more accurate over time without human optimization effort. No competitor offers this.

---

### D4. Transparent Cost Control with Budget Intelligence

**Gap exploited:** Per-task/per-operation pricing is unpredictable. Credit systems are opaque. Enterprise contracts hide costs entirely. Nobody gives real-time cost visibility with intelligent management.

**What we do differently:**
- **Real-time cost attribution:** See exactly what each workflow costs, broken down by step, by model, by day
- **Three-level budgets:** Step → Workflow → System, with configurable actions when limits hit (notify, degrade model, pause, stop)
- **Automatic model selection:** Use the cheapest model that can handle each task; escalate only when needed. Powered by the LLM Evaluation Suite (see `LLM_EVAL_FRAMEWORK.md`) — models are scored against a 50-case benchmark; the cost analyst only recommends models that pass the quality threshold for each task type.
- **Graceful degradation:** When approaching budget limits, switch to cheaper models rather than stopping — using eval-validated fallback models so quality is guaranteed.
- **Predictive forecasting:** "At current growth, you'll hit the monthly ceiling in 18 days"
- **Idle = free:** No LLM calls when nothing is happening. Deterministic monitoring loop costs nothing.
- **Cost analyst module:** A dedicated component that correlates execution history with eval results to produce actionable recommendations: "Step 3 used Sonnet for 200 runs. Our eval proves Haiku handles this task type at 94% accuracy. Switching saves $240/month." Recommendations surface in the UI and require human approval before applying.

**How the eval suite connects to cost intelligence:**
```
LLM Eval Suite (proves which models pass for which task types)
       +
Execution History (shows which models are actually used per step)
       +
Cost Attribution (knows the $ difference between models)
       =
Cost Analyst Recommendations (switch to cheapest passing model per step)
```

**Why this wins:** Users always know what they're spending and why. The system actively works to reduce costs without sacrificing quality — and can *prove* quality is maintained because every recommendation is backed by eval scores. Nobody else does this.

---

### D5. Agent Capability Boundaries (Least Privilege for AI)

**Gap exploited:** AI-native platforms give agents broad access. Enterprise platforms restrict humans but not AI. Nobody applies security principles to AI agents specifically.

**What we do differently:**
- Every agent has an explicit capability set: which tools it can use, which files it can access, which APIs it can call
- Capability inheritance: System → Workflow → Step (most restrictive wins)
- Agents cannot exceed their permissions — they escalate rather than assume
- Complete audit trail of every agent action with reasoning
- Administrators can answer: "Show me all workflows that access customer data" / "What did this agent do with the Salesforce connector?"

**Why this wins:** Enterprise security teams can approve AI automation without open-ended risk. Compliance officers can demonstrate exactly what AI agents can and cannot do.

---

### D6. Mock Environments for Safe Testing

**Gap exploited:** No competitor offers a way to safely test AI-powered workflows against realistic simulated environments.

**What we do differently:**
- **Mock worlds:** Simulated environments (fake SharePoint, mock databases, test inboxes) that look real to agents
- **Safe testing:** Real LLM reasoning against fake systems — zero risk to production
- **Failure injection:** "What happens if SharePoint returns a 503 on the 3rd request?"
- **Outcome assertions:** Write tests: "verify the invoice was filed correctly and Slack was notified"
- **Sandbox-first:** New workflows run against mock data before touching real systems
- **What-if scenarios:** "What would happen if this invoice was $100K instead of $5K?"

**Why this wins:** Enterprises can validate AI behavior before production deployment. Reduces the #1 barrier to AI adoption: fear of unpredictable behavior.

---

### D7. Generative UI (Personalized Dashboard)

**Gap exploited:** All competitors have fixed dashboard layouts. Users see what the product team decided to show them.

**What we do differently:**
- No fixed dashboard — the UI is a canvas users compose through conversation
- "Show me a graph of invoices processed per hour in the top left"
- "Add a list of failed workflows below that"
- "When something fails, flash it red"
- Each user's view is unique and persistent
- Contextual presets: "Set me up for an accounting team" → generates appropriate widgets

**Why this wins:** Every user sees exactly what they need, nothing they don't. Zero wasted screen real estate. No documentation needed to "find the right page."

---

## Pricing Model

**Design principle:** Align price with value delivered, not with implementation complexity.

| Plan | Price | Includes |
|------|-------|----------|
| **Starter** | $29/mo | 1,000 workflow executions, 3 users, all integrations, community support |
| **Pro** | $79/mo | 10,000 executions, 10 users, SSO, priority support |
| **Team** | $199/mo | 50,000 executions, 25 users, SSO, RBAC, audit trail, dedicated support |
| **Enterprise** | Custom | Unlimited executions, unlimited users, SLA, on-premise, custom integrations |
| **Self-hosted** | Free (open core) | Unlimited everything. Pay only for LLM costs (your own API keys). |

**Pricing philosophy:**
- Per-execution (like n8n), not per-step/per-operation — complex workflows don't cost more
- All integrations available on all plans (no connector paywalling)
- Security features (SSO, RBAC, audit) available from Pro tier, not just Enterprise
- Self-hosted is full-featured, not a stripped community edition
- LLM costs are pass-through at cost (no markup on AI model usage)

---

## Non-Functional Requirements

### Performance

| Metric | Target |
|--------|--------|
| Workflow start latency | <500ms from trigger to first step execution |
| Step-to-step latency (deterministic) | <50ms |
| Concurrent workflow instances | 500+ per deployment |
| Dashboard load time | <2s |
| WebSocket update latency | <200ms |

### Reliability

| Metric | Target |
|--------|--------|
| Platform uptime (SaaS) | 99.9% |
| Workflow execution guarantee | At-least-once (with idempotency support) |
| Data durability | 99.99% (Postgres + backups) |
| Recovery time objective (RTO) | <1 hour |

### Security

| Requirement | Implementation |
|-------------|---------------|
| Authentication | OIDC/SAML SSO, API keys for service accounts |
| Authorization | RBAC (human) + capability-based (agent) |
| Encryption at rest | AES-256 (Postgres, file storage) |
| Encryption in transit | TLS 1.3 |
| Secrets management | AWS Secrets Manager (SaaS), customer vault (self-hosted) |
| Audit trail | Immutable, append-only, queryable, exportable |
| Data residency | Customer choice (SaaS: US/EU; self-hosted: anywhere) |
| Compliance targets | SOC 2 Type II, GDPR |

---

## Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Backend | Python, FastAPI, async | Strong AI/ML ecosystem, proven async performance |
| LLM | AWS Bedrock (multi-model) | Multi-model selection, enterprise-grade, no data retention |
| Database | PostgreSQL + pgvector | Handles relational, vector, and JSON workloads in one system |
| Frontend | React (Vite + React Flow) | The canvas is the UI centerpiece; React Flow (`@xyflow/react`) is its best-in-class substrate. Migrated from Angular 2026-05. |
| Real-time | WebSocket | Live workflow status, agent reasoning streams |
| Deployment | Docker / Kubernetes | SaaS on ECS/Fargate, self-hosted via Docker Compose or Helm |
| File storage | S3 (SaaS), local filesystem (self-hosted) | Scalable, cost-effective |
| Search | pgvector + full-text | Vector similarity for knowledge retrieval, FTS for audit queries |

---

## User Personas

### Persona 1: Operations Manager (Primary)

**Profile:** Non-technical, manages business processes, currently doing manual work or using Zapier/Make for simple automations.

**Needs:** Automate complex multi-system workflows without learning to program. Understand what the automation is doing. Trust it won't break things.

**How we serve them:** Natural language interface, visual workflow graph, intelligent defaults, sandbox testing, plain-language status updates.

---

### Persona 2: IT Administrator (Buyer/Enabler)

**Profile:** Technical, responsible for security and compliance, evaluates tools for the organization.

**Needs:** Governance, audit trails, SSO integration, data residency control, ability to restrict what AI can access.

**How we serve them:** Agent capability boundaries, RBAC, immutable audit trail, self-hosted option, admin dashboard ("show me all workflows accessing customer data").

---

### Persona 3: Developer/Technical Power User (Builder)

**Profile:** Comfortable with code, wants maximum control, dislikes opaque abstractions.

**Needs:** Custom code in workflows, API access, self-hosting, version control integration, testing infrastructure.

**How we serve them:** Python/JS code steps, JSON/YAML workflow definitions, Git export/import, mock worlds, replay testing, full API access.

---

## MVP Scope (v1.0)

**Goal:** Ship the minimum product that demonstrates all differentiators while maintaining core parity.

### Included in v1.0:
- Natural language workflow creation (D1)
- Visual workflow builder (P1)
- Hybrid deterministic + agentic steps (D2)
- 20 first-party connectors (Tier 1 subset) + generic HTTP/webhook (P2)
- All trigger types (P3)
- DAG execution engine with parallel support (P4)
- Basic RBAC + audit trail (P5)
- Execution monitoring dashboard (P6)
- Real-time cost tracking per workflow (D4)
- Agent capability enforcement (D5)
- Docker Compose self-hosted deployment (P8)
- Mock environments + dry-run testing (D6 — already built: MockWorld, "sandbox the world, keep the brain")
- Basic agent memory (D3 — already built: file-backed rubric memory + veracium learned per-entity memory; not the full optimization loop)
- Cost analyst module (D4 — eval-backed model recommendations, human-approved)
- LLM evaluation suite for scaffold + execution model quality validation

### Deferred to v1.1+:
- Full adaptive learning / auto-optimization (D3 advanced — auto-codification, threshold learning)
- Generative UI (D7) — ship with a well-designed fixed dashboard first
- Auto-apply cost recommendations without human approval (requires confidence data)
- Connector SDK (community connectors)
- Helm chart / Kubernetes deployment
- SOC 2 certification

---

## Success Metrics

| Metric | Target (6 months post-launch) |
|--------|-------------------------------|
| Time to first workflow | <10 minutes for a non-technical user |
| Workflow creation via NL vs visual | >60% of workflows started via natural language |
| Execution cost vs pure-agentic | 5-10x cheaper (due to hybrid deterministic/agentic) |
| Self-hosted deployments | 500+ |
| Net Promoter Score | >50 |
| Churn rate | <5% monthly |
| Avg workflows per active user | >5 |

---

## Competitive Positioning Summary

| vs Competitor | Our advantage |
|---------------|---------------|
| vs Zapier | Deeper AI (agentic reasoning, not just prompt/response). Per-execution pricing. Self-hosted. |
| vs Make | AI-native architecture. Natural language authoring. Self-improving workflows. |
| vs n8n | Natural language interface. Agent memory. Enterprise governance from day one. Mock worlds. |
| vs Power Automate | Not locked to Microsoft. Transparent pricing. True agentic AI, not just Copilot. Self-hosted. |
| vs Workato/Tray | 10x lower entry price. Self-service onboarding. Accessible to non-technical users. |
| vs Relevance AI | 100x more integrations. Enterprise governance. Self-hosted. Hybrid deterministic/agentic (cheaper). |
| vs Lindy | Enterprise-ready. Complex multi-system workflows. Self-hosted. Visual builder for power users. |

---

## Open Questions

1. **Open source vs open core?** n8n uses fair-code. Should we go full open source (AGPL), open core (community + enterprise), or source-available?
2. **MCP support?** Zapier's MCP integration lets external AI agents trigger actions. Should we expose an MCP server so Claude/ChatGPT can invoke our workflows?
3. **Marketplace?** Connector/template marketplace (like Make's) for community contributions and monetization?
4. **AI model flexibility?** Support only Bedrock, or also OpenAI, Anthropic direct, Google, local (Ollama)? Multi-provider from day one?
5. **Mobile experience?** Is a mobile app needed for monitoring/approvals, or is responsive web sufficient?
