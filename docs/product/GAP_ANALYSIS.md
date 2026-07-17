# Gap Analysis: Current Implementation vs Product Specification

## Overview

This document compares the current state of the Intelligent Workflow Application (189 commits, Phases 0–2 complete, C1–C8 canvas cuts complete) against the Product Specification (`PRODUCT_SPECIFICATION.md`) to identify what's built, what's missing, and what's needed to close the gaps.

**Summary verdict:** The implementation is significantly ahead of the spec in some areas (execution engine, testing infrastructure, trust/governance UX) and has notable gaps in others (integration breadth, adaptive learning, generative UI, self-hosted packaging).

---

## Parity Requirements — Status

### P1. Visual Workflow Builder ✅ COMPLETE

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Canvas-based workflow graph | ✅ React Flow (`@xyflow/react`) |
| Drag-and-drop step creation | ✅ Edit mode with add/connect |
| Visual data flow between steps | ✅ |
| Conditional branching with visual routing | ✅ Conditional edges with condition labels |
| Parallel path visualization | ✅ |
| Step configuration panels | ✅ Inspector with catalog pickers |
| Real-time execution visualization | ✅ Live status on nodes during runs |

**Assessment:** Exceeds spec. The canvas has gone through 8 iterative cuts (C1–C8) with competitive analysis-driven refinement.

---

### P2. Integration Library ⚠️ PARTIAL (5 of 100+ target)

| Spec requirement | Implementation status |
|-----------------|---------------------|
| 100+ first-party connectors at launch | ❌ Currently: 5 (Webhook, S3, Gmail, Filesystem, Browser/Playwright) |
| Generic HTTP/webhook connector | ✅ WebhookConnector (outbound HTTP) + webhook trigger |
| OAuth 2.0 flow support | ✅ Gmail OAuth implemented |
| Connector SDK for building new integrations | ✅ `Connector` ABC with 6-method interface + `ConnectorRegistry` |
| Bidirectional (trigger + action) | ✅ Architecture supports both |

**Missing connectors (from spec Tier 1):**
- ❌ Microsoft 365 (SharePoint, Outlook, Teams)
- ❌ Google Drive / Sheets (only Gmail done)
- ❌ Slack
- ❌ Salesforce
- ❌ Jira
- ❌ HubSpot
- ❌ PostgreSQL / MySQL (as a user-facing connector, not internal DB)
- ❌ SMTP/IMAP (generic email — only Gmail-specific)

**Assessment:** The connector framework is solid and extensible. The gap is volume — adding connectors is implementation work, not architectural work.

---

### P3. Trigger System ✅ COMPLETE

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Webhook triggers | ✅ With HMAC verification |
| Schedule/cron triggers | ✅ `ScheduleTrigger` via croniter |
| Event-driven triggers | ✅ Filesystem watch + Gmail poll |
| Manual triggers | ✅ API + UI "Run" button |
| Workflow-to-workflow triggers | ❌ Not yet (deferred as E1 sub-workflows) |

**Assessment:** 4 of 5 complete. Workflow-to-workflow is the E1 deferred epic.

---

### P4. Execution Engine ✅ COMPLETE (exceeds spec)

| Spec requirement | Implementation status |
|-----------------|---------------------|
| DAG execution | ✅ Kahn's topological sort |
| Parallel execution | ✅ asyncio.wait FIRST_COMPLETED, edge-driven readiness |
| Conditional edges | ✅ simpleeval-sandboxed expressions + skip propagation |
| Loop/iterator support | ⚠️ Batch run exists but no in-workflow loop node |
| Error handling per step | ✅ Retry, skip, fail, timeout |
| Timeout enforcement | ✅ Per-step + per-workflow |
| Pause/resume | ✅ Including fork-from-step |
| Execution history | ✅ Full step-by-step with inputs/outputs |

**Assessment:** Exceeds spec with fork-from-step, budget enforcement, and deterministic replay. Loop/iterator as a workflow primitive is the one gap.

---

### P5. Authentication & Security ✅ COMPLETE

