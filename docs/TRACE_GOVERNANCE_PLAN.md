# Trace Governance — Design

Status: **proposed** (drafted 2026-08-01; internal design review folded —
adopt-with-conditions, all seven conditions below). Not yet built.
The coupled design for the three F3 pre-external-organization gates the
external code review left open after the read-surface redaction closed
(rounds 1–4, HEAD `4de5d58`):

1. A **raw-trace privilege distinct from ordinary administration.**
2. **Audited raw access** — every raw-trace read is itself recorded.
3. **Storage-level trace separation** — raw payloads live apart from the
   operational record, so the ordinary tables never contain them.

These are one design, not three: the privilege gates the vault, the audit
records access to it, and the storage split is what makes the vault a
distinct thing to gate and audit. This document decides the shape; it does
NOT authorize the build (trigger below).

## 0. Why now, and the trigger

**Trigger to BUILD:** before the first external organization is
provisioned (same gate as `ROLES_PLAN` §8's multi-tenancy hardening and
`THREAT_MODEL` §5a's host-administrator disclosure). Until then the host
operator and Administrators are trusted (THREAT_MODEL §5a), so raw traces
in the operational store are an accepted single-operator risk.

**The external-org gate requires TG1–TG3, not a subset** (internal review):
all three asks share this one trigger. Shipping TG1+TG2 (privilege + audit)
and then onboarding an external org would leave raw content at rest in the
operational store — readable by the host/DB operator who is *no longer
trusted* past the gate — and leave the read-surface treadmill intact. TG1
and TG2 are a coherent internal milestone but do not, alone, satisfy the
gate.

**Why write the spec now, ahead of the build:** the F3 read-surface fix
took four review rounds because *redact-at-read is a treadmill* — every
new read surface is a new place to leak (tool_call entry → step
outputs/context → echoed `output_text` → the list endpoint). The
storage-separation design below **inverts** that: the operational path
stores *safe by default* and raw goes to a vault, which retires the
read-surface projection as the primary control and ends that finding
class. Having the design on paper lets the operator decide whether to
pull it forward rather than add a sixth surface to the treadmill.

## 1. What "raw trace" means (the asset being governed)

The sensitive content, today stored inline across the operational tables:

- **Tool call input/result** — `StepExecution.output.tool_calls[].{input,
  result}` (mail recipients/subjects/bodies from `email_send`, file
  contents from `file_read`, browser text, external error detail).
- **Model free-text that may echo the above** — `output.output_text` of a
  tool-bearing agentic step (the round-3 finding).
- **Trigger payloads** — `WorkflowInstance.trigger_payload` (the raw
  inbound message for email workflows: `{from, subject, body, …}`).
- **Recalled correspondent history** — the `recall` field on an agentic
  step's output (`executor.py`), veracium edges/episodes derived from
  third-party mail (internal review F1/F7: a raw asset the first draft
  omitted).
- **The same, duplicated in audit** — `step_completed` / `tool_call`
  audit `detail`.

**Projection is per-asset, NOT one shared function** (internal review F1 —
blocking). `redact_tool_data` sanitizes only `tool_calls` +
`output_text`-when-a-tool-was-used. `trigger_payload` and `recall` share
none of those keys, so reusing it on them is a **no-op** — the reason the
detail endpoint still leaks a raw email today (F4, §5a). Each asset gets
its own projection:

- **trigger payload** → keep *routing* fields only (message_id, thread_id,
  from-address) and strip subject/body/headers. Routing must survive
  because `pin_params` and resume/fork depend on it (§4).
- **recall** → withheld entirely below the grant (it is third-party
  correspondent history, not routing).

`raw_traces.kind` therefore includes `recall` alongside `tool_calls`,
`output_text`, `trigger_payload`.

Current control (HEAD `4de5d58`): tool-call/output content is **stored
raw** and `api/redaction.py::redact_tool_data` **projects it out at every
read surface** for below-`ADMIN_TIER` callers; the list endpoint omits
`context`+`trigger_payload` entirely. This spec keeps that projection as
defense-in-depth but moves the primary control to storage — and closes the
`trigger_payload`/`recall` no-op the shared function leaves open.

**Memory-introspection reconciliation (internal review F7):** the
`/api/memory/summary/{org}/{account}` surface renders the SAME class of raw
correspondent content under a *role* gate (`_MEMORY_ADMIN_ROLES`), audited
as `memory_introspected`. After this design, raw correspondent content has
ONE privilege — the grant — so the introspection mode that renders facts
also requires `can_read_raw_traces`; its counts/summary mode stays
role-gated (aggregate counts are not raw content). Folded into TG1 so the
grant is not undercut by a second door to the same content.

## 2. Decision 1 — the raw-trace privilege

**Chosen: a capability grant, NOT a new role.** The four-role model
(`ROLES_PLAN`) stays fixed; raw-trace access is a **per-user boolean grant
`can_read_raw_traces`** on the `users` row, settable only by an
Administrator, defaulting **off for everyone including Administrators and
Org Admins**. Rationale:

- The reviewer's requirement is a privilege *distinct from ordinary
  administration* — so it cannot be "Administrator implies raw," which is
  today's `ADMIN_TIER` shortcut. A per-user grant is distinct by
  construction: an Administrator manages the platform but does not read
  raw mail content unless separately, deliberately granted it.
- A fifth role was rejected: it would force a raw-reader to *also* be
  audit/ops-shaped, and it collides with `ROLES_PLAN` §8's deferred
  "distinct Auditor role" (which is org-read-only, a different axis). The
  grant composes with any role and consumes neither the Auditor nor the
  per-action-permissions deferral.
- **Break-glass option (deferred sub-item):** the grant may carry an
  `expires_at` (time-bounded elevation) — designed-for but not required in
  the first build; a permanent grant is acceptable at first external org.

**Replace `ADMIN_TIER` at ALL FOUR consult sites** (internal review F6):
the raw-check becomes `user.can_read_raw_traces` at the audit projection,
the instance endpoint, `explain`, AND `ws.py`'s **independent**
`raw_reader` check — the WS today computes its own `ORG_ADMIN_ROLES` test
(`ws.py:130`) that "replace `_raw_trace_reader` everywhere" would miss. The
WS handler already loads the platform user row (`ws.py:98`), so the grant
is readable there. The role-based shortcut retires: an Org Admin no longer
sees raw by virtue of being an Org Admin.

**Intended operational break, stated plainly:** after this lands NO ONE is
auto-granted — a freshly bootstrapped Administrator sees redacted traces
until an Administrator explicitly grants `can_read_raw_traces`. That is the
point (distinct-from-administration), not a regression, and it is called
out so the rollout expects it.

**Scope:** the grant is platform-wide but always composes with org
scoping — a granted user still only reaches their org's traces (cross-org
stays 404). A granted Administrator reaches any org's traces, and that
cross-org raw read is `org_bypass`-audited *and* raw-access-audited (§3).

