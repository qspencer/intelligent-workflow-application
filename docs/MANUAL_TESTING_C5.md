# Manual Testing — C5 Friendly Shell & Cold-Start

A focused, follow-along test script for the **C5** canvas roadmap slice (see
`docs/CANVAS_ROADMAP.md`): the Automations home, the templates gallery, blank
create / template clone, and the Developer-mode reframe.

This is a **GUI test script** — a human clicks through and checks results. It is
narrower than the operator playbook in `docs/MANUAL_TESTING.md`; that doc covers
the backend, connectors, replay, RBAC at the API level, etc. Use this one to
verify the new front door.

> **In scope (C5):** Automations home, Templates gallery, Create (blank + clone),
> Developer toggle, role-gating of authoring, the create→canvas hand-off.
> **Out of scope (not built yet — C6):** Test/dry-run, cost estimate, capability
> boundaries, explain-this-run. Don't look for those; they're the next slice.

---

## Setup

Two processes. The Vite dev server runs on **:4200** and proxies `/api` + `/ws`
to the backend on **:8001**.

```bash
# Terminal 1 — backend (in-memory repos, dev auth, examples as templates)
cd backend
AUTH_MODE=dev uv run uvicorn workflow_platform.main:app --reload --port 8001

# Terminal 2 — frontend dev server
cd frontend
npm run dev      # → http://localhost:4200
```

Open **http://localhost:4200**.

Notes:
- **Templates load from the repo-root `examples/` by default**, resolved
  independently of the working directory — so launching from `backend/` (above)
  or from the repo root both work, no env var needed. Override with
  `WORKFLOW_DEFINITIONS_DIR=/path/to/dir` to point the gallery (and the trigger
  orchestrator) elsewhere.
- **In-memory backend**: workflows you *create* live only until the backend
  restarts. The 10 **templates** are always present (loaded from the examples dir).
  Set `DATABASE_URL=...` before launching uvicorn if you want created workflows
  to persist (and to show up in the dashboard across restarts).
- **Roles**: the header has two independent controls —
  - **Role switcher** (right) — sets your identity (`admins` / `designers` /
    `operators` / `viewers` / `auditors`); writes localStorage and reloads.
  - **Developer toggle** — shows/hides the developer console nav. Independent of role.
- Default identity (nothing set) acts as **admin**.

---

## Test cases

Each case: **Steps → Expected.** Tick the box when it passes.

### TC1 — Automations home is the front door  ☐
**Steps:** Load http://localhost:4200/ (the bare root).
**Expected:**
- The page heading is **"Your automations"** (NOT a list of instances).
- Top nav shows **Automations** and **Templates** only (no Instances/Workflows/Cost).
- If the backend is fresh (nothing created), you see the **empty state**:
  "No automations yet." with a link to templates. If you've created/run things,
  you see a **card grid** instead.

### TC2 — Templates gallery lists the bundled examples  ☐
**Steps:** Click **Browse templates** (or the Templates nav link).
**Expected:**
- A card grid of **10 templates** (Email Triage, GitHub PR triage, Invoice
  Extraction, PDF Classifier, RPA Challenge OCR, Research Paper triage, Scheduled
  Health Report, Webhook Echo, etc.).