| Spec requirement | Implementation status |
|-----------------|---------------------|
| OIDC/SAML SSO | ✅ OIDC (PyJWT + JWKS). SAML not implemented. |
| RBAC | ✅ 5 roles (Admin, Designer, Operator, Viewer, Auditor) |
| Credential vault | ✅ SecretStore ABC + EnvSecretStore + AwsSecretsManagerStore |
| Audit trail | ✅ Immutable, per-transition + per-tool-call |
| OAuth 2.0 token management | ✅ Gmail OAuth with auto-refresh |

**Assessment:** Complete. SAML is a minor gap for some enterprises.

---

### P6. Monitoring & Observability ✅ COMPLETE (exceeds spec)

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Execution log with step-by-step detail | ✅ |
| Error notifications | ✅ Threshold-based monitoring + audit events |
| Workflow status dashboard | ✅ Automations home with status pills |
| Execution history with search/filter | ✅ |
| Basic metrics | ✅ Prometheus `/metrics` + structured JSON logs |

**Assessment:** Exceeds spec with Prometheus metrics, LLM-as-judge evaluation, live WebSocket events, and cost reports.

---

### P7. Team Collaboration ⚠️ PARTIAL

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Shared workspace with ownership | ✅ Single workspace, role-gated |
| Role-based access to workflows | ✅ RBAC enforced |
| Version history | ❌ Deferred (E5 epic) |
| Comments/annotations | ❌ Not implemented |

**Assessment:** Basic collaboration works. Version history and comments are the gaps.

---

### P8. Self-Hosted Deployment ⚠️ PARTIAL

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Docker Compose | ✅ `docker-compose.yml` (Postgres + backend) |
| Helm chart for Kubernetes | ❌ Not implemented |
| Customer-provided LLM access | ✅ `BEDROCK_MODE` + model env vars |
| All data on customer infrastructure | ✅ Architecture supports this |
| Same features as cloud version | ✅ No feature-gating |

**Assessment:** Docker Compose works. Helm chart is missing. Terraform exists but is "validate-clean, not yet applied."

---

## Key Differentiators — Status

### D1. Natural Language as Primary Interface ✅ COMPLETE

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Conversation IS the primary creation method | ✅ "Describe it" dialog → draft → canvas edit |
| System builds workflow in real-time | ✅ NL scaffold via one Bedrock call → React Flow canvas |
| Iterative refinement | ⚠️ Edit on canvas, but not via continued conversation |
| Status inquiries | ❌ No conversational status ("what's happening with today's invoices?") |
| Troubleshooting | ⚠️ Explain-this-run exists but not conversational |

**Assessment:** The one-shot NL scaffold + visual edit loop is implemented. The ongoing conversational interface (ask questions, get status, troubleshoot in natural language) is not — the spec envisions an always-available chat panel.

---

### D2. Hybrid Deterministic + Agentic Steps ✅ COMPLETE (exceeds spec)

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Explicitly typed steps | ✅ `deterministic` + `agentic` discriminated union |
| System recommends type | ⚠️ NL scaffold chooses, but no explicit recommendation |
| Agentic steps have goals + budgets | ✅ Per-step token budgets + tool allowlists |
| Hybrid steps (code + agent fallback) | ❌ Not implemented as a step type |

**Assessment:** Core architecture is complete and validated across 8+ example workflows. The "hybrid" step type (code handles 95%, agent handles exceptions) isn't a distinct primitive yet.

---

### D3. Self-Improving Workflows ⚠️ PARTIAL

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Agent memory across executions | ✅ MemoryManager (file-backed Markdown) + auto-loading |
| Automatic codification (agentic → deterministic) | ❌ Not implemented |
| Cost analyst agent | ❌ Not implemented (deferred) |
| Threshold learning | ❌ Not implemented |
| Proactive suggestions | ❌ Not implemented |
| Learned per-entity memory | ✅ Veracium-backed, write-only slice live |

**Assessment:** The foundation is there (agent memory + learned memory store), but the spec's auto-optimization loop (codification, cost analyst, suggestions) is entirely unbuilt. This is the biggest differentiator gap.

---

### D4. Transparent Cost Control ✅ COMPLETE (exceeds spec)

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Real-time cost attribution | ✅ Per-step, per-model, per-day reports |
| Three-level budgets | ✅ Step → Workflow → System, with configurable actions |
| Automatic model selection | ❌ Manual model assignment only |
| Graceful degradation | ⚠️ Budget actions (pause/notify/escalate) but no auto-downgrade to cheaper model |
| Predictive forecasting | ❌ Not implemented |
| Idle = free | ✅ Deterministic monitoring loop, LLM only on-demand |
| Cost analyst module | ❌ Not implemented |

