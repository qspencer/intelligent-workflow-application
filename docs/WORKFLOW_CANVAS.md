# Workflow Canvas — Design

A graphical, Zapier-style view of a workflow: nodes for steps, lines
for the edges between them, a configuration panel on the side, and a
live-status overlay when an instance is running. The screen a
non-technical user lands on when they want to *understand*, *edit*, or
*operate* a workflow without reading YAML.

This is the centerpiece of the Phase 3 friendly-GUI work scoped in
`docs/BUILD_PLAN.md` and the missing rung on `docs/VISION.md` Goal 4's
progressive-disclosure ladder. It is **not** the generative UI from
`docs/GENERATIVE_UI.md` — that is a different, deferred, LLM-driven
surface. The canvas is a fixed UI: traditional design, traditional
controls, no LLM in the rendering loop.

---

## Scope and substrate decision (confirmed 2026-05-27)

The canvas is the product's intended **competitive differentiator** —
not a read-only viewer. Five capabilities define the ambition:

1. **Live editing of running workflows** — *definition-level only*:
   editing a workflow saves a new version; in-flight instances run to
   completion on the version they started with. This is **not**
   runtime DAG mutation, so `ARCHITECTURE.md` D8 stays deferred.
2. **Conditional-branch authoring** — a safe editor over the existing
   `simpleeval` conditional edges (users never write raw Python).
3. **Nested / sub-workflows** — a step that invokes another workflow.
   Requires new schema + engine support (epic E1 below).
4. **Real-time multi-user collaboration** — a CRDT/Yjs sync layer;
   definitions become live collaborative documents (epic E2 below).
5. **AI-assisted layout** — *A first*: deterministic auto-layout
   (dagre/elk). *B later*: LLM-suggested workflow structure from
   intent (epic E3; overlaps the conversational rung). B stays on the
   plan.

**Substrate: the frontend migrates Angular → React to adopt React
Flow (`@xyflow/react`).** Decided after evaluating `@foblex/flow`
(Angular-native, no migration) — but at ~491 GitHub stars / ~10
contributors / thin ecosystem it's the wrong risk profile for a
flagship surface that pushes the envelope on collaboration, nesting,
and layout. React Flow (~37k stars, ~6M weekly downloads, proven
nested-flow + Yjs-collab + elk-layout patterns) de-risks exactly the
hard parts. The migration is cheapest now while the frontend is small
(~2.2k LOC source + ~830 LOC tests).

**The rendering library is ~20% of the work.** The weight is backend
platform work — most of it sequenced as epics after the canvas surface
proves itself:

| Track | Item | Notes |
|---|---|---|
| Frontend (React Flow) | C1 read-only → C2 live status → C3 run-from-form → C4 edit + conditional authoring | Layout A (dagre/elk) lands as early as C1. |
| Backend epic **E1** | Sub-workflows | New schema concept, engine support, capability/budget/audit propagation across the boundary, cross-workflow cycle detection. |
| Backend epic **E2** | Real-time multi-user | Yjs/CRDT sync server + presence + doc persistence. Biggest single lift. |
| Backend epic **E3** | AI layout B | LLM-suggested structure; research-gated; overlaps `GENERATIVE_UI.md`. |

C1 + Layout A + C4 deliver a differentiated, demo-able editor early;
E1/E2/E3 follow, each with its own design pass.

---

## Audience and intent

| Persona | What they do here |
|---|---|
| Business operator (Sarah from finance) | Opens the canvas, sees what the workflow does, runs it for a single document or a batch. Never edits. |
| Workflow designer (light technical) | Edits a workflow by dragging in steps and filling forms. Never opens a YAML file. |
| Developer / platform engineer | Treats the canvas as a quick-look diagnostic. Drops to `/workflows/:id/yaml` or the API when they need full control. |

The canvas is the **primary** surface for personas 1 and 2 and a
**convenience** surface for persona 3.

---

## Anchoring references

Zapier, Make.com (formerly Integromat), n8n, and Power Automate all
ship variations of this concept. None of them map cleanly to our DAG
shape, so this design takes ideas from each:

