# Veracium 0.4.1 Feature Adoption — Design

Status: **proposed** (drafted 2026-07-29; design-reviewed same day:
adopt-with-conditions, all findings folded in below — including the
reviewer's verification that introspect is genuinely store-only, and a
correction to this plan's own API inventory. Not yet built). The pin
is already at 0.4.1 (`a06c9ee`, security bump for GHSA-r7j7-5jq9-3f5q);
this plan decides what the platform *consumes* from 0.4.x, feature by
feature, against the verified installed API — not the coordination-file
prose. Companion to the veracium adoption conditions in
`docs/SEMANTICS.md` (engine-written observations only; metered LLM;
COALA N1 — no agent-facing memory tools), all of which this plan keeps.

## 0. Inventory, verified against the installed package

| 0.4.x item | What it actually is (inspected) | Disposition |
|---|---|---|
| T1 subset-absorption | Write-path dedup: near-duplicate facts absorb instead of duplicating. **Correction from review: there is NO `absorbed` ingest bucket in 0.4.1** — `remember` returns `{episode, facts, quarantined[, unparseable]}`; absorption is visible only as `invalidation_reason="absorbed_duplicate"` in introspect's retired-by-reason dict | **Passive — already benefiting; observability via §3 (introspect-sourced)** |
| 0.4.1 trust guard | Third-party restatements can no longer inflate/retire user-grade edges | **Passive — prerequisite for G12, noted there** |
| `Memory.introspect(user_id, mode="summary"\|"categories")` | LLM-free, store-only transparency view: counts by relation / evidence author / disclosure tier, lifecycle + retired-by-reason, episode counts; `categories` adds the facts themselves with provenance markers | **Adopt — the memory transparency surface (§2)** |
| `Memory.list_entities()` | Distinct namespaces with edge/episode counts | **Adopt — feeds the same surface (§2)** |
| Proactive recall (`veracium.proactive.assemble`) | Session-start briefing: dated commitments → recent changes, disclosure-gated, token-budgeted | **Assess → defer with named triggers (§4)** |
| CLI verbs (`recall`/`remember`/`introspect`) | Operator convenience against a store file; store-only for reads | **No build — one ops-doc line (§5)** |
| Claude Code hooks recipe | Interactive-session integration pattern | **N/A to a headless platform** |

## 1. The thesis: introspection is a trust-wedge feature, not a dev tool

The platform's differentiator (C6) is surfacing governance no competitor
shows: dry-run, per-tool-call audit, capability boundaries, cost. "What
has the system learned, about whom, from whose evidence?" is the same
wedge pointed at memory — and it is the operator-facing answer to the
question every learned-memory system eventually gets asked ("what do you
know about me and where did it come from?"). Veracium now ships that
view LLM-free; the platform's job is scoping, gating, and rendering it —
not rebuilding it.

## 2. The memory transparency surface (the one real build)

### 2a. Service layer

`LearnedMemoryService` gains two read-only methods, both `to_thread`
wrapped, both zero-LLM (introspect is store-only by construction —
no metering needed, no dry-run interaction, safe under replay):

- `list_memory_namespaces() -> list[dict]` — `Memory.list_entities()`
  (store-wide) filtered to namespace-shaped ids
  (`org:<org>:user:<key>`), each parsed into `{org_id, account, edges,
  episodes}`. **Non-namespace ids are counted, not silently dropped**
  (review finding: a transparency surface that hides rows undercuts its
  own thesis) — the list response carries an Administrator-only
  `unrecognized_ids` count so legacy/other-shaped keys are visible as a
  number even though they render no rows.
- `introspect_namespace(namespace, mode) -> dict` —
  `Memory.introspect(user_id=namespace, mode=mode)` passthrough.

Reads are **lock-free** — they do not take the service's write `_lock`
(SqliteStore has its own; the reviewer confirmed read safety), so a slow
introspect can never stall observation writes. Verified in review:
introspect/list_entities are pure store aggregation — no LLM reference,
no recompile path — so the zero-cost claim is fact, not hope.

### 2b. API (org-scoped like every resource; memory is tenant data)

- `GET /api/memory/summary` — namespaces visible to the caller with
  counts. **Administrator: all orgs; Organization Administrator: own org
  only** (prefix-filtered server-side). Org User/Viewer: 403 — the
  `categories` mode renders mail-derived personal content, so first
  release keeps the surface at the admin tier (same posture as Users
  admin). Self-service "my own memory" view is a real future feature but
  waits on per-user memory keys (ROLES_PLAN §8 already names that
  trigger).
- `GET /api/memory/summary/{org}/{account}?mode=summary|categories` —
  introspect detail for one namespace, **keyed by the full (org,
  account) identity** (review blocking finding: account alone is
  underdetermined for Administrators when the same account exists in
  two orgs; the explicit org segment also hands `org_bypass` its
  resource-org cleanly). Non-Administrators whose own org ≠ path org →
  **404, not 403** (S2's no-existence-leak rule); garbage path params
  (schemathesis will fuzz an email-shaped segment) → 404, never 500. `mode=categories` returns
  veracium's rendered facts **verbatim, provenance markers intact** —
  the same never-rewrite rule G10 pinned for recall injection applies to
  display.
- **Size stance (review finding: categories mode is unbounded —
  introspect renders every active edge; the production namespace holds
  ~8k rows):** the endpoint passes through veracium's rendering without
  reformatting but enforces a response cap (`mode=categories` truncated
  at a configured byte budget with an explicit `truncated: true` flag +
  the summary counts always intact). Pagination is deliberately NOT
  built until a real operator hits the cap — the flag tells us when.
- **Audited**: `memory_introspected` audit entry (actor, namespace,
  mode) on the detail endpoint. Unlike cost reports, this view exposes
  personal-data-heavy content — an access log is the honest posture
  (and the GDPR-shaped ask, when it comes, will want one). List
  endpoint unaudited (counts only).
- Administrator cross-org detail reads ride the existing `org_bypass`
  audit convention.

### 2c. Dashboard (Developer console → new "Memory" page)

- Org-scoped namespace table (account, org badge per IA rules, edge /
  episode counts) → detail view: summary panel (counts by relation /
  author / disclosure / lifecycle, retired-by-reason, episodes) +
  a "Show facts" affordance that fetches `mode=categories` and renders
  the grouped facts verbatim (monospace, provenance markers preserved,
  no reflowing of the fence text). **Text-node rendering ONLY** (review
  condition, pinned): the facts contain attacker-authored mail content —
  no `dangerouslySetInnerHTML`, no markdown rendering, no HTML parsing
  anywhere in the component tree; React text-node escaping is the whole
  defense and the test asserts the fence text arrives byte-identical.
- Skeleton loading states, 720px responsive, axe on the new page
  (the C8 baseline applies to new pages by default).

## 3. Absorption observability (XS, corrected by review)

The original idea (an `absorbed` count on `memory_observed` audit
entries) rested on an ingest bucket that **does not exist** in 0.4.1 —
`remember` returns `{episode, facts, quarantined[, unparseable]}` only.
Absorption is observable exactly one place: introspect's
retired-by-reason dict (`invalidation_reason="absorbed_duplicate"`).
So §3 collapses into §2: the transparency surface's summary panel shows
retired-by-reason counts, which IS the T1 visibility — per-write audit
stays as-is. If a per-write signal ever matters, the ask goes upstream
(a returned bucket) rather than being reverse-engineered here.

## 4. Proactive recall — assessed, deferred with named triggers

The briefing is built for session-shaped consumers (its top section is
DATED COMMITMENTS); the platform's triage path is message-shaped, and
its per-step entity recall (G10) is already the better-targeted
injection for that job. No current consumer = no build. Two named
triggers, either of which reopens it:

1. **G12 elicitation** — the briefing is the natural "what memory
   believes, surfaced for confirmation" substrate; G12's design pass
   should evaluate `proactive.assemble` before inventing its own.
2. **An operator daily-brief surface** (dashboard panel or scheduled
   email summarizing commitments/changes memory has accumulated) — if
   the attention axis's awaiting-reply lifecycle grows toward
   commitments, this is where DATED COMMITMENTS plugs in.

## 5. What this deliberately does not do

- **No agent-facing introspect/recall tools** — COALA N1 stands; every
  0.4.x consumer here is operator-facing or engine-internal.
- **No `remember` path anywhere** — writes remain engine-only via
  declared observations (adoption condition, unchanged by the CLI's
  existence).
- CLI verbs: one line in `docs/MANUAL_TESTING.md` ops notes (they work
  directly against `.memory/learned.db` for local debugging; reads are
  store-only). No wrapping, no build.
- No maintain/forget/export surfaces — real features, no current pull;
  they join the §4 deferral pattern when a workload asks.

## 6. Test plan

- Service: namespace filtering (non-namespace ids excluded), org
  parsing, both modes passthrough, zero-Bedrock-call assertion (the
  stub-provider guarantee holds through our wrapper).
- API: role matrix (Administrator all-orgs / Org Admin own-org /
  Org User + Viewer 403), cross-org 404 no-leak, `memory_introspected`
  audit entry with mode, `org_bypass` on Administrator cross-org reads,
  schema suite picks up both GETs automatically.
- Frontend: page renders from fixture data, org badge rules, verbatim
  fact rendering (fence text byte-identical through the component; a
  fixture fact containing `<script>` renders inert as text), skeleton +
  error states, truncation flag surfaced; axe via the existing e2e
  harness.

## 7. Cuts

| Cut | Contents | Size |
|---|---|---|
| **V1** | Service methods + both endpoints + audit + tests | S |
| **V2** | Dashboard Memory page + e2e/axe | S |
| **V3** | (absorbed into V1/V2 — §3 corrected; retired-by-reason counts ship with the summary panel) | — |

Estimate: ~1 day (the review called this optimistic-but-within-
precedent; the V3 collapse buys the slack back). Sequencing: independent
of the codify build — slots anywhere; natural fit is right after codify
ships (the codify CLI's evidence queries and this surface read the same
store, and the transparency page makes codification's effects visible to
the operator). If the IA rework lands first, the Memory page joins the
Developer console under whatever nav shape IA1 leaves behind — a nav
line, not a conflict.