**Assessment:** Cost visibility and enforcement are excellent. The intelligent cost management (auto-model-selection, graceful degradation, forecasting) is not built. However, with the LLM Eval Framework (`LLM_EVAL_FRAMEWORK.md`) now designed, the path to the cost analyst is clear:

1. The eval suite proves which models pass for which task types (scaffold, classification, triage, etc.)
2. Execution history already records which model ran each step and what it cost
3. The cost analyst correlates (1) with (2): "step X uses Sonnet but Haiku passes the eval for this task type — switch to save $Y/month"

**Promoted to near-term build** (no longer deferred). See Priority 2.5 in the implementation plan below.

---

### D5. Agent Capability Boundaries ✅ COMPLETE (exceeds spec)

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Explicit capability sets per agent | ✅ Layered allowlist intersection |
| Capability inheritance | ✅ System → Workflow → Step → Runtime |
| Agents escalate rather than assume | ✅ `RequestHumanReviewTool` + escalation API |
| Complete audit trail with reasoning | ✅ Per-tool-call audit + explain-this-run |
| Admin query ("show me all workflows accessing X") | ⚠️ Capabilities endpoint exists but no query/search UI |

**Assessment:** This is the strongest area — implemented from Phase 0, surfaces in the GUI via C6, validated across all workloads.

---

### D6. Mock Environments for Safe Testing ✅ COMPLETE

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Mock worlds | ✅ `MockWorld` (side-effect-free fs/messaging/db) |
| Safe testing (real LLM, fake systems) | ✅ Dry-run: "sandbox the world, keep the brain" |
| Failure injection | ❌ No configurable failure simulation |
| Outcome assertions | ✅ Tests assert on MockWorld state |
| Sandbox-first for new workflows | ⚠️ Available as "Test" button, not enforced as default |
| What-if scenarios | ❌ No UI for parameter variation |

**Assessment:** Core mock world + dry-run exists and is surfaced in the GUI. The advanced features (failure injection, what-if) are not built.

---

### D7. Generative UI ❌ NOT IMPLEMENTED (deferred)

| Spec requirement | Implementation status |
|-----------------|---------------------|
| Conversational dashboard composition | ❌ Fixed React dashboard |
| "Show me X" dynamic components | ❌ |
| Per-user persistent layouts | ❌ |
| Contextual presets | ❌ |

**Assessment:** Entirely deferred. The spec notes this should ship post-v1.0, and the current implementation defers it with a well-designed fixed dashboard instead. The PRODUCT_SPECIFICATION.md agrees (D7 is in "Deferred to v1.1+").

---

## Additional Gaps Not in Spec Differentiators

| Gap | Current state | Impact |
|-----|--------------|--------|
| **Frontend framework mismatch** | React (migrated from Angular) | ~~Spec says Angular~~ — spec updated to React (2026-07-16). |
| **Multi-tenancy** | Not implemented | Spec mentions SaaS multi-tenant. Current is single-tenant. |
| **Knowledge library / RAG** | Deferred (10 research questions) | Spec's knowledge system is unbuilt. Agent memory exists but no shared knowledge library. |
| **Connector count** | 5 vs spec's 100+ target | Biggest volume gap. Framework is ready. |
| **In-workflow loops/iterators** | Only batch-run (external). No loop node. | Most competitors have iterators/loops as a primitive. |
| **Conversational interface** | One-shot scaffold only | Spec envisions ongoing conversation for status, troubleshooting, modification. |
| **Model auto-selection** | Manual per-step | Spec's "cheapest model first, escalate when needed" is not automated. |
| **SAML** | Not implemented | Some enterprises require SAML over OIDC. |

---

## Scorecard Summary

