# Automations / Workflows IA Rework — Design

Status: **proposed** (drafted 2026-07-26; design-reviewed same day:
adopt-with-conditions, all findings folded in below; not yet built). Resolves
the open IA question flagged 2026-06-06 and carried in the auto-memory
("Automations vs Workflows distinction needs rework"). Builds on the C5
"friendly shell" intent (`docs/CANVAS_ROADMAP.md`); lands on the fresh
React 19 / react-router 8 foundation laid for exactly this purpose.

## 1. Problem, with evidence from the current code

Two pages list the same entity (workflow definitions) under two synonymous
names, split capabilities arbitrarily, and disagree about what exists:

1. **`/` "Automations"** — card grid, latest-run state, create / describe /
   clone / delete. **Filters out any definition whose id matches a bundled
   template** (`AutomationsHome.refresh`).
2. **`/workflows` "Workflows"** (Developer toggle) — table, instance
   counts, **Run / run-batch dialog, Import** — affordances the friendly
   page doesn't have. Shows everything.

The filtering rule has aged from "questionable" to **actively wrong**: the
platform's production workloads — `email-triage-apply` (live, mutating,
labeling the operator's mailbox right now) and `dmarc-ingest` — are bundled
examples by id, so they are **invisible on "Your automations"**. The page
whose one job is "is my stuff working?" cannot show the stuff that's
working. Meanwhile:

- "Automations" and "Workflows" are synonyms; nothing in the UI explains
  the split (the split is really *friendly vs developer*, but it's
  expressed as two different-looking lists of the same objects).
- Run-with-payload lives only on the developer page; a non-technical user
  on the home cannot manually fire an automation (Run exists on the canvas,
  one click deeper).
- **Org and owner are invisible everywhere except Users admin**, despite
  S2/S3 making them enforced, first-class facts.
- "Instances" is engine jargon on a nav meant for non-technical users.

## 2. Design principle

**One entity, one list, two renderings.** The friendly/developer split the
C5 shell wanted is real — but it is a difference in *density and
affordances*, not in *which objects exist*. Pages should differ by entity
(automations vs runs vs templates), never by audience; audience is a view
mode within a page.

## 3. Options considered

- **(a) Rename only** (Workflows → "All workflows (advanced)"). Cheapest;
  leaves the wrong filter, the capability split, and two lists. Rejected.
- **(b) Two pages on a real axis** (operate vs author: Automations =
  health/runs, Workflows = catalog/editing). A defensible split, but it
  puts *the same cards* on both pages with different chrome, and small
  deployments (ours) don't have enough objects to justify two views.
  Rejected — revisit if a fleet-health dashboard is ever pulled.
- **(c) Merge to one catalog; Developer toggle changes rendering, not
  existence — chosen.** Detailed below.

## 4. Target information architecture

| Nav item | Route | Contents |
|---|---|---|
| **Automations** | `/` | ALL workflow definitions — user-created *and* bundled — as cards (friendly) or table (Developer toggle on). Create / Describe it / Import / Run / clone-from-template entry points. |
| **Runs** | `/runs` (alias `/instances` kept — bookmarks, WS deep links) | The instances list, renamed in nav + headings. Unchanged mechanics. |
| **Templates** | `/templates` | Unchanged: bundled starting points, "Use this template" clones. A template being *cloneable* is orthogonal to its bundled instance *running*. |
| *(Developer)* Cost | `/cost` | Unchanged. |
| *(Admin)* Users | `/users` | Unchanged (gains nothing here; already org-aware). |

**The Automations page, merged:**

- **No template-id filtering.** Every definition appears. Bundled ones
  carry a `Bundled` badge (id ∈ template ids — same derivation as today,
  just displayed instead of used to hide).
- **Cards** (default): name, description, trigger-type chip, latest-run
  state (exists today), run count, `Bundled` badge, and owner attribution
  when present. **Org badges render for Administrators only** (the
  design-review corrected an earlier ">1 org exists" rule: non-admins
  cannot list orgs — 403 for Org Users/Viewers, own-org-only for Org
  Admins — and their workflow list is org-scoped anyway, so every row
  they see shares their org; the badge only carries information for the
  unscoped view). Click → canvas.
- **Table** (Developer toggle): the current WorkflowsList columns (id,
  steps, instance-count link) as a denser rendering of the *same* list —
  same data, same filters, same actions.