## 3. Decision 2 — audited raw access

**Every read that returns a raw trace emits a `raw_trace_accessed` audit
entry** (in addition to any `org_bypass`), capturing: actor, the
instance/step reached, and the surface (audit / instance / explain / ws).

- **NO content hash** (internal review F5). The `raw_trace_accessed` entry
  lands in the ordinary `ANY_ROLE`-readable audit log, so a
  hash-of-what-was-returned would re-create exactly the low-entropy
  equality/dictionary oracle `safe_tool_call` deliberately dropped
  (`redaction.py`). The entry records only *that* a raw read happened, by
  whom, of which instance/step — never a fingerprint of the content.
- **Granularity: per request per instance.** One entry per
  raw-trace-returning response, not per field (avoids audit amplification)
  and not per-session (too coarse to be forensically useful).
- **Where it lives:** the ordinary audit log — access-metadata, not
  content, so it is not itself a raw trace. Two caveats named: (a)
  audit-of-the-auditors — a distinct "who watched the watchers" privilege
  — is deferred (§5); (b) because definition/instance deletion cascades
  audit, a granted Administrator could erase their own `raw_trace_accessed`
  trail. The tamper-evident-audit gate (`THREAT_MODEL` §8) is the eventual
  answer; noted here, not solved here.
- **Fail-closed:** if the `raw_trace_accessed` write fails, the raw content
  is **not** returned (the read degrades to the projected view). Access we
  can't record is access we don't grant. This is a read-time single append
  and is independent of the non-atomic *write*-path caveat
  (`EXECUTION_SEMANTICS` §3): a failure here withholds content rather than
  half-completing a run.