| Area | Spec requirement | Status | Gap severity |
|------|-----------------|--------|-------------|
| P1 Visual Builder | Complete | ✅ | None |
| P2 Integrations | 100+ connectors | ⚠️ 5 built | **High** (volume) |
| P3 Triggers | Complete | ✅ | Low (workflow-to-workflow) |
| P4 Execution Engine | Complete | ✅ | Low (loop primitive) |
| P5 Security | Complete | ✅ | Low (SAML) |
| P6 Monitoring | Complete | ✅ | None |
| P7 Collaboration | Version history + comments | ⚠️ | Medium |
| P8 Self-Hosted | Helm chart | ⚠️ | Medium |
| D1 NL Interface | Ongoing conversation | ⚠️ | Medium |
| D2 Hybrid Steps | Complete | ✅ | Low (hybrid type) |
| D3 Self-Improving | Auto-optimization loop | ❌ | **High** (key differentiator) |
| D4 Cost Control | Auto-model-selection | ⚠️ | Medium |
| D5 Capabilities | Complete | ✅ | None |
| D6 Mock Environments | Core exists | ✅ | Low |
| D7 Generative UI | Not built | ❌ | Low (deferred per spec) |

---

## Plan to Close Gaps

### Priority 1: Integration Volume (P2) — 6-8 weeks

The connector framework is solid. This is pure implementation work with a known pattern.

**Approach:** Build connectors in order of market demand from the competitive analysis.

| Week | Connectors | Notes |
|------|-----------|-------|
| 1-2 | Slack (bot token, post/receive) | Highest-demand after email |
| 2-3 | Google Drive + Sheets | Extend existing Google OAuth |
| 3-4 | Microsoft 365 (Outlook + SharePoint) | Single Graph API OAuth flow |
| 4-5 | Jira + HubSpot | REST API, straightforward |
| 5-6 | Salesforce | OAuth 2.0, more complex schema |
| 6-7 | PostgreSQL/MySQL (user-facing) | DB connector for agent queries |
| 7-8 | SMTP/IMAP (generic email) | Supplement Gmail-specific |

**Target:** 20+ connectors (enough for launch given the generic HTTP connector covers the rest).

**Work per connector:** ~2-3 days following the existing pattern (ABC implementation + trigger + tools + capability gating + tests + catalog entry).

---

### Priority 2: Adaptive Learning Loop (D3) — 4-6 weeks

This is the strongest differentiator in the spec and the biggest functional gap.

#### What the Intelligence Layer Is

The "intelligence layer" is what separates this product from every competitor. It's the set of capabilities that make the system get smarter over time rather than just execute what you configured. Three tiers:

**Tier 1 — Memory (partially built):** The system remembers what happened and uses it in future decisions.
- ✅ Built: Agent memory files (structured Markdown, auto-loaded), memory hash in audit, veracium learned per-entity memory (write-only), fork-from-step as implicit correction signal, three validated rubric-iteration loops.
- ❌ Gap: Recall injection — the store writes but never reads back. When the email agent processes sender X, it doesn't receive "here's what I've observed about X from 47 previous messages."

**Tier 2 — Pattern Recognition (not built):** The system notices patterns across executions and surfaces them.
- "This step fails 30% for invoices from Vendor X" (error correlation)
- "This agentic step always produces the same logic — it's codifiable" (cost signal)
- "Users override routing 20% of the time in the $4K-$6K range" (threshold drift)
- "Monday morning failures correlate with SharePoint maintenance" (environmental)
- This tier makes the Cost Analyst Agent and proactive suggestions possible. Raw data exists (audit logs, step outputs, cost attribution); nothing queries it for patterns yet.

**Tier 3 — Autonomous Optimization (not built):** The system acts on patterns to improve itself.
- Auto-model-selection: switch to cheaper model when it handles a task identically
- Auto-codification: convert consistent agentic reasoning to deterministic functions
- Graceful degradation: approaching budget → cheaper models, not hard stop
- Adaptive thresholds: adjust decision boundaries based on outcome data
- Workflow restructuring: parallelize steps, eliminate redundancies

#### Why This Is the Critical Gap

No competitor has any of this:
- **Zapier/Make/n8n** — zero intelligence layer. Static execution forever.
- **Relevance AI** — per-entity memory exists, no cross-execution pattern recognition or optimization.
- **Lindy** — learns user preferences but doesn't optimize execution costs or accuracy.

The intelligence layer is the only differentiator that compounds over time. Integrations are a one-time effort. A better UI is one-time. But a system that gets measurably cheaper and more accurate with every execution creates a moat that widens with usage.

