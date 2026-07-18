# Basic Authentication & Role System — Design

Status: **proposed** (design-reviewed 2026-07-18: adopt-with-conditions;
all review findings folded in below; not yet built).
Companion to `docs/ARCHITECTURE.md` D4 — this document proposes a scoped
amendment to D4, stated explicitly in §3.

## 1. Problem

The platform has exactly two authentication modes today:

- **`oidc`** — validates a Bearer JWT against a corporate IdP's JWKS.
  Production-shaped, but requires an IdP that doesn't exist in any current
  deployment (solo-dev posture; no Okta/Keycloak/Azure AD anywhere).
- **`dev`** — trusts `X-Dev-User` / `X-Dev-Groups` headers. Spoofable by
  design; for local development and tests only.

Consequences:

1. There is **no way to actually log in** to a deployed instance. The moment
   the backend leaves localhost (the Terraform in `infra/`, or any demo), the
   only choices are "no auth" (dev mode on a network) or "stand up a corporate
   IdP" — disproportionate for a single-operator or small-team deployment.
2. **Roles only exist as IdP group mappings** (`OIDC_GROUP_TO_ROLE`). Without
   an IdP there is no durable role assignment — the dashboard's RoleSwitcher
   literally lets the browser pick its own privileges.
3. The users/orgs skeleton (Alembic `0003`) gives every feature a stable user
   id, but nothing authenticates *as* one of those users except via the modes
   above.
4. The product spec (`docs/product/PRODUCT_SPECIFICATION.md`) prices
   Starter/Pro/Team tiers with per-seat counts and positions SSO as a
   Pro-and-up feature — implying a first-party login path for the base tier.

"Basic authentication and role system" therefore means: a self-contained way
to sign in and durable, admin-managed role assignments — without breaking the
enterprise OIDC path or the local dev loop.

## 2. The central decision

D4 currently says: *"The platform does NOT manage passwords. It delegates
authentication entirely to the configured IdP."* Any design here must either
work within that or amend it. Three options:

### Option A — first-party local auth (recommended)

A third `AUTH_MODE=local`: email + password against the existing `users`
table, server-side sessions, roles persisted per-user in the DB.

- **For:** matches the product spec's self-serve base tier; smallest
  operational footprint (no new infrastructure); user management lives inside
  the product, where a non-technical admin can see it; the users/orgs
  skeleton was built precisely so features like this have a place to land.
- **Against:** the platform now holds credentials (password hashes) —
  a real scope increase in security responsibility. Mitigated by keeping the
  surface minimal (§6) and by the fact that `oidc` mode remains the
  enterprise answer.

### Option B — embed a lightweight IdP (Keycloak/Authentik/Dex in compose)

Keep D4 intact; the platform still only speaks OIDC; add an IdP container.

- **For:** zero credential-handling code in our tree; exercises the
  production `oidc` path everywhere.
- **Against:** heaviest operational footprint (a JVM service with its own
  admin console, realm config, upgrade cadence) for "basic" auth; user/role
  management moves *outside* the product UI, which is wrong for the
  non-technical-operator direction the canvas is taking; still requires all
  the frontend login/session work, now against a third-party's flows.

### Option C — API keys only

Skip interactive login; issue per-user bearer keys via CLI.