## 4. Decision 3 — storage-level trace separation (the inversion)

**Raw payloads move to a separate `raw_traces` table; the operational
tables store only the safe projection.**

- New table `raw_traces(id, org_id, instance_id, step_id NULL, attempt,
  kind, payload JSONB, created_at)` (internal review F1/F8). `kind ∈
  {tool_calls, output_text, trigger_payload, recall}`. `org_id` is carried
  directly — cheap per-tenant retention/deletion without a join through
  `instances`. `step_id` is NULL for the instance-level `trigger_payload`
  row. `attempt` disambiguates retry rows so `(instance_id, step_id, kind,
  attempt)` is unique. Written by the engine at the same points it writes
  step output / audit, under the same non-atomic caveat already documented
  (EXECUTION_SEMANTICS §3) — a `raw_traces` write failure logs and never
  fails the run.
- `StepExecution.output`, the `step_completed`/`tool_call` audit `detail`,
  `WorkflowInstance.trigger_payload`, and the `recall` field store the
  **safe projection only** (the per-asset projections of §1 applied at
  WRITE time, plus a `raw_ref` pointing at the `raw_traces` row).
- **Consequence — reads simplify to a JOIN, not a filter:** an ordinary
  read returns the operational row as-is (already safe). A privileged,
  audited raw read additionally fetches + merges the `raw_traces` payload.
  Read-time projection becomes belt-and-suspenders, not the primary
  control — a new read surface added later is safe by default because the
  operational store has nothing to leak.
- **THE ENGINE REHYDRATES `context` FROM `raw_traces` — load-bearing
  (internal review F2/F3, blocking).** Within a single run,
  `context.steps`/`context.trigger` hold the FULL output in memory and are
  unaffected. But **resume rebuilds `context.steps` from the persisted
  `instance.context`, and fork rebuilds it from persisted `step.output`**
  (`executor.py:222-226, 266-316`). If those are the projected-safe copies,
  a resumed/forked agentic step's prior-steps message and its
  conditional-edge evaluation would get redacted data — silently changing
  execution semantics (`EXECUTION_SEMANTICS` is normative). Worse,
  `pin_params` resolve routing fields from `trigger.*` and **fail closed**
  (`executor.py:1094-1112`): a projected-away `message_id` would kill a
  resumed `apply` step. **Therefore resume and fork MUST rehydrate the full
  `context.steps`/`context.trigger` from `raw_traces` before driving the
  engine.** This is why trigger-payload projection keeps routing fields
  (§1) and why deliberate rehydration — not the current accidental
  no-op-preservation — is the correct mechanism. Test-pinned as criterion 7.
- **Encryption at rest / separate retention:** `raw_traces` is the natural
  home for a shorter retention window and (deployment-tier) column
  encryption — designed-for, deferred to the deployment that needs it.

**Migration:** additive (`0006`, new table + `raw_ref`/projected columns);
a backfill sweeps existing instances (project their inline raw → move to
`raw_traces`, replace inline with safe). The backfill is one-way and
operator-run, mirroring the veracium-namespace and role migrations.

## 5. What this deliberately does NOT do (first build)

- Break-glass time-boxing (`expires_at`) — designed-for (§2), not built
  first; permanent grants acceptable at first external org.
- Audit-of-raw-access read restriction — the `raw_trace_accessed` entries
  are Administrator-readable; a separate "who watched the watchers"
  privilege is a named deferral (§3).
- Column-level encryption / distinct retention on `raw_traces` — the table
  is the seam that makes both cheap later; not first build.
- Changing the tool-call projection algorithm — `redact_tool_data` is
  unchanged; it moves from read-time-primary to write-time-primary +
  read-time-backstop. The *new* projections are the trigger-payload and
  recall ones (§1), which the shared function never covered.

