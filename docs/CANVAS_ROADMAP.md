# Canvas Roadmap — Prioritized

**Extends:** `docs/WORKFLOW_CANVAS.md` cut/epic nomenclature (C1–C4 shipped; E1–E3 epics).
**Companion to:** `docs/VISION.md` Goal 4 (progressive disclosure) and `docs/BUILD_PLAN.md` Phase 3.
**Status of inputs:** C1 read-only → C2 live status → C3 run-from-form → C4 edit + Tier 1 polish →
**C5 friendly shell** are **all shipped.** **C6 (the trust wedge) is the next build — scoped in
detail below.** C7–C8 remain sketches until their turn; E1–E5 stay deferred. Derived from a
competitive GUI gap analysis against Airtable AI, Amazon Quick Suite, Union.ai, Tines, Gumloop, and
Zapier (`gui-gap-analysis.md` §6 closing sequence → these cuts).

> **Naming.** Frontend cuts continue the `C#` series. Backend epics continue the `E#` series. Each
> cut is independently shippable and demo-able. Effort is S / M / L (rough, single-dev). "Backend"
> flags new server work; everything else is frontend surfacing of capabilities that already exist.

---

## North star

> An operator who has never seen the codebase opens the app, picks a template (or describes what they
> want), watches it run *safely in a sandbox*, sees exactly what each AI step is allowed to do and
> what it cost — and ships it. Without ever seeing YAML, a UUID, or a JSON dump.

C1–C4 got us to "a developer can edit a workflow on a canvas." C5–C8 get us to that north star.

---

## The sequencing thesis (why this order)

1. **C5 first (reframe + cold-start).** ✅ Shipped. The canvas was built but the app was framed as a
   dev console; C5 reframed it around the friendly surface + surfaced the 10 examples as templates.
2. **C6 second (the trust wedge).** ▶ Next. Surface the four assets *no competitor's GUI shows* —
   cost/budget, capability boundaries, per-tool-call audit, replay/dry-run. This is where we win
   demos. Do it **before** chasing feature parity, not after. Build order (cheapest-first):
   C6.2 cost → C6.3 capability → C6.4 explain → C6.1 dry-run (detailed scope below).
3. **C7 third (authoring parity).** The universal features every competitor has and we lack — NL
   scaffolding, connector picker, build-time validation.
4. **C8 fourth (operability + polish).** Batch, safer goal editing, a11y/responsive.
5. **Epics (E1–E5) deferred** — each needs its own design pass; pull in when the single-user surface
   proves out or a workload demands.

Within C6, **cost estimate and capability viz come first** — small, high-impact, and the backend
data already exists, so they convert the C5 reframe into "a governed, cost-aware tool" fastest.

---

## Dependency map

```
C5 ✅ Friendly shell  ──►  C6 ▶ Trust wedge  ──►  C7 Authoring parity  ──►  C8 Polish
   (shipped)                cost → capability      (scaffold + catalog      (batch +
                            → explain → dry-run     + validate endpoints)    a11y)

Epics, independent design passes, parallel-able once C6 lands:
   E1 sub-workflows · E2 collaboration · E3 AI-layout-B · E4 delivery surfaces · E5 version history
```

---

## C5 — Friendly shell & cold-start  ✅ shipped (2026-05-31)

**North star for the cut:** a non-technical user lands on something they understand and can create a
workflow without pasting YAML.

| Item | Status | Scope | Effort | Backend? |
|---|---|---|---|---|
| **C5.1 Automations home** | ✅ | New default route (`/`): card grid of workflows (name, latest-run status pill, step count, run count). Instances/Workflows/Cost demoted behind a "Developer" toggle (`lib/advanced.ts`, localStorage-persisted). | M | No |
| **C5.2 Templates gallery** | ✅ | `/templates` — seeded from the 10 bundled example workflows. "Use this template" clones into a new editable workflow and opens the canvas in edit mode. | M | `GET /api/templates` (summaries) |
| **C5.3 Create blank** | ✅ | "Create" on the home → names + creates a blank manual-trigger workflow → canvas opens in edit mode (`?edit=1`). | S | `POST /api/workflows` (blank or `{template_id}` clone) |

