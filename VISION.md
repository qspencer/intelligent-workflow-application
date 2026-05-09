# Intelligent Workflow Platform — Product Vision

## One-Liner

An AI-powered workflow platform that you talk to, not configure.

---

## Core Philosophy

The system should feel like hiring a competent assistant who already knows how your tools work. You tell it what you need done, it figures out the details, connects to your systems, and gets to work. If it needs clarification, it asks. If something goes wrong, it tells you in plain language and offers solutions.

Configuration screens, JSON files, and technical setup are available for those who want control — but they are never required. The default experience is conversational.

---

## Product Goals

### 1. Natural Language is the Primary Interface

Everything the system does should be achievable through conversation:

- **Create a workflow**: "When a new invoice PDF arrives in our SharePoint folder, extract the vendor name and amount, check it against our approved vendor list, and if it's under $5,000 auto-approve it. Otherwise send it to Sarah for review."
- **Modify a workflow**: "Actually, also CC the finance team on anything over $10,000."
- **Check status**: "What's happening with the invoices from today?"
- **Troubleshoot**: "Why did that last invoice fail?"
- **Configure the system**: "Connect to our company's SharePoint. Here's the site URL."
- **Set policies**: "Don't spend more than $50/day on AI processing."
- **Manage permissions**: "Give the finance team access to the invoice workflows."

The system translates natural language into actions, confirms when appropriate, and executes. Users never need to learn a DSL, navigate a settings hierarchy, or understand the underlying data model.

### 2. Intelligent Defaults Everywhere

The system should work well out of the box with minimal input. When a decision can be made reasonably, make it:

| Situation | Intelligent Default |
|-----------|-------------------|
| User doesn't specify a model | Use the cheapest model that can handle the task; escalate to a more powerful model if it struggles |
| User doesn't set a budget | System-wide default that prevents runaway costs but doesn't block normal operation |
| User doesn't specify error handling | Retry once, then pause and notify (don't silently fail, don't spam retries) |
| User doesn't specify file destination | Infer from context: "processed invoices" → `/processed/invoices/` |
| User doesn't specify notification channel | Use whatever channel they've communicated through most recently |
| User doesn't specify permissions for a new workflow | Inherit from the team/project it belongs to |
| User doesn't specify versioning strategy | Run existing instances to completion, new instances use new version |
| User creates a workflow similar to an existing one | Suggest reusing/extending the existing one rather than duplicating |

The principle: **every setting has a sensible default. Users only configure what they care about.**

### 3. Seamless Connectivity with Existing Software

The platform meets users where they already work. It doesn't ask them to change their tools or processes — it plugs into them:

- **Input from anywhere**: SharePoint, Google Drive, email, Slack, S3, webhooks, databases, ERPs
- **Output to anywhere**: same systems, bidirectionally
- **No manual data transfer**: users should never need to download from one system and upload to another — the platform handles the bridge
- **Authentication once**: connect a system once, use it across all workflows
- **Discovery**: the system should be able to explore connected systems ("what folders are in our SharePoint?" "what fields does this Salesforce object have?") so agents can work with them intelligently

The goal is that the platform becomes invisible infrastructure — users interact with their familiar tools and the automation happens behind the scenes.

### 4. Progressive Disclosure of Complexity

The system has depth for power users but doesn't expose it by default:

| User Need | What They See |
|-----------|--------------|
| "Automate my invoices" | Conversational setup, workflow runs in background |
| "I want to see what it's doing" | Visual workflow graph, step-by-step progress |
| "I want to tweak the logic" | Click into steps, edit goals/conditions/tools |
| "I want full control" | JSON/YAML definitions, API access, custom tools |
| "I want to extend the platform" | Connector SDK, tool SDK, plugin architecture |

No user is forced up this ladder. A non-technical user can operate the system entirely through conversation and never see a configuration screen. A developer can bypass the conversation entirely and work with definitions and APIs.

### 5. The System Gets Smarter Over Time

The platform learns from every interaction:

- **Agent memory**: past successes and failures inform future decisions
- **Workflow optimization**: "this step always takes 30 seconds and never fails — skip the expensive model, use the cheap one"
- **Suggestion engine**: "you've manually handled 20 of these this month — want me to automate it?"
- **Error prevention**: "last time a PDF from this vendor arrived, the extraction failed because of their unusual format — I'll use a different approach this time"
- **User preference learning**: "you always want invoices over $10K flagged — I'll apply that to new workflows by default"

The system should require less configuration over time, not more.

### 6. Trust Through Transparency

Users must trust the system to let it operate autonomously. Trust is built through:

- **Explainability**: every decision an agent makes can be inspected — what it saw, what it considered, why it chose what it chose
- **Predictability**: same input produces same (or similar) output; no mysterious behavior
- **Boundaries**: agents can't exceed their permissions; the system can't do things the user hasn't authorized
- **Reversibility**: actions can be undone where possible; destructive actions require confirmation
- **Audit trail**: complete history of everything the system has done, queryable and exportable
- **Graceful degradation**: when the AI is uncertain, it asks rather than guesses; when a system is down, it pauses rather than fails silently

### 7. Enterprise-Ready from Day One

The platform must be deployable in organizations with strict IT requirements:

