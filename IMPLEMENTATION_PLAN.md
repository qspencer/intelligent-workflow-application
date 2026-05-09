# Intelligent Workflow Platform — Implementation Plan

Ordered sequence of implementation phases. Each phase builds on the previous.

---

## Phase 1: Foundation

### 1.1 Project scaffolding

- Python backend with FastAPI, async throughout
- Angular frontend with routing, base layout
- Docker Compose for local development (backend, frontend, Postgres)
- CI pipeline skeleton (lint, test, build)
- Monorepo structure: `backend/`, `frontend/`, `shared/` (schemas), `tools/`, `docs/`

### 1.2 Agent framework

The core primitive everything else depends on. A thin wrapper around Bedrock's `converse` API with tool use.

- `Agent` class: takes a system prompt, a set of tools, a model ID, and a policy (token budget, max tool calls)
- Tool-use loop: send message → receive response → if tool call, execute tool, send result back → repeat until done or budget exhausted
- Tool interface: `name`, `description`, `input_schema`, `execute(params) → result`
- Policy enforcement: count tokens, count tool calls, enforce limits, raise on violation
- Structured output parsing: agent returns typed results (not just free text)
- Logging: every LLM call and tool call recorded with timestamps and token counts

### 1.3 Agent memory system

- Memory store: text files per agent identity (as decided in D5)
- `MemoryManager` class: `load(agent_id) → str`, `append(agent_id, entry)`
- Memory entries are structured: timestamp, event type, context summary, outcome, lesson
- Memory injected into agent system prompt on startup
- Memory file rotation/summarization when file exceeds configurable size (future: LLM-summarized compaction)

### 1.4 Persistence layer

- Postgres + pgvector for runtime state: workflow instances, step executions, audit log, knowledge embeddings
- JSON files for definitions: workflow definitions, tool configs, policies
- Application-level in-memory caching for hot paths: budget counters, active agent state, knowledge manifest
- Repository pattern: abstract storage interface for testability and future flexibility
- Migration system for schema changes (Alembic)

---

## Phase 2: Workflow Engine

### 2.1 Workflow definition model

- Data model for workflow definitions: steps, edges, triggers, policies
- Validation: DAG check (no cycles unless explicitly marked as loops), required fields, tool references resolve
- CRUD API: create, read, update, delete, list, export (JSON/YAML), import
- Version storage: every save creates a new version, old versions retained

### 2.2 DAG executor

- Async execution engine that walks the workflow graph
- Topological sort to determine execution order
- Parallel execution of independent steps (no dependency between them)
- Context accumulation: shared dict that steps read/write
- Conditional edges: `"condition": "context.route.decision == 'approve'"` evaluated at runtime
- Step types: `deterministic` (call a function) and `agentic` (spawn an agent with goal + tools)

### 2.3 Workflow instance lifecycle

- States: `pending` → `running` → `completed` | `failed` | `paused` | `cancelled`
- Step states: `pending` → `running` → `completed` | `failed` | `skipped` | `waiting_for_input`
- Pause/resume: workflow can be paused (by orchestrator, by human, by budget limit) and resumed
- Retry: individual steps can be retried without restarting the whole workflow
- Timeout: per-step and per-workflow timeouts, configurable action on timeout

### 2.4 Budget enforcement in the executor

- Token counter per step, per workflow instance, per system
- Budget inheritance resolution: step → workflow → system (as decided in D6)
- Parallel budget splitting: divide parent budget among concurrent steps
- Configurable limit actions: notify, pause, degrade, stop, escalate
- Model degradation: when `degrade` is triggered, switch agent to a cheaper model and continue

---

## Phase 3: Orchestrator

### 3.1 Passive monitoring loop

- Background async task that polls system state on an interval
- Checks: stuck workflows (no progress for N minutes), token burn rate, error rate, queue depth
- Thresholds configurable per metric
- When thresholds crossed → invoke the orchestrator LLM agent for active reasoning

### 3.2 Orchestrator agent

- LLM agent with system-wide context: active workflows, recent errors, resource usage, memory
- Tools available to orchestrator: pause_workflow, resume_workflow, kill_workflow, retry_step, reassign_budget, send_notification, modify_priority, spawn_workflow
- Decision-making: given the current system state and an anomaly, decide what to do
- Cross-workflow coordination: detect dependencies, prevent resource contention
- Memory: records decisions and outcomes for future reference

### 3.3 Escalation handling

- Receive escalation requests from workflow agents and step agents
- Evaluate: can the orchestrator resolve this? (check its own capabilities and policies)
- If yes: resolve and respond to the requesting agent
- If no: escalate to human via configured notification channel
- Track escalation state: pending → resolved | timed_out
- Human responses fed back to the requesting agent

---

## Phase 4: Triggers

### 4.1 Trigger plugin architecture