**Exit criterion (met):** a non-developer opens the app, picks the email-triage template (or starts
blank), and lands on the canvas ready to edit — no import dialog, no YAML. Role-gated to
Designer/Admin (viewers get the read-only home). 16 new tests (8 backend, 8 frontend).

> **Implementation notes.** `POST /api/workflows` handles both blank and clone (one endpoint, the
> roadmap's two net-new backend items collapsed into create + `GET /api/templates`). New id is
> slugified from the name and de-duplicated. Templates are discovered by walking the definitions
> dir for `workflow*.y*ml`; malformed files are skipped. The dev console remains fully routable —
> the toggle only governs nav visibility, so deep links to `/workflows` etc. still work.

---

## C6 — The trust wedge (differentiators) ▶ NEXT BUILD

**North star for the cut:** the canvas *shows* the governance no competitor's GUI shows. Every item
here is a demo a Zapier/Airtable/Gumloop GUI cannot reproduce.

| Item | One-liner | Effort | Net-new backend |
|---|---|---|---|
| **C6.2 Cost estimate + live budget meter** ✅ | Per-run cost estimate in the Run dialog + a live token/$ meter during a run, against the budget. | S–M | `GET /api/workflows/{id}/cost-estimate` (thin; data exists) |
| **C6.3 Capability boundaries visible** ✅ | On an agent node, show the tools it *can* use vs greyed-out, with the reason. | M | `GET /api/workflows/{id}/capabilities` |
| **C6.4 Explain-this-run** ✅ | Click a node in a finished run → forensic view of what the agent saw, called, and cost. | M | `GET /api/workflow-instances/{id}/steps/{step_id}/explain` |
| **C6.1 Test / dry-run** | A "Test" button that runs side-effect-free (MockWorld) so no real systems are touched. | L | `POST /api/workflows/{id}/dry-run` |

### Build order (cheapest-first; each independently shippable + demo-able)

Sequenced to front-load the highest credibility-per-effort. **C6.2 → C6.3 → C6.4 → C6.1.** The
first three are mostly *surfacing data the backend already produces*; C6.1 is the one with real new
runtime behavior and an open design question, so it lands last.

> Principle reminder: "backend mostly exists" for C6.2–C6.4 — the work is endpoints that shape
> existing data + frontend surfacing. Keep new endpoints read-only and dev/role-gated like the rest.

---

#### C6.2 — Cost estimate + live budget meter  ✅ *(shipped)*

- **Already built:** `cost/pricing.py` (per-model $/1M rates), per-step `cost_usd` + `model` on step
  output, `WorkflowContext.total_tokens`/`total_cost_usd`, `CostReportService.by_workflow`,
  `WorkflowPolicy.max_total_tokens` + `budget_action`, and the live `/ws/events` stream.
- **New backend:** `GET /api/workflows/{id}/cost-estimate` → `{ models: [{step_id, model, in_rate,
  out_rate}], avg_cost_usd, avg_tokens, run_count, max_total_tokens, budget_action }`. `avg_*` from
  `by_workflow` over prior runs (null when `run_count == 0` — show model rates only, no fake number).
- **New frontend:** (a) Run dialog shows "~$X/run (avg of N runs)" or, with no history, the per-step
  models + their rates. (b) A footer **budget meter** during a run: `tokens / max_total_tokens` bar +
  running $, fed by the WS step events the footer already consumes; turns `--warn` past ~80% and
  `--err` at the cap, with the `budget_action` (notify/pause/escalate) shown. Use `tabular-nums`
  (see `docs/UI_POLISH_AND_A11Y.md`).
- **Acceptance:** open the Run dialog on a workflow with prior runs → see an estimate; start a run →
  the footer meter climbs live and changes colour approaching the cap; a workflow with
  `budget_action: pause` visibly pauses at the cap.

#### C6.3 — Capability boundaries visible  ✅ *(shipped)*

- **Already built:** the capability model + intersection (system → workflow → step → runtime, most
  restrictive wins) enforced in agent dispatch; the tool catalog.
- **New backend:** `GET /api/workflows/{id}/capabilities` → per agentic step: `{ step_id, allowed:
  [tool…], denied: [{tool, reason}] }` where `reason` names the narrowing layer (e.g. "not in the
  workflow capability allowlist"). Compute via the same intersection the engine uses — do not
  duplicate the logic; call into it.
- **New frontend:** on an agent node (and/or its inspector), list allowed tools and render the
  denied ones greyed with the reason on hover. A small shield/lock affordance summarising "can use N
  of M tools."
- **Acceptance:** a step whose `tools:` list is narrower than the catalog shows the extras greyed
  with a correct reason; widening the allowlist and reloading moves a tool from denied → allowed.

#### C6.4 — Explain-this-run  ✅ *(shipped)*

- **Already built:** immutable per-tool-call audit log, `AgentResult` (full conversation + per-call
  tool log), `memory_hash` on agent step output, per-step usage/cost, the EventBus.
- **New backend:** `GET /api/workflow-instances/{id}/steps/{step_id}/explain` → `{ system_prompt_excerpt,
  memory_hash, iterations, usage, cost_usd, tool_calls: [{name, args, result_excerpt, ts}], output }`,
  assembled from the step execution + audit slice for that step. Excerpt/truncate large blobs.
- **New frontend:** clicking a node in a **finished** instance opens a drawer/panel: the tool-call
  timeline (name + args + result), tokens/cost/iterations, and the `memory_hash` in effect — framed
  as "what this agent saw and did." Reuses the node-click selection the canvas already has.
- **Acceptance:** after a real run, clicking an agent node shows its actual tool calls with arguments
  and the memory hash; a deterministic step shows its function + inputs/outputs.

#### C6.1 — Test / dry-run  *(L; do last)*

- **Already built:** `MockWorld` (side-effect-free filesystem/messaging/db), `BedrockMode`
  record/replay/live, the engine runs against any `World`.
- **New backend:** `POST /api/workflows/{id}/dry-run` → runs the definition once against `MockWorld`
  (no real file/email/http/connector side effects) and returns the instance result inline, tagged
  `dry_run: true` so it's distinguishable in history.
- **⚠ Open design decision (resolve before building):** what does Bedrock do during a dry-run?
  Options — **(a)** live Bedrock (the agent reasons for real; costs a little; "sandbox the world,
  keep the brain") — most useful default; **(b)** replay (zero cost, fully deterministic, but needs
  a recording per request — brittle for arbitrary workflows); **(c)** a stub responder. Recommend
  **(a)** as the default with a per-run toggle to (b) when recordings exist. This is the only C6 item
  that needs a real decision; the other three are pure surfacing.
- **New frontend:** a **Test** button on the canvas (next to Run) → runs dry → renders results in the
  existing output cards with a clear "sandbox — nothing real was touched" banner.
- **Acceptance:** clicking Test on the email-triage or PDF-classifier workflow produces a result with
  **no** real file written / email sent / connector call made (assert against MockWorld), labelled as
  a dry run.

**Exit criterion for C6:** in a demo we open the Run dialog and show the per-run cost; start a run
and watch the live budget meter; click an agent node to show exactly which tools it's *allowed* to
touch and, on a finished run, exactly what it *did* and what it cost; and click **Test** to run it
touching nothing real — and the audience understands no other tool in the eval can show them any of
that.

---

## C7 — Authoring parity

**North star for the cut:** close the universal features every competitor has and we entirely lack,
so we're not disqualified on a feature checklist.

| Item | Scope | Effort | Backend? |
|---|---|---|---|
| **C7.1 NL scaffold → editable draft** | A prompt box: describe the workflow → one bounded agentic call emits a draft `WorkflowDefinition` rendered on the canvas for human editing. **Scoped to one LLM call producing a draft the user must approve** — deliberately sidesteps the research-gated full conversational rung. | L | `POST /api/workflows/scaffold` (one agentic call over the function/connector catalog) |
| **C7.2 Connector / trigger picker** | Searchable catalog of functions + connectors with icons + one-line descriptions, grouped by domain (Files, Email, HTTP, Schedule, Webhook). Replaces the generic "+ Function / + AI step." | M | `GET /api/catalog` (functions + connectors + config schemas + descriptions) |
| **C7.3 Build-time validation** | Red node borders + inline messages on save/edit ("step has no inputs," "edge target missing"). Wraps the existing Kahn's-DAG + edge-target + capability-shape validator. | M | `POST /api/workflows/validate` (wraps existing validator) |
| **C7.4 Safer goal editing** | Reframe the agentic `goal` textarea: "you're editing the AI's instructions," inline help, examples. Defer the structured "goal wizard." | S | No |

**Exit criterion:** a designer types "triage inbound invoices and email me the urgent ones," gets a
draft on the canvas, picks a real connector from the picker, and the canvas flags the one step that's
misconfigured before they run it.

---

## C8 — Operability & polish

**North star for the cut:** remove the "rough v1" feel and round out day-to-day operation.

| Item | Scope | Effort | Backend? |
|---|---|---|---|
| **C8.1 Batch run from the GUI** | CSV/JSON upload in the Run dialog → fires N instances; footer cycles instance ids. Surfaces the batch pattern we've proven in shell scripts (50-paper, PR batches). | M | `POST /api/workflows/{id}/run-batch` (bounded `asyncio.gather`) |
| **C8.2 Polish / a11y / responsive** | Empty states, loading skeletons, focus management, ARIA on custom nodes, a responsive breakpoint. Design tokens (CSS vars) already exist. | M, continuous | No |

**Exit criterion:** an operator uploads a CSV of 50 rows and watches the batch run; a keyboard-only
user can navigate the canvas; the app doesn't look like a prototype.

---

## Deferred epics (own design passes)

Named so they're not silent gaps. Each is larger than a cut and parallel-able once C5+C6 land.

| Epic | What | Why deferred | Trigger to start |
|---|---|---|---|
| **E1 Sub-workflows** | A step that invokes another workflow; nested-graph drill-in. | Needs new schema + engine support (capability/budget/audit propagation across the boundary, cross-workflow cycle detection). | A workload needs reuse/modularity. |
| **E2 Real-time collaboration** | Yjs/CRDT sync server + presence; definitions become live collaborative docs. | Biggest single lift; not needed for single-user proof. | Multiple designers on one workflow. |
| **E3 AI-assisted layout (Layout B)** | LLM-suggested *structure* from intent. (Layout A — deterministic dagre — already shipped.) | Research-gated; overlaps the conversational rung and `docs/GENERATIVE_UI.md`. | C7.1 scaffold proves the intent→draft pattern. |
| **E4 Delivery surfaces** | Surface agents/results in Slack/Teams/Gmail (Gumloop's "co-worker" model). | Connector/notification concern, not a canvas concern. | A customer asks for in-channel delivery. |
| **E5 Version history / diff** | Visual version timeline + rollback. Minimum first step: concurrent-edit `If-Match` + "changed since you opened it" banner (canvas open-question #4). | Save currently upserts; the `If-Match` guard is the near-term floor, full timeline is later. | Two-tab edit collisions become real. |

---

## If we only do one thing (next)

**C6.2 (cost estimate + live budget meter).** C5 reframed the app around the friendly surface; the
single highest-leverage *next* move is to show cost-per-run and a live budget meter where decisions
happen. The data already exists, the lift is S–M, and it shifts the product's read from "a workflow
viewer" to "a governed, cost-aware automation tool" — the position no competitor's GUI occupies.

## If we only do two things

**C6.2 + C6.3.** Add per-agent capability boundaries on the canvas. Together with the cost meter,
they make the two governance assets competitors can't match — *what each agent costs* and *what each
agent is allowed to touch* — visible on the authoring surface. Both are mostly surfacing existing
backend data.

---

## At-a-glance

| Cut | Theme | Items | Net-new backend |
|---|---|---|---|
| **C5** ✅ | Friendly shell & cold-start | Automations home, templates gallery, create-blank | `GET /api/templates`, `POST /api/workflows` |
| **C6** ▶ next | The trust wedge | cost/budget meter, capability viz, explain-this-run, dry-run (build in that order) | `GET .../cost-estimate`, `GET .../capabilities`, `GET .../steps/{id}/explain`, `POST .../dry-run` |
| **C7** | Authoring parity | NL scaffold, connector picker, validation, safer goals | `POST .../scaffold`, `GET /api/catalog`, `POST .../validate` |
| **C8** | Operability & polish | Batch run, polish/a11y/responsive | `POST .../run-batch` |
| **E1–E5** | Deferred epics | Sub-workflows, collab, AI-layout-B, delivery, version history | (per-epic design passes) |
