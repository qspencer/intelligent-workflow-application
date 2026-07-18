# Tenant-Scoped Roles — Design

Status: **S1 + S2 + S3 shipped** (all 2026-07-18, same day as the design
review — the plan is fully executed; §8's deferrals are what remains, each
behind its named trigger). S3 delivered the org lifecycle:
`GET/POST/PATCH /api/organizations` (Administrator-gated writes, slugified
ids, `org_created`/`org_renamed` audit; **no DELETE** — orgs are rename-only
per §8, pinned by test), Administrator user-moves between orgs via
`PATCH /api/users/{id}` (`org_id`; sessions revoked, target org validated,
and the last-org-admin guard protects the *old* org on the way out), and the
Users admin page grew an Org column, org pickers in the create/edit dialogs,
and an Organizations dialog (create + rename) — all Administrator-only
affordances that stay hidden for other roles. S2 delivered resource scoping end to
end: `OrgScope` resolution on every §4-table endpoint (cross-org reads AND
mutations 404 — no existence leaks), the §4b joins for audit/steps/cost
(instance-less audit entries are Administrator-only), escalations scoped
through their instance, bulk-delete org-bounded, `org_bypass` audited on
Administrator cross-org mutations, event `org_id` enrichment at emit time +
WS delivery filtering (system events to Administrators only), instances now
inherit their definition's org at run/fork time, and the org-aware veracium
namespace (`org:<org>:user:<key>`) live — engine + offline tools composed,
the production store's 7,981 rows migrated (`tools/
migrate_learned_namespace.py`), recall verified intact under the new keys.
All seven §7 isolation criteria are test-pinned in
`backend/tests/test_org_isolation.py`. S1 delivered: the four-role vocabulary end to end (enum, group
map, RoleSwitcher, Users admin, `create_user.py`), Alembic `0005` data
migration, the §4-table translation of every `require_roles` call site with
the §4c expansions test-pinned, and org-scoped `/api/users` (Org Admins
manage their org only, cannot grant/touch Administrator, cross-org targets
404, last-org-admin guard live). D4's Human Permissions table revised in
place. Companion to `docs/AUTH_PLAN.md` (which built
the authentication + role *machinery*) — this document changes the role
*model* and starts the multi-tenancy enforcement work deferred by the `0003`
users/orgs skeleton. Revises D4's "Human Permissions" table in
`docs/ARCHITECTURE.md` on adoption.

## 1. The proposal and what it actually is

Replace the five global roles (Admin / Workflow Designer / Operator /
Viewer / Auditor) with four:

| Role | Scope | Powers |
|---|---|---|
| **Administrator** | Platform-wide | Everything, across all organizations. Creates organizations. The only role that can grant Administrator. |
| **Organization Administrator** | One organization | Everything belonging to or affecting their org: workflows, runs, users (of that org), audit, cost. Cannot see or touch other orgs. |
| **Organization User** | One organization | Create, edit, and run workflows in their org; view their org's runs, audit entries, and costs. No user management. |
| **Organization Viewer** | One organization | Read-only: dashboards, run history, audit, cost — for their org. Cannot create, edit, run, or dry-run anything. |

The critical framing: **this changes the axis of the role system.** The
current five roles answer *"what actions can you take?"* over one global
resource pool. These four answer *"over which resources?"* — Administrator
and Organization Administrator differ by scope, not powers. That makes this
proposal the multi-tenancy enforcement epic the `0003` skeleton deliberately
deferred, wearing a role hat. The role vocabulary is the small part; the
work is org-scoping every endpoint. §6 stages it so the cheap parts ship
first.

**Why Organization Viewer is load-bearing here:** in this product, *run* is
a spend action (live Bedrock tokens). The line between "can watch the
dashboard" and "can fire a workflow" is a budget-control line, not just a
permissions nicety. Viewer also cannot dry-run — dry runs sandbox the world
but keep the live brain, so they spend real money too.

### Why the five-role collapse is sound

- **Designer + Operator → Organization User.** Nobody has ever exercised
  the build-vs-run distinction (one user, same person in small orgs). If a
  customer wants "can run but not edit," it returns as a permission flag
  inside Organization User, not a fifth role.
- **Viewer + Auditor → Organization Viewer.** The dedicated Auditor role's
  constituency (compliance) doesn't exist yet; Organization Viewer includes
  audit-trail read access for its org. A distinct Auditor (audit-only, no
  operational dashboards) returns when an enterprise asks — logged in §8.

## 2. Pinned semantics

Decisions that bite later if left implicit — pinned now:

1. **One org per user.** `users.org_id` stays a single column. Multi-org
   membership (an Org Admin of two tenants) means a `users_orgs` join table
   and belongs to a later phase (§8). Until then: one account, one org.
2. **Escalation guards.** An Organization Administrator can never create or
   promote an Administrator, and never touch users outside their org. Each
   org has a **last-org-admin guard**: the last active Organization
   Administrator of an org cannot be demoted or deactivated (the org-level
   analogue of the existing last-active-Admin guard). Administrators are
   exempt from the org guard but covered by the existing global guard.
3. **Only Administrators create organizations** (and only Administrators
   delete them — deletion semantics deferred to §8; nothing cascades yet).
4. **Administrator's org bypass is explicit and audited.** Administrators
   act across orgs; every enforcement point treats this as a named bypass
   (`org_bypass=true` in the relevant audit detail), never as a missing
   filter. An Administrator still *has* an org_id row value (the column is
   NOT NULL); it's simply not consulted for authorization.
5. **Cost is tenant data.** "Affecting one specific tenant" includes
   spend: cost reports scope by org. (Per-org *budgets* are a natural
   follow-up, §8 — policy enforcement stays per-workflow for now.)
6. **Org attribution is already there.** Definitions and instances carry
   `org_id` from birth (`0003`); enforcement reads what attribution already
   wrote. New definitions inherit the creating user's org (Administrators
   creating a definition attribute it to their own org unless they pass an
   explicit `org_id` — the API grows that optional parameter in S2).

## 3. Role storage and mapping

No schema change. Roles remain strings — `users.roles` (local mode), IdP
groups via `OIDC_GROUP_TO_ROLE` (oidc mode), `X-Dev-Groups` (dev mode) —
with exclusive per-mode authority exactly as `AUTH_PLAN` §6 defined.

Old → new mapping (applied wherever old strings live):

| Old | New |
|---|---|
| Admin | Administrator |
| Workflow Designer | Organization User |
| Operator | Organization User |
| Viewer | Organization Viewer |
| Auditor | Organization Viewer |

- The `Role` enum is replaced; `require_roles` call sites are re-audited
  one by one during S1 (the mapping above is the default translation, but
  each endpoint gets an explicit look — that audit *is* the review of the
  permission surface).
- Local users: a one-shot data migration rewrites `users.roles` arrays by
  the table above (Alembic `0005`, pure UPDATE, reversible by the inverse
  mapping with the documented loss that Designer/Operator and
  Viewer/Auditor distinctions cannot be reconstructed — acceptable: no
  production tenant exists).
- Dev defaults: `X-Dev-Groups` friendly names become `admins` →
  Administrator, `org-admins` → Organization Administrator, `org-users` →
  Organization User, `org-viewers` → Organization Viewer. The dashboard
  RoleSwitcher options update to match.
- OIDC deployments set `OIDC_GROUP_TO_ROLE` explicitly; the built-in
  defaults change to the new names (documented breaking change for a
  deployment class that currently numbers zero).

## 4. Enforcement design

One new dependency factory replaces bare `require_roles` on org-scoped
endpoints:

```python
require_org_access(*roles: Role, resource_org: Callable[..., Awaitable[str]] | None = None)
```

- Resolves the caller's platform user (the existing `(iss, sub)` lookup) →
  `(user.org_id, user.roles)`.
- **Administrator**: allowed, org filter bypassed, `org_bypass` audited on
  mutating actions.
- Otherwise: the caller must hold one of `roles`, AND the target org must
  equal `user.org_id`. The target org is the resource's `org_id` (loaded by
  the endpoint via `resource_org` for `/{id}` routes) or the caller's own
  org (for list/create routes, which filter/attribute by it).
- List endpoints add `WHERE org_id = :caller_org` (repo methods grow an
  optional `org_id=` filter — additive, defaulting to unfiltered so
  engine-internal callers are untouched).

Action → minimum role, by endpoint family:

| Surface | Viewer | User | Org Admin |
|---|---|---|---|
| Read workflows / instances / steps / audit / cost / catalog / templates / cost-estimate / capabilities / explain / escalations list | ✓ | ✓ | ✓ |
| Create / edit / import / scaffold / validate workflows | | ✓ | ✓ |
| Run / run-batch / dry-run / fork / retry / pause / resume / kill | | ✓ | ✓ |
| Delete workflows | | ✓ | ✓ |
| Resolve escalations | | ✓ | ✓ |
| Manage org users (`/api/users`, own org only) | | | ✓ |
| Webhook trigger endpoints | *(unchanged — HMAC-authenticated, not user-authenticated)* | | |

`/api/me`, `/api/health`, `/metrics` are role-agnostic as today.
`/ws/events` keeps its current behavior (any authenticated user, all
events) through S1 — restricting it earlier would break the live
instance-detail merge for Org Users and violate S1's no-behavior-change
property. The org filter lands in S2 (see §4b for how events learn their
org). `/metrics` note for multi-tenant deployments: it exposes
platform-wide operational aggregates (cost by model, run counts) and is
an *operator* surface — deployments with more than one tenant should
firewall it (network-level), same posture as the Prometheus ecosystem
default; it never carries per-workflow or per-user detail.

### 4b. Org attribution for audit, step executions, and events

`0003` put `org_id` on definitions and instances only. Three surfaces the
§4 table scopes have **no org column**, and each needs a stated mechanism
(this section is the design-review blocking finding, resolved):

- **`step_executions` and `audit_log`: join, don't add columns.** Both
  carry `workflow_instance_id` (audit: nullable); the instance row carries
  `org_id`. Scoped queries join through it — repo methods grow the
  optional `org_id=` filter as `JOIN workflow_instances ON … WHERE
  workflow_instances.org_id = :org`. Volume is fine at our scale, the
  attribution can't drift from its instance, and no backfill migration is
  needed. Revisit (denormalize the column) only if the join shows up in
  profiles.
- **Instance-less audit entries** (`alert_*` monitoring entries,
  orchestrator load errors, `auth_*`, `user_*` — anything with
  `workflow_instance_id IS NULL`) are **platform-operator data:
  Administrator-only** in scoped listings. Org Admins see their org's
  instance-attached entries only. (User-management entries could be
  org-attributed later via a detail field; not needed for S2.)
- **EventBus / WS:** events are enriched with `org_id` at emit time — the
  engine already holds the instance when it mirrors an audit append, so
  the event dict gains the field at the source instead of per-subscriber
  lookups. The S2 WS filter then compares against the subscriber's org
  (Administrators receive everything). Events with no org (system alerts)
  go to Administrators only.
- **`CostReportService`**: `by_workflow` / `by_model` / `by_day` gain the
  same join-based org filter; an Org Admin's report covers their org, an
  Administrator's covers everything (optionally filtered by an explicit
  `org_id=` query param).

### 4c. Accepted permission expansions (explicit, per DECISIONS.md)

The §3 mapping does not preserve the old permission matrix — it widens it
in both directions, and this is **accepted deliberately**, not an
oversight:

- Old *Designers* could not run/pause/kill; as Organization Users they
  can. Old *Operators* could not create/delete definitions; as
  Organization Users they can. Rationale: the build-vs-run split was never
  exercised and §8 records its return trigger.
- Old *Viewers* could not read the audit log (Admin/Auditor only); as
  Organization Viewers they can — for their org. Rationale: org-scoped
  audit is the transparency the trust wedge sells; the org boundary is now
  the sensitive line, not the audit/dashboard line.

Detection signal: if a customer asks for "viewer without audit access" or
"operator who can't edit," that's the §8 trigger firing, not a defect.

## 5. Migration risks called out

- **The `default` org holds everything today.** Existing definitions,
  instances, and both real users (the dev-JIT rows and the local admin) are
  all in `default`. After S1, a user in `default` with Organization
  Administrator sees exactly what they see today; nothing visibly changes
  until a second org exists. That's the safety property that makes staging
  viable.
- **`require_roles(Role.ADMIN, Role.OPERATOR)`-style call sites** (~20)
  must not be bulk-replaced by mapping alone — each gets the §4 table
  treatment. The dry-run/cost-estimate endpoints in particular currently
  gate on Designer/Admin and need the spend-action lens applied.
- **The engine and orchestrator are org-blind and stay that way.** They run
  definitions loaded from disk under a system identity; org attribution
  flows from the definition rows. Disk-seeded (bundled-example and
  `WORKFLOW_DEFINITIONS_DIR`) definitions land in the **default org** —
  pinned: single-tenant deployments see no change, and a multi-tenant
  deployment's Administrator re-attributes explicitly if an example should
  belong to a tenant. No engine change in any stage.

## 6. Staging

| Stage | Ships | Size |
|---|---|---|
| **S1 — vocabulary + user management scoping** | New `Role` enum + mapping; Alembic `0005` data migration; RoleSwitcher/dev-group updates; `/api/users` org-scoped (Org Admins manage only their org, cannot grant Administrator; last-org-admin guard); `/api/me` reports org + new roles. All other endpoints keep working via the mapping (old checks translated, still globally scoped). | S–M |
| **S2 — resource scoping** | `require_org_access` on the §4 table: list filters + resource-org checks via the §4b joins, cost-report scoping, escalations scoping, event org enrichment + WS filter, `org_bypass` auditing, Administrator explicit-`org_id` create, org-aware veracium namespace (§9). Isolation criteria (§7) all pass. | M–L |
| **S3 — org lifecycle** | `POST /api/organizations` (Administrator), org rename, user-to-org assignment UI, org column in the Users admin page. Invitations stay deferred (need email infrastructure — same deferral as password reset). | S–M |

Each stage is independently shippable; S1 alone delivers the new model with
no behavior change for the single-org deployment.

## 7. Test-pinned isolation criteria

Following the AUTH_PLAN §9 convention — these tests must exist and pass at
the end of S2:

1. A user in org A can never read or list org B's workflows, instances,
   step outputs, audit entries, or cost rows (list endpoints filter; direct
   `/{id}` access 404s — not 403, to avoid existence leaks).
2. A user in org A cannot run, fork, retry, pause, kill, or dry-run an org
   B instance/workflow.
3. An Organization Administrator cannot: see users outside their org,
   create a user in another org, grant Administrator, or demote/deactivate
   their org's last active Organization Administrator.
4. An Organization Viewer gets 403 on every spend/mutation surface in the
   §4 table — including dry-run — and 200 on every read surface, in their
   own org.
5. Administrator cross-org mutations carry `org_bypass: true` in their
   audit detail.
6. The WS event stream never delivers an org B event to an org A
   subscriber; instance-less system events reach Administrators only.
6b. `GET /api/escalations` never lists an org B escalation to an org A
   caller (it walks the audit log, so it inherits the §4b join — pinned
   separately because it predates org thinking entirely).
7. After the `0005` migration, no user row contains an old role string
   (and the Users admin page renders only new-vocabulary options).

## 8. Deferred, with triggers

| Deferred | Trigger |
|---|---|
| Multi-org membership (join table) | First real human who needs two orgs. |
| Distinct Auditor role (audit-only) | First compliance/enterprise ask. |
| Run-vs-edit split inside Organization User | First customer org that separates ops from authoring. |
| Per-org token budgets | Second paying org (until then per-workflow policies suffice). |
| Org deletion + cascade semantics | First org that needs deleting; until then orgs are rename-only. |
| Invitations / email verification | Email infrastructure (same trigger as AUTH_PLAN's password reset). |
| Per-org connectors / secrets namespacing | Second org that needs a connector. |

## 9. Interactions

- **veracium namespace migration** (the decided `user:<user.id>` follow-up)
  should become org-aware in the same move — `org:<org_id>:user:<user_id>`
  or equivalent — so learned memory can never leak across tenants. Do it
  once, in S2, not twice.
- **`AUTH_PLAN` guards compose:** the global last-active-Admin guard
  (shipped) now reads "last active Administrator"; the last-org-admin guard
  is additive per org.
- **`docs/product/` pricing tiers** (seats per org) get their enforcement
  substrate from S2; no product-doc change required now.
