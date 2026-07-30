# Automations / Workflows IA Rework — Design

Status: **BUILT 2026-07-30** (design 2026-07-26, two reviews folded; built
in two commits — backend sidecar + Tool.effect, then the frontend merge).
Shipped: the merged catalog at `/` (cards/table via `?view=`, localStorage
default), `/workflows` → `/?view=table` redirect, `/runs` canonical +
`/instances` redirect (detail routes untouched), attribution sidecar with
server-resolved display names + bundled/lifecycle metadata + run-effect
classification (`Tool.effect` across all 20 stock tools; unknown counts as
mutating), Run/Import dialogs extracted per the §4b matrix, delete
disabled for bundled rows, effect warning + explicit confirm in the Run
dialog, org badges Administrator-only, §6 partial-failure contract in a
shared `useCatalog` hook. 193 frontend + 849 backend tests; 3 new e2e
specs (same-ids-both-renderings, redirects, mutating-run confirm) green
against a live backend.

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
affordances*, not in *which objects exist*. Pages differ by entity
(automations vs runs vs templates), never by audience; audience is a view
mode within a page. The two renderings show **exactly the same set of
workflow ids** (test-pinned); they differ in density and in which actions
get visual prominence (§4b).

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

### 4a. Pages and routes (canonical vs compatibility, stated exactly)

| Nav item | Canonical route | Compatibility | Contents |
|---|---|---|---|
| **Automations** | `/` (view mode URL-addressable: `/?view=cards` \| `/?view=table`) | `/workflows` → redirect to `/?view=table` | ALL definitions — user-created *and* bundled — one list, two renderings. |
| **Runs** | `/runs` (list) | `/instances` → redirect to `/runs`. **`/instances/:id` stays canonical for detail in this cut** — WS deep links, batch-result links, compare links all point there; a `/runs/:id` alias may come later, never the reverse. | The instances list, renamed in nav + headings. Unchanged mechanics. |
| **Templates** | `/templates` | — | Unchanged: bundled starting points, "Use this template" clones. Cloneability is orthogonal to a bundled instance *running*. |
| *(Developer)* Cost | `/cost` | — | Unchanged. |
| *(Admin)* Users | `/users` | — | Unchanged. |

View mode: the `?view=` param is the source of truth (history-correct,
shareable, deterministic in tests); the Developer toggle writes it. Last
choice persists via localStorage as a *default* when the param is absent.

### 4b. Action matrix (which affordances, which rendering)

| Action | Friendly cards | Developer table |
|---|---|---|
| Open canvas | ✓ (card click) | ✓ (name link) |
| Run (single) | ✓ | ✓ |
| Run (batch) | behind an "Advanced" affordance in the Run dialog (existing tab) | ✓ |
| Create / Describe it | ✓ | ✓ |
| Import | — (table/Developer affordance) | ✓ |
| Delete | overflow/hover affordance (as today), **user-created only** | row action, **user-created only** (§4d) |
| Clone from template | ✓ (via Templates) | ✓ |

All write actions are gated by the `hasRole` write set (viewers see none —
session-true and browser-tested). **UI gating is presentation only**: every
mutation endpoint independently 403s (S2 test-pinned); nothing here relies
on hidden buttons.

Per-action permissions (run vs edit vs import as distinct grants) stay
deferred — that is ROLES_PLAN §8's existing "run-vs-edit split inside
Organization User" deferral, trigger already named there. Not re-decided
here.

### 4c. The merged Automations page

- **No template-id filtering.** Every definition appears; bundled ones
  carry a `Bundled` badge.
- **Cards** (default): name, description, trigger-type chip, latest-run
  state, run count, `Bundled` badge, owner attribution when available.
  **Org badges render for Administrators only** (internal-review
  correction: non-admins cannot list orgs — 403 for Org Users/Viewers,
  own-org-only for Org Admins — and their workflow list is org-scoped
  anyway; the badge only carries information for the unscoped view).
- **Table** (Developer toggle / `?view=table`): the current WorkflowsList
  columns (id, steps, instance-count link) as a denser rendering of the
  same list.
- `WorkflowsList.tsx` is deleted; its Run/Import dialogs are extracted to
  shared components and reused as-is.

### 4d. Bundled-workflow semantics (external-review upgrade)

Bundled-ness stops being a client-side id coincidence and becomes
**authoritative row metadata in the attribution sidecar** (§5): `source:
user | bundled` computed server-side from the same templates dir the
orchestrator seeds (one source of truth instead of two derivations), plus
`lifecycle: reseeded` for bundled rows.