- `Trigger` interface: `start()`, `stop()`, `on_event(callback)`
- Trigger registry: discover and load trigger plugins at startup
- Each trigger, when fired, creates a workflow instance with the event payload as initial context

### 4.2 File watcher trigger (PDF primary use case)

- Port from prototype: watchdog-based folder monitoring
- Configurable: folder path, file pattern (default `*.pdf`), polling interval
- On new file: create workflow instance with `{"file_path": "...", "filename": "...", "size": ...}` as context

### 4.3 Webhook trigger

- HTTP endpoint that accepts POST requests
- Configurable: path, expected payload schema, authentication (API key, HMAC)
- On request: validate, create workflow instance with request body as context

### 4.4 Schedule trigger

- Cron-style scheduling
- Configurable: cron expression, timezone
- On fire: create workflow instance with `{"triggered_at": "...", "schedule": "..."}` as context

### 4.5 Manual trigger

- API endpoint and UI button to start a workflow with user-provided input
- Configurable: input schema (what fields the user must provide)

---

## Phase 5: Tools

### 5.1 Tool framework

- Tool interface: `name`, `description`, `parameters_schema`, `execute(params, context) → result`
- Tool registry: discover and load tools at startup
- Permission check before execution: does the calling agent have this tool in its capability set?
- Execution logging: every tool call recorded in audit trail

### 5.2 Port existing tools from prototype

- `pdf_extract`: PyMuPDF for native, pytesseract + pdf2image for scanned, auto-detect
- `file_operation`: move, copy, delete files
- `notify`: webhook, email
- `api_call`: HTTP requests with configurable method, headers, body
- `cli_command`: shell command execution
- `gui_automation`: pyautogui-based desktop automation
- `web_automation`: Playwright headless browser

### 5.3 New tools

- `llm_analyze`: call Bedrock with a prompt and document text, return structured analysis
- `database_query`: read/write Postgres or configured external database
- `lookup`: key-value lookup against configured data sources (vendor lists, policy tables)
- `send_notification`: multi-channel (Slack, SMS, email, webhook) — used by orchestrator and workflows
- `create_workflow_instance`: one workflow can trigger another

---

## Phase 6: Integrations / Connectors

### 6.1 Connector framework

- `Connector` interface: `trigger_listen()`, `trigger_poll()`, `send(payload)`, `query(params)`, `authenticate()`, `health_check()`
- Connector registry: discover and load connector plugins at startup
- Credential management: store auth tokens/keys in secrets backend (AWS Secrets Manager for SaaS, configurable vault for self-hosted)
- OAuth 2.0 flow helper: handle authorization code grant, token refresh, token storage
- Connectors register both as triggers (event sources) and as tools (agents can use them for output)

### 6.2 Generic webhook + S3 connectors

- Webhook: incoming HTTP POST triggers workflows; outgoing HTTP requests as output
- S3: EventBridge notification on object created triggers workflows; PutObject as output
- These two cover the most flexible integration points and validate the framework

### 6.3 Microsoft 365 connectors (SharePoint, Outlook, Teams)

- Single OAuth 2.0 app registration against Microsoft Graph API
- SharePoint: trigger on file created/modified in library; output: upload file, create list item
- Outlook: trigger on email received (with attachment filter); output: send email, create event
- Teams: trigger on channel message / adaptive card response; output: post message, send card

### 6.4 Google Workspace connectors (Drive, Gmail, Sheets)

- Google service account or OAuth 2.0 for user-delegated access
- Drive: trigger on file created/modified; output: upload, create doc
- Gmail: trigger on email received; output: send email, apply label
- Sheets: trigger on row added; output: append row, update cells

### 6.5 Slack connector

- Bot token authentication
- Trigger: message in channel, slash command, reaction
- Output: post message, upload file, send DM, update message

### 6.6 Enterprise connectors (Salesforce, ServiceNow, Jira)

- Prioritize based on customer demand
- Salesforce: trigger on record created/updated; output: create/update record
- ServiceNow: trigger on incident/change request; output: create/update ticket
- Jira: trigger on issue created/transitioned; output: create issue, add comment, transition

### 6.7 Connector development kit

- Scaffold generator for new connectors
- Standard test harness with mocked external APIs
- Auth flow helpers (OAuth dance, token refresh)
- Documentation template
- Independent versioning per connector

---

## Phase 7: Security & Permissions

### 6.1 Authentication

- OIDC integration: configure IdP (Okta, Azure AD, AWS IAM Identity Center, Keycloak)
- Token validation middleware in FastAPI
- API key authentication for service-to-service and CI/CD
- Session management for the web UI

### 6.2 Human RBAC

- Role definitions: Admin, Workflow Designer, Operator, Viewer, Auditor
- Role-to-IdP-group mapping (configured in admin settings)
- Permission checks on all API endpoints
- UI adapts to role: hide/disable features the user can't access