- **Actions move up from the dev page:** Run (single + batch dialog,
  exists in WorkflowsList) and Import join Create/Describe — all
  role-gated by the `hasRole` write set. (Note: this *tightens* today's
  dev page, which shows Run/Import buttons unconditionally and only
  mentions roles in dialog copy; the merged page hides them from viewers
  outright — gating that is now session-true and browser-tested.)
- **Delete** keeps its current card affordance + confirm; in table mode a
  Delete action per row (role-gated identically).

**Removed:** the `/workflows` page and nav item. The route redirects to `/`
(Developer-toggle table mode is its successor). `WorkflowsList.tsx` is
deleted, its Run/Import dialogs extracted to shared components — they are
the majority of its code and are reused as-is.

## 5. Backend touches (small — but NOT a field on the definition model)

- None strictly required for IA1: the client already derives bundled-ness
  from `GET /api/templates` and enriches with counts + latest state.
- **IA2's attribution needs a deliberate shape (design-review blocking
  finding).** The obvious move — adding `org_id`/`owner_user_id` to the
  list response — is a trap twice over: `response_model=
  list[WorkflowDefinition]` strips unknown keys, and putting the fields ON
  `WorkflowDefinition` would leak ownership into YAML export/import
  round-trips, contaminating the deliberate row-vs-model separation
  (`repository.py`: "Definitions' org lives on the row, not the
  YAML-shaped model"). Chosen shape: a sidecar endpoint mirroring the
  existing instance-counts pattern — `GET /api/workflows/attribution` →
  `{workflow_id: {org_id, owner_user_id}}`, org-scoped like every list,
  called by the home alongside counts. Zero response-model churn, zero
  YAML contamination, one extra cheap request on one page.

## 6. What this deliberately does not do

- No fleet-health dashboard (option b's operate view) — the enriched cards
  cover "is it working?" at current scale.
- No change to Templates' role or the canvas.
- Export stays API-only (`GET /api/workflows/{id}/export`) — it was never
  on the dev page; no UI home claimed or needed.
- No new pages for G11's future attention surface — but the merged home is
  its natural landing (a "needs attention" strip above the cards), and the
  merge removes the ambiguity about *where* such a strip would live.
- No route-level breaking changes: `/instances` and `/workflows` keep
  working (alias + redirect).

## 7. Cut plan

| Cut | Contents | Size |
|---|---|---|
| **IA1** | Merge: unfilter the home, move Run/Import dialogs into it, table rendering behind the Developer toggle, delete `/workflows` page + redirect, nav rename Instances→Runs (+`/runs` alias). Vitest for: unfiltered list w/ bundled badge, run-dialog reachable from home, redirect, viewer sees no write affordances (extend existing). e2e: home shows a bundled workflow; run dialog opens from home. | M |
| **IA2** | Attribution: backend `org_id`/`owner_user_id` on the workflows list response; org badge (>1 org) + owner on cards/table. Backend test + Vitest. | S |
| **IA3** | Polish pass on the merged page (empty states, card/table transition, a11y sweep with axe on both renderings). | S |

IA1 is the substance; IA2/IA3 can ride the same PR or follow. Estimated
total: ~1 day.

## 8. Risks / open questions for review

- **Delete on bundled examples** is already possible today from the dev
  page and re-seeds on restart (documented quirk). Surfacing bundled cards
  on the home makes that quirk more visible — the confirm dialog should
  say "bundled examples reappear on restart" for those ids.
- **Card grid scale**: fine at ≤ dozens of definitions; the table mode is
  the pressure valve. No pagination in this cut.
- **Naming**: is "Runs" the right friendly term for instances? (Zapier uses
  "Zap history"/"Runs"; n8n uses "Executions".) Recommend "Runs".
- **One-click Run on mutating workflows (design-review finding):**
  unfiltering the home puts a Run button on `email-triage-apply` — a live
  mailbox mutation — one click from the friendly page. Mitigation in IA1:
  the Run dialog already requires composing a payload before firing
  (never truly one-click), and it gains a warning line when the target
  workflow's steps hold any connector-backed tool ("this workflow acts on
  external systems"), derived from the definition's step tool lists. A
  full capability-aware confirm ties into C6 surfaces later.
- Keep `/instances/:id` deep-link routes explicitly (WS + batch-result
  links), not just the bare `/instances` alias.