- **Security**: OIDC/SAML SSO, RBAC, agent capability boundaries, encrypted at rest and in transit
- **Compliance**: immutable audit logs, data residency controls, retention policies
- **Operations**: health monitoring, alerting, backup/restore, zero-downtime upgrades
- **Scale**: handle hundreds of concurrent workflows without degradation
- **Self-hosted option**: for organizations that cannot put data in a shared cloud
- **IT admin experience**: also conversational — "show me all workflows that access customer data" / "revoke the finance team's access to the HR workflows"

---

## What Success Looks Like

A user signs up, connects their SharePoint and Slack, and says:

> "When a new PDF arrives in our Invoices folder, figure out what it is, pull out the important information, and post a summary to the #finance channel. If it's over $10,000, also ping Sarah for approval before filing it."

The system:
1. Creates the workflow (shows it building in real time)
2. Connects to SharePoint and Slack (asks for auth if not already connected)
3. Starts watching the folder
4. Processes the first invoice within seconds of it arriving
5. Posts to Slack with a clean summary
6. Learns from each document it processes and gets better at extraction

The user never sees a configuration screen, never writes JSON, never reads documentation. They just described what they wanted and it happened.

---

## What This Is NOT

- Not a no-code tool with a visual builder as the primary interface (though one exists for those who want it)
- Not a chatbot — it's an autonomous system that runs workflows in the background; conversation is for setup and oversight, not for every interaction
- Not a replacement for existing tools — it's the glue between them
- Not a general-purpose AI assistant — it's specifically for automating workflows that span multiple systems

---

## Anti-Goals: What We Refuse to Build

These are failure modes we design against explicitly. Every architectural decision should be tested against this list.

### 1. "Setup takes longer than doing it manually"

The system must be operational within minutes of first use, not days. A user should be able to describe a workflow and have it running before they'd finish reading a setup guide.

Design rules:
- Zero mandatory configuration before the first workflow runs
- Connections to external systems happen inline ("connect to SharePoint" during workflow creation, not as a prerequisite in a settings screen)
- No required training, onboarding wizard, or multi-step setup flow
- If the system needs information, it asks for it at the moment it's needed — not upfront in a form

### 2. "The system won't shut up"

Alerts, confirmations, and questions must be rare and high-value. The system should operate autonomously and only surface things that genuinely require human attention.

Design rules:
- **Batch, don't spam**: 10 similar warnings become one summary, not 10 notifications
- **Decide, don't ask**: if the system can make a reasonable decision, it makes it and logs the reasoning — it doesn't ask permission for routine choices
- **Escalate with context**: when the system does need human input, it explains why, what it already tried, and what the options are — not just "error occurred"
- **Respect attention budgets**: configurable quiet hours, notification frequency caps, severity thresholds below which the system stays silent
- **Learn what matters**: if a user dismisses a type of alert repeatedly, stop showing it (or reduce its priority)
- **Default to confidence**: the system should project competence, not anxiety. "I handled this" not "is this okay? is this okay? is this okay?"

### 3. "It works but costs a fortune"

The system must be economically viable at scale. An idle system should cost nearly nothing. A busy system should cost proportionally less per unit of work, not more.

Design rules:
- **Cheap by default**: use the least expensive model that can handle each task; only escalate to expensive models when the cheap one fails or the task demonstrably requires it
- **Don't use AI where deterministic logic works**: file moves, API calls, data formatting — these don't need an LLM. Only use agents for decisions that require reasoning.
- **Cache aggressively**: if the same type of document has been analyzed 100 times, the system should recognize the pattern and skip the expensive analysis
- **Budget visibility**: users always know what the system is costing them, in real time, without having to look for it
- **Graceful degradation over hard stops**: when approaching budget limits, switch to cheaper models and batch processing rather than stopping all work
- **Idle = free**: no background LLM calls when nothing is happening. The orchestrator's passive monitoring loop is deterministic (free). LLM is only invoked when there's something to reason about.
- **Per-workflow cost attribution**: users can see which workflows cost what, identify expensive ones, and optimize or accept the cost consciously

### 4. "It broke everything it touched"

The system must be safe by default. It should be harder to corrupt data than to process it correctly. When errors occur, they should be contained — not propagated to connected systems.

Design rules:
- **Read before write**: agents verify state before modifying it. Don't overwrite a file without confirming the destination is correct.
- **Validate before sending**: output to external systems is validated against expected schemas before transmission. Malformed data never leaves the platform.
- **Sandbox first**: new workflows run in mock/dry-run mode on their first execution (or first N executions) before being allowed to affect real systems. Configurable, but the default is cautious.
- **Blast radius containment**: a failing workflow cannot affect other workflows. A failing agent cannot corrupt shared state. Errors are isolated to the instance that caused them.
- **Reversibility by default**: where possible, actions are reversible (move not delete, create not overwrite). Destructive actions require explicit confirmation and are logged with enough context to undo manually.
- **Rate limiting on external systems**: agents cannot flood a connected system with requests. Built-in rate limiting per connector, respecting the target system's limits.
- **Data integrity checks**: before writing to databases or updating records, verify the data makes sense (type checking, range validation, referential integrity where applicable)
- **Circuit breaker pattern**: if a connector fails repeatedly, stop trying and alert — don't keep hammering a broken system
- **Immutable audit trail**: even if data is corrupted, the audit trail records exactly what happened, enabling diagnosis and recovery