**Delete is disabled for bundled rows in both renderings** (tooltip:
"Bundled example — managed by the examples directory; it re-seeds on
restart"). This replaces the warn-but-allow quirk: deleting a bundled
definition was always a footgun (cascades run history, then the definition
returns on restart). Clearing a bundled workflow's *run history* remains
available (instance deletion, unchanged). A persistent "disable a bundled
workflow" (stop its trigger across restarts) is a real feature with
orchestrator implications — deferred, trigger: first time the operator
actually wants a bundled example off without editing the YAML.

### 4e. Run-dialog effect warning (external-review upgrade)

The internal review's "warn when any step holds a connector-backed tool"
was too heuristic (connector tools may be read-only; local tools like
`file_write` mutate; custom tools are unknown). Replace with a
conservative three-way classification:

- `Tool` gains an `effect: ClassVar` — `"read_only" | "mutating"` — set on
  every stock tool (`pdf_extract`/`file_read`/connector-query =
  read_only; `file_write`/`email_send`/`email_label_apply`/connector-send
  = mutating). Absent (third-party tools) = **unknown**.
- A workflow's run-effect = worst over its steps' tools; **unknown counts
  as mutating**.
- The Run dialog states the classification and the tool names ("acts on
  external systems via: email_label_apply__…"); mutating/unknown targets
  require an explicit confirmation checkbox after the payload is composed.
  Read-only workflows show no warning (a false destructive warning erodes
  the real ones).
- This is deliberately C6-adjacent: the same classification can later
  surface on the capabilities panel.

## 5. Backend touches (small — but NOT fields on the definition model)

- **The attribution/metadata sidecar** (both reviews converged here; the
  internal one caught the trap, the external one caught that the cut plan
  still contradicted it): `GET /api/workflows/attribution` →

  ```json
  { "<workflow_id>": {
      "org_id": "default", "org_name": "default",
      "owner_user_id": "…", "owner_display_name": "Quentin",
      "source": "bundled", "lifecycle": "reseeded" } }
  ```

  Org-scoped like every list; **display names resolved server-side**
  (non-admins cannot call `/api/users`, so a raw `owner_user_id` would be
  undisplayable exactly where it's most needed); fields the requester may
  not see are omitted, never nulled-and-leaked. Mirrors the
  instance-counts pattern: zero `response_model` churn on the workflows
  list, zero ownership leakage into YAML export/import (the row-vs-model
  separation stands — `repository.py`: "Definitions' org lives on the
  row, not the YAML-shaped model").
- The `Tool.effect` classification (§4e) — additive ClassVar + catalog
  exposure.

## 6. Data loading & partial failure (external-review addition)

The merged page joins five sources: definitions, templates, counts,
latest-run, attribution. Behavior is specified, not emergent:

- **Definitions failing fails the page** (error state, as today).
- Every enrichment degrades independently: attribution failure hides
  owner/org/bundled decoration; counts failure renders "—" (never a
  misleading 0); latest-run failure renders no state chip (never
  "has-never-run").
- Joins are by workflow id; sort is deterministic (name, then id).
- All requests use the ignore-flag pattern (cancel-on-navigate), as the
  codebase already does everywhere.
- At current scale parallel client-side joins are fine; a dedicated
  catalog-view endpoint is the noted escape hatch if the object count ever
  makes this chatty.

## 7. What this deliberately does not do

- No fleet-health dashboard (option b's operate view).
- No change to Templates' role or the canvas.
- Export stays API-only (`GET /api/workflows/{id}/export`) — it was never
  on the dev page.
- No new pages for G11's future attention surface — but the merged home is
  its natural landing (a "needs attention" strip above the cards).
- No per-action permission model (referenced deferral, §4b).
- **Nothing here consults veracium** (external-review regression notes,
  affirmed): workflow existence, ownership, roles, run state, and effect
  classification come from the transactional API only; memory-recalled
  context never decides visibility or permissions. Workflow ids are
  unchanged by the rename, so veracium-side references stay stable; the
  Instances→Runs rename may leave stale "Instances" wording in remembered
  guidance — cosmetic, self-corrects as new observations accrue.

## 8. Cut plan (revised sequencing — external review; IA1/IA2 are NOT independent)

The card/table component shape depends on attribution + bundled metadata,
so the sidecar lands first, not second:

| Cut | Contents | Size |
|---|---|---|
| **IA1 — data model + routes** | Attribution/metadata sidecar (backend + tests, incl. cross-org isolation); `?view=` param + `/workflows` → `/?view=table` redirect; `/runs` canonical + `/instances` redirect (detail routes untouched); shared catalog data hook with the §6 partial-failure contract. | M |
| **IA2 — renderings + dialogs** | Unfiltered merged list, card + table renderings off the shared hook, Run/Import dialogs extracted and wired per the §4b matrix, delete-disabled-for-bundled, `Tool.effect` + run-dialog warning/confirm. Basic keyboard/focus handling (dialog focus return) lands HERE, not deferred. | M–L |
| **IA3 — regression + polish** | e2e: same-ids-in-both-renderings, redirect-to-table-mode, viewer-direct-endpoint 403s, mutating-vs-read-only warning presence/absence, bundled delete-disabled, attribution-failure degradation; axe on both renderings; empty states; toggle keyboard + URL-state compatibility. | S–M |

**Estimate: 2–3 days** (the external review is right that ~1 day was
optimistic once dialog extraction, the sidecar with authorization, effect
classification, and the browser matrix are counted honestly).

## 9. Risks / open questions

- **Card grid scale**: fine at ≤ dozens of definitions; table mode is the
  pressure valve. No pagination in this cut; the catalog-view endpoint
  (§6) is the escape hatch.
- **Naming**: "Runs" recommended (Zapier "Runs", n8n "Executions").
- Keep `/instances/:id` deep links working forever-ish; they are embedded
  in WS payload links and batch results.