### 6.3 Agent capability enforcement

- Capability definitions stored per workflow definition and per step
- Runtime enforcement: before a tool executes, check the agent's capability set
- File access ACLs: agent can only read/write paths in its allowed list
- Network ACLs: agent can only call allowed hosts
- Capability inheritance: system policy → workflow → step → runtime (most restrictive wins)

### 6.4 Audit trail

- Append-only log of all actions (human and agent)
- Stored in Postgres (partitioned by time for efficient retention and querying), exportable to external systems
- Fields: timestamp, actor (type + id), action, params, result, policy check result
- Query API: filter by actor, action, time range, workflow
- Retention policy: configurable, default 90 days

---

## Phase 8: Frontend

### 7.1 Natural language workflow authoring

- Chat-style interface: user describes what they want in plain text
- Backend agent (the "authoring agent") translates description into a workflow definition
- Workflow definition rendered as a graph in real time as the agent builds it
- User can iterate: "add a step that checks the vendor against our blocklist"
- Authoring agent has tools: create_step, connect_steps, set_trigger, set_policy

### 7.2 Visual workflow editor

- Graph canvas showing workflow steps as nodes, edges as connections
- Drag to reorder, connect, disconnect
- Click a node to edit: step type, goal (for agentic), function (for deterministic), tools, policy
- Add/remove steps via palette or right-click menu
- Changes update the underlying definition in real time
- JSON/YAML view toggle for power users

### 7.3 Intelligent monitoring dashboard

- System status summary (orchestrator-generated natural language)
- Active workflow instances with progress indicators
- Error/warning panel with grouped, deduplicated alerts
- Click-through: instance → step → agent reasoning → full trace (D9 transparency levels)
- Actions: retry, pause, resume, kill, provide input, approve/reject mutations

### 7.4 Real-time updates

- WebSocket connection for live status streaming
- Events: workflow started/completed/failed, step progress, agent reasoning (streaming), escalation requests, budget alerts
- Notification preferences: configure which events trigger Slack/SMS/email

### 7.5 Settings and administration

- IdP configuration
- Role-to-group mapping
- System-wide policies: budgets, default models, notification channels
- Tool configuration: API keys, file paths, database connections
- Workflow definition management: versions, rollback, import/export

---

## Phase 9: Cost Control

### 8.1 Token metering

- Count input and output tokens for every Bedrock call
- Attribute to: step agent → workflow instance → workflow definition → system
- Store in time-series format for reporting

### 8.2 Budget enforcement

- Real-time budget tracking at all three levels (step, workflow, system)
- Configurable actions on limit hit (notify, pause, degrade, stop, escalate)
- Model degradation: maintain a model preference order (e.g., Sonnet → Haiku → Ministral) and fall back when budget is tight

### 8.3 Cost reporting

- Dashboard widget: spend by workflow, by time period, by model
- Alerts: approaching budget, budget exceeded
- Projections: "at current rate, system budget will be exhausted in N days"

---

## Phase 10: Testing Infrastructure

### 9.1 Replay mode

- Record all LLM interactions during workflow execution (inputs, outputs, tool calls, results)
- Store recordings as JSON files per workflow instance
- Replay engine: execute workflow using recorded responses instead of live Bedrock calls
- Deterministic, free, fast — useful for regression testing

### 9.2 Mock mode

- Sandboxed tool execution: file operations on a temp directory, API calls to mock servers, database operations on isolated test schema
- Real LLM calls but no real side effects
- Useful for development and pre-production validation

### 9.3 Dry-run mode

- Walk the workflow graph, simulate tool calls, return "would have done X" at each step
- No LLM calls, no side effects
- Useful for quick structural validation of workflow definitions

### 9.4 Automated test suite

- Unit tests for agent framework, workflow engine, tools, permission checks
- Integration tests: full workflow execution with replay mode
- End-to-end tests: API → workflow → tools → results

---

## Phase 11: Deployment

### 10.1 SaaS infrastructure (initial focus)

- Containerized backend (Docker)
- AWS deployment: ECS/Fargate for compute, RDS Postgres for state, S3 for file storage
- Tenant isolation: separate database schemas or separate databases per tenant
- Auto-scaling based on active workflow count
- Managed secrets (AWS Secrets Manager)

### 10.2 Self-hosted packaging

- Docker Compose for single-machine deployment
- Helm chart for Kubernetes
- Configuration for customer-provided Bedrock access (their AWS account)
- Documentation: deployment guide, IdP integration guide, network requirements

### 10.3 Operational tooling

- Health check endpoints
- Structured logging (JSON) for log aggregation
- Metrics export (Prometheus/CloudWatch)
- Backup/restore procedures for state and definitions