#### What Makes It Hard

The implementation deferred this deliberately (10 open research questions in `LEARNING_IMPLEMENTATION.md`):

1. **Confidence thresholds** — when has a pattern been observed enough to act on? Generalizing from 3 observations is dangerous; waiting for 1000 is too conservative.
2. **Quality measurement** — how do you know an optimization didn't degrade quality? LLM-as-judge evaluation exists for one workflow but isn't generalized.
3. **Codification safety** — converting agentic → deterministic is practically irreversible. Edge cases the code can't handle will surface later.
4. **Attribution** — if outcomes improve after a change, was it the optimization or something else (better input data, memory accumulation, model improvement)?

#### Implementation Plan

Sequenced so each phase validates before the next begins:

**Phase 1 (weeks 1-2): Recall injection from learned memory**
- The write-only veracium slice is live. Build the read side: inject relevant per-entity observations into agent context before execution.
- Query the learned memory store for entities relevant to the current input (sender, vendor, document type).
- Position injected facts per the lost-in-the-middle ordering (LEARNING.md).

**Phase 2 (weeks 2-3): Execution outcome tracking + pattern detection**
- After each run: record success/failure, user corrections (fork-from-step = implicit correction), timing, cost.
- Build a simple pattern detector: "this step always produces the same logic" / "this step fails 30% for vendor X."
- Surface patterns in a new "Insights" panel on the dashboard.