### 5a. Live gap this plan's landing fixes now (internal review F4)

The `/workflow-instances/{id}` **detail** endpoint calls
`redact_tool_data(instance.model_dump(), admin)` — which is a no-op on
`trigger_payload` (F1). So a same-org **Viewer** (the endpoint is
`ANY_ROLE`) can recover the raw inbound email TODAY, even though the list
endpoint deliberately omits it. This is a pre-existing, live below-grant
leak — not introduced by this plan, but the same thorough reviewer who
found the `output_text` echo and the list endpoint will find it. It is
**fixed in the commit that introduces this doc** by applying the
trigger-payload/recall projection at the read surfaces (a small,
independent change), ahead of the full storage inversion. Criterion 1
names the detail endpoint explicitly.

## 6. Interaction with existing designs (stated, so review can check)

- **ROLES_PLAN §8** — the grant is orthogonal to the deferred Auditor role
  (org-read-only) and to per-action permissions; it does not consume
  either trigger. `users.can_read_raw_traces` is a new column beside
  `roles`.
- **THREAT_MODEL §5a** — this is the control that lets §5a's "Administrators
  are trusted with raw content" assumption be *narrowed* at first external
  org: Administrators keep platform control but lose default raw-content
  access.
- **EXECUTION_SEMANTICS §3** — `raw_traces` writes inherit the existing
  non-atomic-persistence caveat; the read-time audit append (§3) is
  separate and fail-closed. No new write-path guarantee claimed.
- **AUTH_PLAN** — the grant is set through the existing admin-gated
  `/api/users` surface (a new field + guard), no new auth mode.

## 7. Test-pinned acceptance criteria (for the build, not now)

1. A user WITHOUT the grant (any role, incl. Administrator/Org Admin)
   recovers no raw tool payload / echoed `output_text` / **trigger
   payload** / **recall** from any read surface — explicitly including the
   `/workflow-instances/{id}` DETAIL endpoint, not only list/audit
   (internal review F4).
2. A user WITH the grant recovers raw content — and every such read emits
   exactly one `raw_trace_accessed` entry with the correct actor/instance/
   surface and NO content hash or raw content in the entry.
3. A failed `raw_trace_accessed` write degrades the read to projected
   (fail-closed).
4. The operational tables (`step_executions.output`,
   `workflow_instances.trigger_payload`, `audit_log.detail`, and the
   `recall` field) contain the safe projection only — asserted directly
   against the repo after a run with sentinel secrets (proves storage-level
   separation, not just response projection).
5. Cross-org raw read by a granted Administrator emits BOTH `org_bypass`
   and `raw_trace_accessed`.
6. Migration `0006` moves existing inline raw into `raw_traces` and leaves
   the operational rows safe. The seeded pre-0006 fixture MUST include a raw
   trigger BODY and a recall episode, not only tool_calls — otherwise it
   silently passes on the §1 no-op (internal review F9).
7. **Resume AND fork of a tool-bearing step reproduce the same downstream
   behavior as a straight-through run** (internal review F2/F3): the
   rehydrated `context` carries the full prior output; a pinned `apply`
   step's `message_id` still resolves; conditional edges evaluate
   identically.

## 8. Cut plan (when triggered)

| Cut | Contents | Size |
|---|---|---|
| **TG1** | `can_read_raw_traces` grant + `/api/users` guard; replace `ADMIN_TIER` raw-check with the grant at all four sites (incl. WS); fold memory-introspection facts-mode under the grant; trigger-payload/recall read-surface projection (closes §5a live gap); criterion 1 pinned | S–M |
| **TG2** | `raw_trace_accessed` audit + fail-closed; criteria 2,3,5 | S |
| **TG3** | `raw_traces` table (+`org_id`,`attempt`) + per-asset write-time projection + `raw_ref` merge on privileged read + **engine rehydration on resume/fork** + migration `0006` + backfill; criteria 4,6,7 | M–L |

TG1+TG2 are a coherent internal milestone but **do NOT satisfy the
external-org gate** (§0) — that requires TG1–TG3, because only TG3 gets raw
content out of the operational store the post-gate host operator can reach.
Estimate ~2–3 days total when triggered.
