# Canvas Roadmap — Prioritized

**Extends:** `docs/WORKFLOW_CANVAS.md` cut/epic nomenclature (C1–C4 shipped; E1–E3 epics).
**Companion to:** `docs/VISION.md` Goal 4 (progressive disclosure) and `docs/BUILD_PLAN.md` Phase 3.
**Status of inputs:** C1 read-only → C2 live status → C3 run-from-form → C4 edit + Tier 1 polish are
**all shipped.** This roadmap picks up at **C5** and sequences the remaining GUI gaps into shippable
cuts. It was derived from a competitive GUI gap analysis against Airtable AI, Amazon Quick Suite,
Union.ai, Tines, Gumloop, and Zapier.

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

1. **C5 first (reframe + cold-start).** The canvas is built but the app is framed as a dev console.
   Highest credibility-per-effort: mostly rearranging surfaces we already have + surfacing the 10
   example workflows we already ship as templates.
2. **C6 second (the trust wedge).** Surface the four assets *no competitor's GUI shows* —
   replay/dry-run, per-tool-call audit, capability boundaries, cost/budget. This is where we win
   demos. Do it **before** chasing feature parity, not after.
3. **C7 third (authoring parity).** The universal features every competitor has and we lack — NL
   scaffolding, connector picker, build-time validation.
4. **C8 fourth (operability + polish).** Batch, safer goal editing, a11y/responsive.
5. **Epics (E1–E5) deferred** — each needs its own design pass; pull in when the single-user surface
   proves out or a workload demands.

A cheap-and-early exception: two C6 items (cost estimate, capability viz) are small and high-impact —
they can be **pulled forward into C5** if there's appetite, since the backend data already exists.

---

## Dependency map

```
C5  Friendly shell & cold-start  ──┐
                                   ├──►  C6  Trust wedge  ──►  C7  Authoring parity  ──►  C8  Polish
 (no backend deps beyond          │      (dry-run + catalog        (scaffold + catalog       (batch +
  create-empty + templates list)  │       endpoints)                + validate endpoints)     a11y)
                                   │
   pull-forward candidates: B4 cost estimate, B3 capability viz ─┘

Epics, independent design passes, parallel-able once C5+C6 land:
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

## C6 — The trust wedge (differentiators)

**North star for the cut:** the canvas *shows* the governance no competitor's GUI shows. Every item
here is a demo a Zapier/Airtable/Gumloop GUI cannot reproduce.

| Item | Scope | Effort | Backend? |
|---|---|---|---|
| **C6.1 Test / dry-run** | A "Test" button that runs the workflow in replay/mock-world mode (no real systems touched) and shows the result inline. Serves the VISION "sandbox-first" anti-goal. *Only we can do this — we're the only product with replay determinism.* | L | `POST /api/workflows/{id}/dry-run` (MockWorld + replay Bedrock) |
| **C6.2 Cost estimate + live budget meter** | Pre-run estimate in the Run dialog ("~$0.003/run at Haiku 4.5"); live budget meter in the footer during a run; surface `budget_action` state. Brings the Cost route's data to where decisions happen. | M | Estimate can be client-side from the pricing table; live meter reuses existing per-step cost on step output |
| **C6.3 Capability boundaries visible** | On an agent node, show what it *can* use and what's greyed out, with the reason ("restricted by workflow capability allowlist"). Turns an invisible safety property into a visible selling point. | M | `GET /api/workflows/{id}/capabilities` (compute the per-step intersection server-side) |
| **C6.4 Explain-this-run** | Click a node in a finished run → forensic view: what the agent saw, which tools it called with what args, why, and the `memory_hash` in effect. Reframes our per-tool-call audit log as a trust surface (VISION Goal 6). | M | Mostly existing audit/step data; may add a per-step audit-slice endpoint |

**Pull-forward note:** C6.2 and C6.3 are the cheapest here and the data already exists — consider
shipping them inside C5 to make the reframe land harder.

**Exit criterion:** in a demo, we click "Test," show a run that touched nothing real, then click a
node and show exactly what the agent was allowed to do, did, and cost — and the audience understands
no other tool in the eval can show them that.

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

## If we only do one thing

**C5.** The canvas is already built; the product just doesn't lead with it. Reframing the app around
the friendly surface + surfacing the 10 templates we already ship is the single highest-leverage move
— it converts existing, shipped work into a credible non-technical product. Everything else compounds
on top of a correct frame.

## If we only do two things

**C5 + C6.2/C6.3 pulled forward.** Reframe, then show cost-per-run and per-agent capability boundaries
on the canvas. Both are cheap (data exists), and together they make the reframe land as "a governed,
cost-aware automation tool" rather than "a workflow viewer" — which is exactly the position no
competitor's GUI occupies.

---

## At-a-glance

| Cut | Theme | Items | Net-new backend |
|---|---|---|---|
| **C5** | Friendly shell & cold-start | Automations home, templates gallery, create-blank | `GET /api/templates`, `POST /api/workflows` |
| **C6** | The trust wedge | Dry-run, cost/budget meter, capability viz, explain-this-run | `POST .../dry-run`, `GET .../capabilities` |
| **C7** | Authoring parity | NL scaffold, connector picker, validation, safer goals | `POST .../scaffold`, `GET /api/catalog`, `POST .../validate` |
| **C8** | Operability & polish | Batch run, polish/a11y/responsive | `POST .../run-batch` |
| **E1–E5** | Deferred epics | Sub-workflows, collab, AI-layout-B, delivery, version history | (per-epic design passes) |