- **For:** trivially simple; useful regardless (service-to-service is already
  in D4's authentication table).
- **Against:** not a human login system — no dashboard story beyond "paste a
  key into localStorage", which is the dev-mode problem wearing a hat.

**Recommendation: Option A**, with Option C's API keys noted as a separate,
deferred follow-up (trigger: first CI/service integration that needs to call
the API unattended, beyond the already-HMAC'd webhook endpoints).

## 3. D4 amendment (proposed text)

> **Amendment (2026-07-18):** `AUTH_MODE=local` adds first-party email +
> password authentication for self-hosted and small-team deployments. This
> narrows, but does not reverse, D4's delegation posture: **in `oidc` mode
> the IdP remains the sole authority** for authentication and roles, and
> enterprise deployments should use it. Local mode exists so the base tier
> is deployable without corporate identity infrastructure. Credential
> storage is limited to an Argon2id hash on the `users` row (nullable —
> SSO-only users never gain one); the session store holds only hashed
> opaque tokens. Nothing else credential-shaped is persisted.

## 4. Data model (Alembic `0004`)

Extends the `0003` tables; no new conceptual entities beyond sessions.

```
users                              (existing, + new columns)
  password_hash   TEXT NULL        -- Argon2id PHC string; NULL = no local login
  roles           JSONB NOT NULL DEFAULT '[]'
                                   -- DB-assigned roles, e.g. ["Admin"]
  is_active       BOOLEAN NOT NULL DEFAULT true
                                   -- soft disable; inactive users cannot log in
                                   -- and their sessions stop validating

  -- New partial unique index: login is by email, but 0003 left email
  -- nullable and non-unique (fine for JIT-provisioned SSO users, fatal for
  -- login). Local-credentialed users must be unique by canonical email:
  CREATE UNIQUE INDEX uq_users_login_email
      ON users (lower(email)) WHERE password_hash IS NOT NULL;
                                   -- emails are lowercased on create/login

sessions                           (new)
  id              UUID PK
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
  token_hash      TEXT NOT NULL UNIQUE   -- sha256 of the opaque token
  created_at      TIMESTAMPTZ NOT NULL
  expires_at      TIMESTAMPTZ NOT NULL
  last_seen_at    TIMESTAMPTZ NOT NULL   -- TTL-throttled update, same
                                         -- pattern as users.last_seen_at
```

Notes:

- **Local users' identity key.** Local users are rows with
  `iss = "local"`, `sub = <user uuid>` — the `(iss, sub)` uniqueness from
  `0003` is untouched, JIT-provisioned OIDC users and local users coexist,
  and audit `actor_id` (the raw sub) stays stable even if an email changes.
- **`roles` lives on the user row**, not a join table. Five fixed roles,
  read on every request, no per-role metadata — a `role_assignments` table
  is structure without a query that wants it (three similar lines beat a
  wrong abstraction). Revisit if per-org roles arrive with multi-tenancy.
- **Sessions are opaque tokens, not JWTs.** `secrets.token_urlsafe(32)`,
  stored **hashed** (a DB read never yields a usable credential). Opaque
  beats stateless here because revocation (logout, user deactivation) must
  actually work, and one indexed lookup per request is nothing at our scale.

## 5. Authentication flow

### Endpoints (all under the existing FastAPI app)

| Endpoint | Auth | Behavior |
|---|---|---|
| `POST /api/auth/login` | none (rate-limited) | body `{email, password}`. Verify Argon2id; create session; set cookie. Identical 401 body for unknown email / wrong password / inactive user (no enumeration). Audits `auth_login` / `auth_login_failed`. |
| `POST /api/auth/logout` | session | Delete the session row, clear the cookie. Audits `auth_logout`. |
| `GET /api/me` | any mode | Already exists; unchanged contract. In local mode, `identity` derives from the session's user row. |

### Cookie + middleware

- Cookie: `wp_session=<token>`; `HttpOnly; SameSite=Lax; Path=/`; `Secure`
  whenever the request scheme is https (always set in production). Behind
  the `infra/` ALB the app terminates TLS upstream, so uvicorn must run
  with `--proxy-headers` (honor `X-Forwarded-Proto`) or the scheme check
  never sees https. Max-Age = session lifetime (default 7 days, env
  `AUTH_SESSION_TTL_HOURS`).
- `AuthMiddleware` grows a third branch: in `local` mode, read the cookie,
  hash it, look up the session (valid, unexpired, user active), and populate
  `request.state.user` as a `UserIdentity` with `roles` from the user row.
  Unknown/expired session → 401, same shape as the other modes.
- **JIT provisioning is skipped in local mode.** The middleware currently
  calls `provisioner.provision()` unconditionally, and
  `provisioning.current_issuer()` only knows `dev`/`oidc` — left as-is,
  every local login would mint a duplicate `(iss="oidc", sub=<uuid>)` row.
  In local mode the session lookup *is* the identity (the user row already
  exists); the provisioner must not run. `current_issuer()` learns the
  third mode as a guard.
- Local-mode identity headers are inert: `X-Dev-User` / `X-Dev-Groups` are
  ignored entirely when `AUTH_MODE=local` (test-pinned, §9.5).
- `UNAUTHENTICATED_PATHS` gains `/api/auth/login` only. Everything else keeps
  the existing posture (webhooks keep their HMAC path; `/metrics` stays open).
- **WebSocket:** `/ws/events` currently reads dev headers or an OIDC
  `?token=`. In local mode the browser sends the cookie on the upgrade
  request automatically — validate it exactly like the middleware does. This
  *removes* the need for the token-in-query-string pattern in local mode
  (query strings leak into logs; the cookie path is strictly better).
  **The upgrade request is a GET**, so the §CSRF non-GET origin rule never
  fires for it — the WS accept path gets its own explicit check: reject the
  upgrade when `Origin` is present and doesn't match the request host
  (classic cross-site WebSocket hijacking defense; test-pinned, §9.7).

### CSRF

Cookie auth introduces CSRF exposure that Bearer/header auth didn't have.
`SameSite=Lax` blocks cross-site POSTs from forms/fetch in modern browsers;
belt-and-suspenders: on non-GET requests authenticated by cookie, require
that the `Origin` header (when present) matches the request host, else 403.
No token dance — proportionate to "basic", and the SPA is same-origin (Vite
proxies `/api` in dev; same host in production).

### Rate limiting + audit

- In-process sliding window on `POST /api/auth/login`: max 10 attempts per
  (client IP × 15 min) **and** per (email × 15 min); exceeding either → 429
  with `Retry-After`. In-memory is acceptable at current scale (single
  process); note in code that multi-process deployment moves this to the DB.
- Every login success/failure/logout lands in the audit log (existing
  `AuditEntry` machinery). Successes/logouts: `actor_type="user"`,
  `actor_id=<sub>` as everywhere else. Failures are unauthenticated, so
  `actor_type="anonymous"`, `actor_id="login"`, with the *attempted* email
  and source IP in `detail` — operator-facing security signal, and the
  audit-log convention (actor_id never dangles) is preserved because no
  user id is claimed.

### Password hashing

`argon2-cffi`, Argon2id, library defaults (time=3, 64 MiB, parallelism=4 as
of 25.x). Verification runs in `asyncio.to_thread` (it's CPU-bound by
design — never block the event loop). On login with a legacy-parameter hash,
re-hash and update (argon2-cffi's `check_needs_rehash`) so parameter
upgrades roll forward automatically.

## 6. Role system

### Source of truth by mode

| Mode | Roles come from | Notes |
|---|---|---|
| `oidc` | IdP groups via `OIDC_GROUP_TO_ROLE` (unchanged) | D4 posture: no local role editing for SSO users. If the IdP sends no mappable groups, the user has no roles — same as today. |
| `local` | `users.roles` | Admin-managed via the API below. |
| `dev` | `X-Dev-Groups` headers (unchanged) | Tests and the RoleSwitcher keep working untouched. |

No merging across sources — each mode has exactly one role authority.
Precedence rules between IdP and DB roles are a multi-tenancy problem;
deliberately out of scope (same deferral as `0003`'s enforcement).

### Management API (local mode; all Admin-gated)

| Endpoint | Behavior |
|---|---|
| `GET /api/users` | List users (id, email, display_name, roles, is_active, last_seen). |
| `POST /api/users` | Create a local user: `{email, display_name?, password, roles}`. |
| `PATCH /api/users/{id}` | Update `roles`, `is_active`, `display_name`, or `password`. Deactivating or re-roling deletes the user's sessions (changes take effect immediately, not at next login). |

Guard: an admin cannot remove their own `Admin` role or deactivate
themselves while they are the **last active Admin** — checked in the
endpoint, covered by a test. (Lockout via SQL remains possible; the
bootstrap CLI below is also the recovery path.)

### Bootstrap

`backend/tools/create_user.py` — CLI that creates/updates a local user
(prompts for password, `--roles Admin`). Runs against `DATABASE_URL`
directly, so it works before the first login exists and doubles as the
break-glass recovery path. No "first user is admin" magic in the API — a
deliberate, auditable operator action instead.

### Frontend

- **Login page** at `/login`: email + password, error display, redirect to
  the original route after success. Rendered when any API call returns 401
  in local mode (the API client already funnels errors; add a 401 →
  navigate-to-login hook). Uses the a11y form checklist from
  `docs/UI_POLISH_AND_A11Y.md` (label↔input, `aria-invalid`,
  `role="alert"`).
- **UserChip** grows a menu with "Sign out" (calls logout, returns to
  `/login`). No change in `dev`/`oidc` modes.
- **Users admin page** (behind the Developer toggle initially): table +
  create/edit dialogs over the management API. Reuses the dialog patterns
  from Import/Run.
- **RoleSwitcher stays dev-only** — it renders only when the backend reports
  `auth_mode: "dev"` (add the mode to `/api/me`), because in local mode
  picking your own role must be impossible.

## 7. Explicitly out of scope (deferred, with triggers)

| Deferred | Trigger to build |
|---|---|
| Password reset / email verification | First deployment with a second human user. (The Gmail connector could send resets, but coupling auth to a connector account is wrong — needs a platform mail identity. Until then: admin resets via `PATCH /api/users/{id}` or the CLI.) |
| API keys (Option C) | First unattended service-to-service consumer beyond webhooks. |
| MFA / TOTP | A deployment that holds data warranting it; SSO (already supported) is the enterprise answer. |
| SAML | First enterprise ask; D4 already names it. |
| Per-org roles, invitations, query scoping | Multi-tenancy pull — same deferral as `0003`. |
| Self-serve signup | A real SaaS deployment; local mode is admin-provisioned by design until then. |
| Session UI (list/revoke devices) | Post-basic; deactivation already mass-revokes. |

## 8. Build plan (est. 3–4 days)

| Day | Slice |
|---|---|
| **B1** | Migration `0004` + repo methods (users: password/roles/active; sessions CRUD) on both repo impls; `argon2-cffi` dep; hashing helpers with rehash-on-verify. Unit tests. |
| **B2** | `AUTH_MODE=local` middleware branch (provisioner skipped; dev headers inert) + login/logout endpoints + rate limiter + CSRF origin check + audit entries + `/api/me` mode field + WS cookie auth with upgrade Origin check. `create_user.py` CLI. Tests: session lifecycle, enumeration-resistance (identical 401s), rate-limit 429, revocation-on-deactivate, last-admin guard, WS cookie + Origin paths, no-duplicate-provisioning. |
| **B3** | User management API + frontend: login page, 401 redirect, UserChip sign-out, users admin page, RoleSwitcher gating by reported mode, `lib/auth.ts` + `EventsService` updated for cookie auth (no dev headers / no `?token=` in local mode). Vitest + one Playwright spec (login → land on home → sign out) with axe on `/login`. |

Rollout: `dev` stays the default for local development and tests
(`run-local-be.sh` unchanged); `local` becomes the documented mode for any
network-reachable deployment (`infra/` notes updated); `oidc` unchanged.

## 9. Test-pinned security acceptance criteria

Mirroring the G10 convention — these are the tests that must exist and pass:

1. Login failures are indistinguishable (unknown email vs wrong password vs
   inactive user: same status, same body, comparable timing via a dummy
   verify on the unknown-email path).
2. A logged-out or deactivated user's session token stops working on the
   very next request (revocation is immediate, not TTL-bound).
3. Session tokens never appear in logs, audit entries, or API responses
   after `Set-Cookie`; only hashes are stored.
4. A cross-origin non-GET with a valid session cookie is rejected (CSRF
   origin check).
5. `dev`-mode headers are ignored when `AUTH_MODE=local` (no privilege path
   via spoofed headers on a deployed instance).
6. The last active Admin cannot remove or deactivate themselves.
7. A `/ws/events` upgrade with a valid session cookie but a mismatched
   `Origin` header is rejected (cross-site WebSocket hijacking defense).
8. Logging in twice as the same local user creates no duplicate user rows
   (the JIT provisioner does not run in local mode).