**Phase 3 (weeks 3-5): Cost-aware model recommendation**
- Track model × step-type × outcome correlations.
- When a cheaper model consistently succeeds for a step type, recommend the switch.
- Surface recommendations in the UI (don't auto-apply yet).

**Phase 4 (weeks 5-6): Proactive suggestion engine**
- "You've manually handled N of these — want to automate?"
- Workflow-level optimization suggestions based on execution history.
- Weekly digest of actionable optimizations.

**Deferred:** Full automatic codification (agentic → deterministic) — this requires confidence thresholds and quality tracking that need more data.

---

### Priority 2.5: Cost Analyst Module (D4) — 3-4 weeks

The eval framework + existing execution history + existing cost attribution make this buildable now.

**Week 1: LLM Eval Runner**
- Implement the eval runner: feed each test case to each model, score L1/L2 automatically, L3/L4 via judge model.
- Run the 50-case scaffold eval against Haiku, Sonnet, Mistral, and GPT-4o Mini.
- Store results in a model capability matrix: model × task_type × pass/fail + score.

**Week 2: Execution-to-Eval Correlation**
- Tag each step execution with a `task_type` (scaffold, classification, triage, extraction, routing, summarization).
- Build a query: "for task_type X, which models pass the eval and what do they cost?"
- Compare against actual model usage per step from execution history.
- Identify gaps: steps using expensive models where cheaper ones pass.

**Week 3: Recommendation Engine + UI**
- `CostAnalystService`: generates recommendations from the correlation data.
- Recommendation format: "Step X in workflow Y uses Sonnet ($3/M tokens). Haiku passes the eval for this task type at 94% accuracy ($1/M tokens). Estimated savings: $X/month at current volume."
- Surface in the dashboard: new "Cost Insights" panel showing active recommendations.
- Each recommendation has "Apply" (changes the step's model config) and "Dismiss" buttons.

**Week 4: Graceful Degradation**
- New `budget_action: degrade` option.
- When budget is approaching the limit, the engine consults the model capability matrix and switches remaining steps to the cheapest passing model.
- Audit entry: `model_degraded` with reason, original model, fallback model, eval score.
- Dashboard: budget meter shows when degradation is active.

**Connection to the eval suite:**
```
LLM_EVAL_TEST_SUITE.md (50 cases, scored)
         │
         ▼
Model Capability Matrix (model × task_type → pass/score/cost)
         │
         ▼
Cost Analyst (correlate with execution history → recommendations)
         │
         ▼
Dashboard "Cost Insights" panel (human approves or dismisses)
         │
         ▼
Graceful Degradation (auto-apply matrix during budget pressure)
```

---

### Priority 3: Conversational Interface Depth (D1) — 3-4 weeks

The one-shot scaffold exists. Extend to ongoing conversation.

**Week 1: Conversational status + troubleshooting**
- Add a chat panel component (always available, collapsible).
- Wire to a dedicated "assistant" agent with tools: `list_workflows`, `get_instance_status`, `get_recent_errors`, `explain_failure`.
- "What's happening with today's invoices?" → agent queries instances, returns natural language summary.

**Week 2: Conversational modification**
- Add tools: `modify_step`, `add_step`, `remove_step`, `update_trigger`.
- "Also CC the finance team on anything over $10K" → agent modifies the workflow definition.
- Changes shown on canvas in real-time, require user confirmation before save.

**Week 3-4: Conversational workflow iteration**
- Multi-turn scaffold: refine the initial draft through conversation rather than only visual editing.
- "Make the approval threshold configurable" → agent adjusts the workflow + surfaces a configuration point.

---

### Priority 4: Self-Hosted Packaging (P8) — 2 weeks

**Week 1: Helm chart**
- Kubernetes-native deployment (backend, Postgres, frontend as static files).
- ConfigMap for environment variables, Secret for credentials.
- Horizontal pod autoscaling for the backend.

**Week 2: Documentation + one-command setup**
- `helm install` guide.
- Customer-provided Bedrock/OpenAI configuration documentation.
- Air-gapped deployment notes (local model via Ollama).

---

### Priority 5: Collaboration Features (P7) — 2-3 weeks

**Week 1: Version history (E5 minimum)**
- `If-Match` header for optimistic concurrency on save.
- Store version history (each save = new version, old retained).
- UI: version timeline + diff view + rollback.

**Week 2-3: Multi-model support**
- Support OpenAI API alongside Bedrock.
- Support Ollama for local models (self-hosted deployments).
- Model selection per step with a "cheapest that works" auto option.

---

### Priority 6: Remaining Gaps — 2-4 weeks

| Gap | Effort | Approach |
|-----|--------|----------|
| In-workflow loop/iterator node | 1 week | New step type `iterator` that processes array items |
| SAML support | 3 days | Add python3-saml alongside existing OIDC |
| Automatic model degradation | 1 week | When budget_action=degrade, swap to next-cheapest model and retry |
| Failure injection in dry-run | 1 week | Config option on dry-run: "fail step X at attempt N" |
| Multi-tenancy (SaaS) | 2-3 weeks | Schema-per-tenant in Postgres, tenant context in middleware |

---

## Spec Updates Needed

> **Applied 2026-07-16** during the fold-in to `docs/product/`: items 1 (React), 4 (D6
> included), and 5 (agent memory complete) are now reflected in the spec; 2, 3, and 6
> were already true.

Based on the implementation's actual state, the PRODUCT_SPECIFICATION should be updated:

1. **Frontend:** Change "Angular" to "React" — the implementation migrated and React Flow is the correct choice for the canvas.
2. **Database:** Already aligned (Postgres + pgvector).
3. **Pricing table:** Add "LLM costs pass-through at cost" — already true in implementation.
4. **MVP scope:** Mark D6 (Mock Environments) as included — it's already built and surfaced in the GUI.
5. **MVP scope:** Move "Basic agent memory (D3)" from included to "Complete" — both file-backed and veracium learned memory are live.
6. **Success metric:** "Time to first workflow <10 minutes" — already achievable via NL scaffold + template gallery.

---

## Timeline Summary

| Priority | Gap | Weeks | Cumulative |
|----------|-----|-------|-----------|
| P1 | Integration volume (20+ connectors) | 6-8 | 6-8 |
| P2 | Adaptive learning loop | 4-6 | 10-14 |
| P2.5 | Cost analyst module (eval-backed) | 3-4 | 13-18 |
| P3 | Conversational interface depth | 3-4 | 16-22 |
| P4 | Self-hosted packaging (Helm) | 2 | 18-24 |
| P5 | Collaboration (version history) | 2-3 | 20-27 |
| P6 | Remaining gaps | 2-4 | 22-31 |

**Estimated total to close all gaps: 22-31 weeks (5.5-8 months) of single-developer effort.**

The implementation is roughly 60-70% of the way to the product specification's v1.0 MVP. The foundation (engine, security, governance, testing, canvas) is complete. What remains is primarily: connector volume, the intelligence layer (learning/optimization/cost analyst), and packaging/polish.