| Anchor | What we borrow |
|---|---|
| **Zapier** | Top-down flow, "Choose app → choose event" trigger picker, an "Add step" affordance that inserts between existing steps rather than free-placing. |
| **Make.com** | Curved connector lines, color-coded node states during a run, modal-overlay node config (no leaving the canvas). |
| **n8n** | Horizontal layout option for wider workflows, ability to copy-paste a step. |
| **Power Automate** | "If/Then" branch nodes as a first-class metaphor (we'll call them **Paths**). |

What we explicitly do **not** copy:

- Zapier's strict linear-chain limit (our model is a DAG).
- n8n's free-canvas drag (no hand-positioning; layout is computed).
- Make.com's scenario-as-app feel (we want operability, not
  prosumer tinkering).

---

## Information architecture

The canvas screen has four regions:

```
┌──────────────────────────────────────────────────────────────────────┐
│ Header:  [Workflow name]  [Mode toggle: Edit | View]   [Run] [Save]  │
├──────────┬───────────────────────────────────────────┬───────────────┤
│          │                                           │               │
│  Step    │             CANVAS (the graph)            │  Inspector    │
│  Palette │                                           │  (right pane) │
│  (left)  │                                           │               │
│          │                                           │               │
│          │                                           │               │
├──────────┴───────────────────────────────────────────┴───────────────┤
│ Footer:  [Status: idle / running #abcd1234 / 2 of 5 steps complete]  │
└──────────────────────────────────────────────────────────────────────┘
```

- **Step palette (left, ~200px)** — drag-to-insert sources for new
  steps. Hidden in View mode.
- **Canvas (center, fills)** — the graph itself. Pan and zoom; no
  hand-positioning of nodes (layout is auto-computed).
- **Inspector (right, ~360px)** — context-sensitive. Shows the
  selected node's configuration (Edit mode) or last-run output (View
  mode). Empty when nothing is selected.
- **Footer status bar** — live state of the most recent instance.
  Click to open the instance-detail dashboard.

---

## The node model

Each step in `WorkflowDefinition.steps` renders as one node. The node
is one of three visual species:

### Trigger node (always exactly one, always the top of the graph)

```
   ┌────────────────────────────┐
   │  📥  When email arrives    │
   │      Gmail · every 60s     │
   └─────────────┬──────────────┘
                 │
```

- Icon picked from connector type (📥 Gmail, 📅 schedule, 🔗 webhook,
  📂 filesystem, ▶️ manual).