- Each card shows a **step count** and a **friendly trigger label** (e.g. "On a
  webhook", "On a schedule", "On a new file", "Run manually") — not the raw
  `webhook`/`schedule` enum.

### TC3 — Use a template → clone → land on the canvas  ☐
**Steps:** On a template card (as admin or designer), click **Use this template**.
**Expected:**
- You navigate to `/canvas/<new-id>?edit=1` and the canvas opens **in edit mode**
  (you see the "Editing" pill, the step palette, Save/Discard).
- The cloned **steps + edges are present** (matches the template's shape).
- The workflow name ends with **"(copy)"**; the id is a slug of that name
  (e.g. `email-triage-copy`).
- Go back to **Automations** — the new workflow now appears as a card.

### TC4 — Create a blank workflow  ☐
**Steps:** On the home, click **Create** → type a name (e.g. "Invoice triage") →
click **Create** in the dialog.
**Expected:**
- Canvas opens in **edit mode** with a single trigger node and **no steps**.
- The id is the slug of the name (`invoice-triage`).
- Add a step from the palette, Save — it persists (no error).
- Back on the home, the new workflow shows as a card with **0 runs**.

### TC5 — Blank id de-duplication  ☐
**Steps:** From the home, click **Create**, leave the name **blank**, Create.
Repeat a second time (also blank).
**Expected:**
- First → name "Untitled workflow", id `untitled-workflow`.
- Second → id `untitled-workflow-2` (no collision, no error).

### TC6 — Developer toggle reveals/hides the console  ☐
**Steps:** Click the **Developer: off** button (top-right). Then reload the page.
**Expected:**
- Toggling **on** makes **Instances / Workflows / Cost** appear in the nav;
  toggling **off** hides them again.
- The state **persists across reload** (localStorage).
- With it **on**, clicking **Workflows** shows the old developer list (table with
  ids/Steps/Instances columns) — i.e. the dev console still works, just demoted.

### TC7 — Deep links to the dev console still work  ☐
**Steps:** With Developer **off**, manually visit http://localhost:4200/workflows
and http://localhost:4200/instances in the address bar.
**Expected:** Both render normally. The toggle governs **nav visibility only** —
it does not disable the routes (no regression for developers/operators).

### TC8 — Card enrichment: run count + latest status  ☐
**Steps:** Run a workflow once (Developer on → Workflows → Run on a row, or open a
template-cloned manual workflow on the canvas and use **Run**). Return to the
**Automations** home.
**Expected:** That workflow's card shows an updated **run count** and a
**status pill** for the latest run (e.g. "Done" green, "Failed" red, "Running"
blue) — friendly words, not the raw engine enum.

### TC9 — RBAC: authoring is gated, reading is not  ☐
**Steps:** Use the **Role switcher** to become a **viewer**.
**Expected:**
- On the **home**, the **Create** button is gone; the empty-state text drops the
  "or create one" clause.
- In the **Templates** gallery, the **Use this template** button is gone (you can
  still browse).
- Switch back to **designer** or **admin** → both buttons return.
- (API cross-check, optional) a viewer hitting create is refused:
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8001/api/workflows \
    -H 'X-Dev-User: v' -H 'X-Dev-Groups: viewers' -H 'Content-Type: application/json' -d '{}'
  # → 403
  ```

### TC10 — Dialog cancel / no-op safety  ☐
**Steps:** Open **Create**, type a name, click **Cancel** (and/or click the
overlay outside the dialog).
**Expected:** Dialog closes, **no workflow created**, home unchanged. Re-opening
Create starts with an empty name field.

---

## API-level cross-check (optional)

If you'd rather verify the two new endpoints directly:

```bash
H='-H X-Dev-User:dana -H X-Dev-Groups:designers'

# Templates list (any authenticated role)
curl -s $H http://localhost:8001/api/templates | python -m json.tool | head -30

# Clone a template
curl -s $H -H 'Content-Type: application/json' \
  -d '{"template_id":"webhook-echo"}' \
  http://localhost:8001/api/workflows | python -m json.tool

# Create blank
curl -s $H -H 'Content-Type: application/json' -d '{}' \
  http://localhost:8001/api/workflows | python -m json.tool
```

Expected: templates returns 10 summaries (`id`, `name`, `description`,
`step_count`, `trigger_type`); both POSTs return **201** with the persisted
definition (alias-correct `from`/`to` on edges).

---

## Known limitations / what you can't test yet

- **No C6 surfaces** — there's no Test/dry-run button, cost estimate, capability
  view, or explain-this-run. Those are the next slice.
- **Agentic-goal editing** on the canvas is still a freeform textarea (Designer
  role), unchanged by C5.
- **Created workflows are ephemeral** under the in-memory backend (gone on
  restart). Use `DATABASE_URL` for persistence.
- **Batch run from a form**, **connector picker**, and **build-time validation**
  are later cuts (C7/C8) — the create→canvas flow uses the existing edit surface.

---

## Note on the broader playbook

`docs/MANUAL_TESTING.md` Section 2 ("Frontend boots and the routes render") and
3b ("The workflow canvas") predate the C5 reframe — they still describe the old
default route (`/instances`) and the three-link nav. They need a refresh to match
the Automations-home IA. Out of scope for this C5 script; flagged so the two docs
don't silently disagree.