- Title is a plain-language sentence ("When email arrives", "On a
  schedule", "When a file lands in /inbox").
- Subtitle exposes the one config field a non-technical user cares
  about (the inbox name, the cron expression, the folder path).

### Step node — deterministic ("function block")

```
   ┌────────────────────────────┐
   │  ⚙️  Save the result        │
   │      record_email_triage   │
   └────────────────────────────┘
```

- Icon ⚙️ uniformly (deterministic = "the machine does it").
- Title is either an authored `label:` field on the step (new — see
  Schema changes below) or, falling back, a humanized version of
  `function` (snake_case → Title Case).
- Subtitle shows the function name for cross-reference with YAML.

### Step node — agentic ("AI block")

```
   ┌────────────────────────────┐
   │  🧠  Decide the category   │
   │      Claude Haiku 4.5      │
   └────────────────────────────┘
```

- Icon 🧠 uniformly (agentic = "the AI decides").
- Title is an authored `label:` or the first sentence of `goal:`,
  trimmed to ~60 chars.
- Subtitle shows the friendly model name (we resolve
  `us.anthropic.claude-haiku-4-5-…` to "Claude Haiku 4.5" via a
  lookup table reused from `cost/pricing.py`).

### Status overlay (live mode only)

Each node grows a colored left-edge stripe + status icon when an
instance is running or has finished:

| Engine state | Stripe color | Icon | Label |
|---|---|---|---|
| `pending` | gray | ⏳ | "Waiting" |
| `running` | blue, pulsing | 🔄 | "Running…" |
| `completed` | green | ✓ | "Done" |
| `failed` | red | ✗ | "Failed" |
| `skipped` | gray, hatched | ➖ | "Skipped" |

The label words come from a small dictionary so we never expose the
engine's internal enum to users. Pause / kill / retry actions on the
*instance* live on the footer bar, not on individual nodes.

---

## Edges — paths between steps

Each `Edge` in the definition renders as a curved line from the
source node's bottom anchor to the target node's top anchor. Three
flavors:

### Plain edge

A simple line. Most edges are this. No label.

### Conditional edge — Paths block

Conditional edges (`edge.condition` populated) get a **Paths** block
inserted as a synthetic node — *not* a real step, just a visual
"router":

```
                ┌──────────────┐
                │  ◇ If…       │
                └──┬────┬──────┘
                   │    │
   When score>0.8 ◀┘    └▶ Otherwise
        ┌────────┐         ┌────────┐
        │ Notify │         │ Ignore │
        └────────┘         └────────┘
```

The condition expression itself stays in the source YAML / API, but
the canvas renders the **labels** in plain language. A non-technical
user can read "When score>0.8" without needing to grok
`simpleeval` syntax. (We require designers to author a
`condition_label:` alongside `condition:`; absent, we fall back to
showing the raw expression.)

### Parallel fan-out

When a node has multiple outgoing plain edges (no conditions), they
render as parallel lines diverging downward — implicit "run all of
these in parallel". A small label "Both" or "All in parallel"
appears at the divergence point.

```
              ┌───────────────┐
              │  Triage email │
              └───┬───────┬───┘
                  │       │   (all in parallel)
         ┌────────▼┐    ┌─▼────────┐
         │ Apply   │    │  Record  │
         │ label   │    │  in DB   │
         └─────────┘    └──────────┘
```

---

## Layout

No hand-placement. Layout is computed every time the canvas opens or
the graph changes. Algorithm:

1. Topological sort the DAG → assign each node a **layer** (longest
   path from any root).
2. Within each layer, order nodes by source-edge order (stable).
3. Compute X positions to minimize edge crossings (simple barycenter
   pass; defer Sugiyama-full until graphs are big enough to need it).
4. Vertical spacing 120px, horizontal 240px (tuned by eye).

Pan + zoom-to-fit on load. Mini-map in the bottom right for graphs
that exceed the viewport.

Layouts up to **~30 nodes** should be readable without scrolling at
1080p. We don't currently have a workflow that big — the largest
example is the PDF classifier with ~5 nodes — but the algorithm is
sized for headroom.

---

## The Inspector pane

Clicking a node opens it in the right pane. Two modes:

### Edit mode

A form per step type. **No YAML, no JSON in the user's face.**

For a trigger:

```
Trigger
─────────────
Type:           [Gmail ▼]
Account:        [intelligent.workflow.engine@quentinspencer.com]
Poll interval:  [60 seconds]
Label filter:   [INBOX]

[Test trigger]
```

For a deterministic step:

```
Save the result
─────────────
Function:       [record_email_triage ▼]
                ↳ Records the triage decision into structured fields
                  for downstream querying.

Inputs:
  triage_from:  [steps.triage.output_text]
                  (auto-suggested from upstream step outputs)

[Advanced ⌄]   ← capability allowlist, retries, timeout
```

For an agentic step:

```
Decide the category
─────────────
Model:          [Claude Haiku 4.5 ▼]
                $1 / $5 per million tokens (in / out)

Instructions    (the agent reads this as its goal)
─────────────
┌─────────────────────────────────────────────────┐
│  You are triaging an inbound email…             │
│                                                 │
└─────────────────────────────────────────────────┘
                   [Open larger editor]

Tools the agent can use:
  ☑ email_send
  ☑ email_label_apply
  ☐ pdf_extract
  ☐ file_read
  …

[Advanced ⌄]   ← max_iterations, max_total_tokens, system_prompt override
```

The goal is that **a non-technical user can read every field on this
form and know what changing it does**. "Tools the agent can use"
becomes a checkbox list rather than `tools: [email_send,
email_label_apply]`. Model names use the human-readable form.

### View mode (live or post-run instance selected)

Same node, but the form is replaced with the last-run output. Output
cards (per `BUILD_PLAN.md` Phase 3 scope) — not raw JSON:

```
Decide the category — Done · 4.3s · 2,847 tokens · $0.014
─────────────
Category:        Urgent (confidence 0.92)
Summary:         Final notice from accounting — needs reply today.

Concerns flagged: (none)

[Show raw output]   ← collapsible JSON for power users
```

The View-mode card layout is **per-workflow**, driven by an optional
`output_renderer:` field on the step (small enum: `triage_card`,
`pr_card`, `paper_card`, `email_card`, default `raw_json`). The
existing triage examples have well-defined output shapes; we author
renderers for those and ship `raw_json` as the universal fallback.

---

## Schema additions

The canvas needs three small, additive fields on the existing
`WorkflowDefinition`. None break existing YAML; all default to None
and fall back to current behavior.

| Field | Where | Type | Used for |
|---|---|---|---|
| `label` | on every `Step` | `str \| None` | Friendly node title. Fallback: humanized function name / first sentence of `goal`. |
| `condition_label` | on `Edge` | `str \| None` | Plain-language render of the condition. Fallback: raw expression. |
| `output_renderer` | on every `Step` | `str \| None` | Which card template to use in View mode. Fallback: `raw_json`. |

These are purely **UI** fields — the engine ignores them. Adding them
to `definition.py` is a five-line change.

---

## Modes — Edit vs View

The mode toggle in the header swaps the canvas behavior:

| Mode | Selection target | Inspector shows | Edit affordances |
|---|---|---|---|
| **Edit** | Step (definition) | Form fields | Yes — drag from palette, click nodes, edit forms, save |
| **View** | Step execution (in an instance) | Output card | No — read-only; offers "Fork from here" + "Re-run" |

A **role gate** kicks in:

- Operator / Viewer / Auditor — locked to View mode. Edit toggle
  hidden.
- Designer / Admin — both modes available; defaults to View when
  arriving from an instance link, Edit when arriving from
  `/workflows/:id`.

This matches the existing RBAC and avoids two parallel access models.

---

## How an operator runs a workflow from the canvas

The "Run" button in the header opens a payload form (not a JSON
textbox). The form is generated from `trigger.example_payload`:

- Object → grouped section with one input per key.
- String → text input.
- Number → number input.
- Boolean → toggle.
- Array → "+ Add another" list.
- Nested object → indented subsection.

For email_triage the form would look like:

```
Run "Email Triage"
─────────────
From:       [newsletter@databricks-weekly.com]   [Databricks Weekly]
To:         [intelligent.workflow.engine@…]
Subject:    [This week in data]
Body:       [Here's what's new this week…                       ]
Received:   [2026-05-25 09:15 UTC]
Labels:     [INBOX] [+]

Run for:    ⦿ This payload     ◯ A batch — upload JSON / CSV

[Cancel]                                              [Run]
```

After clicking Run, the canvas flips to View mode automatically, the
new instance shows live in the footer, and node statuses animate as
the engine progresses.

The batch path uploads a JSON array or CSV (one row per fire),
hands it to a new backend bulk-fire endpoint, and the footer cycles
through instance ids. (Scoped in BUILD_PLAN Phase 3.)

---

## Implementation choices

### Library

**React Flow (`@xyflow/react`)** on a React frontend. See the
substrate decision above for why — and why the whole frontend
migrates Angular → React rather than embedding React Flow in an
Angular host (cross-framework embedding is operational pain that the
small codebase size makes unnecessary to suffer).

Deterministic layout comes from **dagre** (or **elk** if the graphs
warrant hierarchical edge routing) feeding node positions into React
Flow — that's "Layout A." React Flow's nested-flow primitives back
the sub-workflow rendering (epic E1); its documented Yjs integration
pattern backs collaboration (epic E2).

### State

- **Component / app state:** plain React state + a small Zustand store
  (React Flow already ships Zustand internally, so it's a free dep).
  No Redux.
- `definition` — the current edited `WorkflowDefinition`.
- `instance` — live `InstanceDetail` for View mode, fed by the
  WebSocket events hook (the port of the existing `EventsService`).
- `selectedNodeId` — drives the Inspector.
- Layout recomputes (memoized) whenever the definition changes.

### Component breakdown

```
<WorkflowCanvasPage>              (route component, holds state)
├── <CanvasHeader>                (name, mode toggle, Run, Save)
├── <StepPalette>                 (drag sources; Edit mode only)
├── <ReactFlow>                   (the canvas; pan / zoom / selection built-in)
│   ├── <TriggerNode>             (custom node types registered with React Flow)
│   ├── <DeterministicNode>
│   ├── <AgenticNode>
│   └── <PathsNode>
├── <Inspector>                   (right pane; routes to a per-type editor)
│   ├── <TriggerEditor>
│   ├── <DeterministicEditor>
│   ├── <AgenticEditor>
│   └── <OutputCard>              (View mode)
└── <CanvasFooter>                (instance status, fit-view)
```

Function components + hooks. Styling stays on the existing CSS
variable system (ported from `styles.scss`); no CSS-in-JS, no SCSS
modules.

### Backend changes

Minimal:

- Three additive YAML fields (above) on `WorkflowDefinition`,
  `Step`, `Edge`. Pure-Pydantic; no migrations.
- New bulk-fire endpoint: `POST /api/workflows/{id}/run-batch`
  accepting `{payloads: [...]}`; returns `{instance_ids: [...]}`.
  Wraps the existing `engine.run` in `asyncio.gather` with a
  configurable concurrency cap (default 5; same default as the
  current frontend batch loop).
- Optional `POST /api/workflows/{id}/validate` that runs the DAG
  validator (Kahn's, edge-target existence, capability shape) and
  returns structured findings — so the canvas can light up node
  borders red on save without a full server round-trip.

Everything else (definition CRUD, instance lifecycle, audit, events)
already exists.

---

## Layered rollout

A canvas is enough rope to hang a quarter on. To avoid that, the
build sequences as four shippable cuts:

| Cut | Scope | "Done" looks like |
|---|---|---|
| **C1 — Read-only canvas** | Render any existing workflow as nodes + edges. View mode only. Layout pass. Pan / zoom. Inspector shows form fields read-only. | A non-developer can open the email_triage workflow and read what every step does without seeing YAML. |
| **C2 — Live status overlay** | WebSocket-driven node state coloring + per-node output cards (one renderer per existing example workflow). Footer instance-status bar. | Operator can watch an instance progress through the canvas in real time and click any node to see its output. |
| **C3 — Run-from-form** | "Run" button → form generated from `example_payload`. Single-payload path; the existing JSON-paste dialog stays as a fallback. | Operator can fire an instance without ever opening a JSON editor. |
| **C4 — Edit mode** | Step palette, drag-to-insert, in-canvas connect / disconnect, Inspector form editors, Save → API write. | Designer can edit a workflow end-to-end in the canvas, no YAML. |

**C1 alone clears the demo-blocking issue** — a non-technical user
can be shown the system without YAML. C2 and C3 are what makes them
*operate* it. C4 is what closes the loop for designers.

Each cut is independently shippable. The current `/workflows/:id`
page (the dev console) stays put through all four — the canvas
lives at a new route (`/canvas/:id` or `/automations/:id`,
TBD).

---

## What this is not

- **Not the generative UI** (`docs/GENERATIVE_UI.md`). That is an
  LLM-driven canvas where the user describes the interface in
  natural language; this is a hand-designed, fixed UI.
- **Not a free-form diagram editor.** Nodes don't go where the user
  drags them; layout is computed. The user edits *structure*; the
  computer handles *position*.
- **Not a sub-workflow / nested-graph viewer *in the early cuts*.**
  C1–C4 render one workflow per canvas, no drill-in. Nested
  sub-workflows are a planned capability (epic E1) but require new
  schema + engine support first; they land after the single-workflow
  surface proves out.
- **Not a conversational front-end.** Conversation is the long-term
  Goal 4 rung; the canvas is the shorter-term Goal 4 rung. The two
  surfaces will eventually coexist — the canvas as the
  "show your work" view that backs the conversation.

---

## Open questions

1. **Agentic-step `goal:` editing for non-technical users.** The
   goal is a free-text prompt. A non-technical user can read it,
   but can they *edit* it without breaking the agent? Options:
   leave it freeform with a warning banner, gate behind Designer
   role, or build a structured "goal wizard" (purpose / output
   shape / constraints) on top. **Probably:** Designer role only
   for now, freeform editor. Revisit when a non-technical user
   demands it.
2. **Tool / capability surfacing.** "Tools the agent can use" as a
   checkbox list is honest but long (the platform ships ~12 tools).
   Do we group? Hide tools the workflow can't use given upstream
   capabilities? **Probably:** group by domain (Files, Email, PDF,
   HTTP, Database, Human), and grey out anything the workflow's
   capability allowlist excludes.
3. **Inline JSON output fallback.** When `output_renderer` is
   `raw_json`, do we show pretty-printed JSON, a tree view, or both
   behind a toggle? **Probably:** pretty-printed by default, tree
   view behind a toggle. Lift the existing instance-detail
   audit-log JSON-rendering helper.
4. **Save-and-version semantics in Edit mode.** Today,
   `POST /api/workflows/import` upserts by id. If two designers
   edit the same workflow in two tabs, the second save wins
   silently. **Probably:** add `If-Match: <etag>` on PUT and
   surface a "this workflow changed since you opened it — reload"
   banner. Out of scope for C1–C3.
5. **Layout stability across edits.** The auto-layout pass can
   reshuffle the whole graph when a node is inserted. **Probably:**
   anchor the layout by topological depth + sibling order, so
   inserting a step between two layers preserves left-right
   positions of unrelated branches. Same algorithm as today, but
   with stable id-based sort within layers.
6. **What about the YAML view?** Power users will want to read /
   diff YAML even after the canvas exists. **Probably:** keep a
   "View YAML" link in the Edit-mode header that opens a read-only
   YAML drawer; round-trip editing in YAML stays at
   `/workflows/:id/yaml`.

---

## Success criteria

The canvas is working when:

1. **Demo-able**: an operator unfamiliar with the codebase can be
   shown the email_triage workflow on the canvas and describe in
   their own words what it does, without coaching, within 60
   seconds of seeing the screen.
2. **Operable**: that operator can run the workflow for one email
   they care about, watch it progress live, and see the result —
   without ever opening a JSON editor or YAML file.
3. **Authorable**: a designer can take an existing workflow,
   change the model, add a new deterministic step at the end, save,
   and run the modified workflow — without leaving the canvas.
4. **No regressions**: the existing `/workflows`, `/instances`,
   `/cost` dev-console routes work unchanged. The canvas is a
   parallel surface, not a replacement.

Criterion 1 is achievable with C1 alone. Criteria 2 and 3 require
C2 + C3 and C4 respectively.

---

## Cost and dependencies

- **Frontend framework migration Angular → React.** One-time cost,
  ~2.2k LOC source + ~830 LOC tests. Cheapest now while the app is
  small. New runtime deps: `react`, `react-dom`, `react-router`,
  `@xyflow/react`, `dagre` (layout). Tests stay on Vitest
  (`@testing-library/react` replaces the Angular test helpers).
- **No new backend dependencies for C1–C4.** The canvas schema
  additions (`label`, `condition_label`, `output_renderer`) are
  pure-Pydantic. Backend epics E1 (sub-workflows) and E2 (collab)
  carry their own dependencies and get their own design passes.
- **No LLM dependency in the canvas C1–C4.** The agent runs are the
  same agent runs the engine already does; the canvas just
  visualizes and triggers them. Layout B (epic E3) is the only
  LLM-dependent piece, sequenced last.
- **No infra change for C1–C4.** Same FastAPI + Postgres backend;
  the served frontend is now a React bundle instead of an Angular
  one. Epic E2 (collab) later adds a Yjs sync server.

This is a frontend-heavy build with a small near-term backend
footprint; the larger backend epics (E1/E2/E3) follow once the canvas
surface proves itself. Matches the broader Phase 3 shape in
`BUILD_PLAN.md`.
