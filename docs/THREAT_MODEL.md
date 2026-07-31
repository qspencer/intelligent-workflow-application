# Trust Boundaries & Threat Model — Current Deployed Surface

Status: **written 2026-07-31** (G20; external review §3 / bundle item 3).
This consolidates the security design that exists **piecewise and
test-pinned** across AUTH_PLAN, ROLES_PLAN, the email-triage plans,
SEMANTICS/COALA, and RELEASE_READINESS into one picture. It describes
the system as built; gaps are named in §8, not hidden.

**Scope (external review §1):** this covers the deployed email/DMARC
workflows and the shared platform surfaces currently implemented
(auth, tenancy, capabilities, memory, audit, WebSocket). Aspirational
architecture and unimplemented connectors — general API/DB/browser/CLI
tools, knowledge ingestion, generated code, multi-node/self-hosted
operation, the model-provider boundary as a first-class adversary
surface — are **out of scope** and each requires its own threat-model
amendment before activation. The deepest analysis (the injection
ladder) is specifically the email surface because that is what mutates
a real asset today.

## 1. Assets

A1 the operator's mailbox (content + label state — the platform can
read and add-only-label it) · A2 credentials (Gmail refresh tokens under
`.secrets/` 0600, AWS keys, local-auth password hashes, session tokens)
· A3 the learned-memory store (distilled personal mail metadata) · A4
Postgres (definitions, run history, audit) · A5 Bedrock spend · A6 the
tenant boundary itself.

## 2. Trust boundaries (data flow, hostile → trusted)

```
 HOSTILE                 SEMI-TRUSTED                     TRUSTED
 ───────                 ────────────                     ───────
 arbitrary senders ──► Gmail API ──► GmailPollTrigger ──► Engine
   (mail bodies,         │            • ordered Auth-Results parse
    headers, names,      │              (first mx.google.com entry only)
    attachments)         │            • body cap, slim payload,
                         │              hostile-filename flattening
                         │            • reply_status / auth_pass annotations
                         ▼
              classifier agent  [tools: [], read-only fence — test-pinned]
                         │  memory: quarantined 3rd-party facts,
                         │  never-assert fence VERBATIM (G10, pinned)
                         ▼
              deterministic gates [enum vocab both axes; apply_labels
                         │         composed ONLY from validated enums]
                         ▼
              apply agent [inputs: minimized — sees label list + msg id
                         │  ONLY; per-account tool; wf/* 8-label
                         │  allowlist; Gmail-side no-create fence]
                         ▼
              Gmail write path  [add-only; removal is operator-CLI-only]

 browser/API clients ──► AuthMiddleware [dev|local|oidc, exclusive role
                         authority] ──► OrgScope on EVERY resource
                         endpoint ──► repos (row-level org key)
 webhook callers     ──► HMAC (X-Hub-Signature-256) when secret_name set
 operator CLIs       ──► same connector code paths; label removal,
                         codify --apply, user bootstrap live HERE only
```

## 3. Adversaries and their reachable surface

| Adversary | Reaches | Wants |
|---|---|---|
| **Hostile email sender** | mail content, headers, attachment names; indirectly the classifier prompt and (via distillation) the memory store | steer labels, gain attention flags, poison memory/codification, exfiltrate via reply (no reply tool exists) |
| **Tenant user** (authenticated, other org) | org-scoped API | cross-org reads, escalation, spend |
| **Network attacker / CSRF** | browser session | ride the cookie |
| **Spoofed "trusted" sender** | codified-sender fast path | rule-credentialed mislabeling with zero content scrutiny |
| **Compromised Administrator** | everything, cross-org, audited-but-permitted | full compromise — **trusted today** (§5a) |
| **Same-org insider** (malicious/careless) | their org's resources | misuse within org scope |
| **Compromised Gmail/OAuth account** | the mailbox + its refresh token | read/label as the operator |
| **Malicious model output** | classifier/attention verdicts | steer decisions within the enum bounds (§8-decision) |
| **Host / DB / backup operator** | disk: secrets, store, Postgres | read everything at rest — **trusted today** (§5a) |
| **Dependency / build-pipeline attacker** | the running code | arbitrary — supply-chain (out of app scope) |
| **Resource-exhaustion sender** | poll/parse/spend/audit volume | DoS + cost burn (§8-resource) |

## 4. The mail-surface injection defense ladder (deepest asset, most layers)

1. Content readers hold **no tools** (classifier + attention steps —
   pinned).
2. Vocabulary gates: category and attention validate against enums;
   hostile strings poison only their own axis; the tool-holder receives
   an **enum-derived label list**, never free text (hostile-payload
   fixtures pinned).
3. Tool-holder input minimization: `inputs:` strips trigger content,
   rubric memory, and recall from the apply step (leak test-pinned;
   token counts asserted).
4. Write-path fences: 8-label allowlist at the tool boundary + Gmail
   no-create (mailbox label list is a physical allowlist) + add-only
   (removal exists only in operator CLIs).
5. Memory: agents have **no memory tools** (COALA N1); the ENGINE
   writes provenance-tagged observations; third-party claims are
   quarantined and rendered under a never-assert fence injected
   verbatim (both criteria test-pinned); recall entities normalized so
   attacker-chosen display names never key anything.
6. Codification: bypass requires DKIM/DMARC-aligned authentication
   (ordered AR parsing defeats attacker-appended headers — test-pinned),
   attention is never bypassed, rule applications never count as
   promotion evidence, corrections disqualify at registrable-domain
   granularity (freemail-exempt), sampling is HMAC-keyed, drift
   disables rules at runtime via the overlay.

## 4a. Two claims kept separate (external review §8)

- **Authority containment (hard invariant, test-pinned):** hostile
  content cannot expand tool authority beyond the fixed validated
  operations — no tool invocation from a reader, no invented labels, no
  mailbox removal. This is the injection ladder's guarantee.
- **Decision robustness (empirical, NOT a hard invariant):** hostile
  content *may* still influence WHICH of the eight allowed labels the
  classifier picks, or push an attention flag. The ladder bounds what
  can happen, not which permitted choice is made. Adversarial
  misclassification / attention-escalation rate is tracked empirically
  (the correction-driven rubric loop + the two-axis part-2 window are
  the measurement), never claimed as pinned.

## 5. Tenant isolation (A6)

Row-level org key (`org_id` on definitions/instances from birth) in
shared Postgres. Every resource endpoint resolves `OrgScope`; cross-org
is **404, never 403** (no existence oracle); audit/steps/cost join
through the instance; WS events carry org from emit time and filter per
subscriber; learned memory is namespaced `org:<org>:user:<key>` and the
transparency surface is org-scoped with cross-org reads audited as
`org_bypass`. **Seven isolation invariants pinned in
`test_org_isolation.py`.** Next assurance step (external review §4): a
generated route-to-scope inventory proving EVERY surface participates
(REST, background jobs, WS emit+subscribe, audit/cost queries, export,
fork/retry, connector creds, admin bypass, deletion/retention) rather
than relying on reviewer memory; Postgres row-level security as
defense-in-depth if the app layer ever misses a filter. Tracked G22. Escalation guards: last-Administrator and
per-org last-org-admin protected; Org Admins cannot touch
Administrators; role authority is exclusive per auth mode (never
merged).

## 5a. Trust assumptions (stated, not implied — external review §5)

**The Administrator role and the host operator are FULLY TRUSTED in the
current deployment.** Administrators reach cross-org resources (audited
as `org_bypass`), instance-less audit is Administrator-only, operator
CLIs perform otherwise-prohibited actions (label removal, codify
`--apply`, user bootstrap), and secrets sit on the same machine as the
service. Consequence, stated plainly: **the current single-operator
deployment does not protect tenants against a malicious host
administrator.** Acceptable while the operator is the sole user and
tenant; a material boundary to close before the first external
organization (§8).

## 6. Authentication & session surface

`dev` (headers; inert outside dev mode — pinned) · `local` (Argon2id
with rehash-on-verify; opaque sessions stored **hashed**, revocation =
row delete; HttpOnly SameSite=Lax cookie; CSRF Origin check on cookie'd
non-GETs AND WS upgrades; enumeration-resistant login with dummy-verify
timing; rate limits per IP + per email) · `oidc` (JWKS-validated JWT;
IdP is sole authority — D4). Fifteen pinned security tests in
`test_auth_local.py`.

## 7. Capabilities, secrets, spend

- Capability model: system ∩ workflow ∩ step ∩ runtime — narrowest
  wins; enforced at Agent dispatch AND inside tools (a tool absent from
  the intersection cannot be called; file tools check the capability's
  path scope internally).
- Secrets: `.secrets/**` 0600 + gitignored; `SecretStore` abstraction
  (env / AWS Secrets Manager); tokens never logged or stored raw
  (session tokens hashed; pinned). Bedrock spend is bounded per step,
  per workflow (budget actions), and observable per run/model/day; the
  sampling HMAC key comes from the environment, not the repo.
- Audit: every transition, tool call, capability denial, org bypass,
  budget event, memory write/recall/introspection. Org-scoped reads;
  instance-less audit is Administrator-only.

## 8. Known gaps — named, with dispositions

*Each row's disposition names the compensating control (where one
exists) and the revisit trigger. External review §10 asks for full
governance columns (severity / owner / acceptance date / required
pre-trigger action / monitoring signal) — for a single-operator project
the owner is the operator and the acceptance date is this doc's date;
the governance-table expansion becomes real at the first external org.*

| Gap | Disposition |
|---|---|
| Audit is append-only at the app layer, **not tamper-evident** (no hash chain/WORM) | Accepted for single-operator; trigger: first compliance-bound customer (RELEASE_READINESS names it) |
| Retries not engine-gated on `Tool.effect` | Authoring rule in EXECUTION_SEMANTICS §4; trigger: second mutating connector |
| Schemathesis fuzzes GET only | TESTING.md roadmap item (widen to mutating verbs) |
| Webhook HMAC | **Now ENFORCED**: outside dev mode the orchestrator refuses to register an unsigned webhook trigger (test-pinned). Remaining follow-ups: min-entropy secret check, timestamp/nonce replay defense, constant-time compare (httpx hmac already), rotation overlap, signature-failure rate-limit |
| Secrets on local disk (0600) — an operational safeguard, NOT a secrets boundary | **Trigger corrected to first external ORGANIZATION** (external review §6, was "compliance-bound customer"): external secret manager, separate service identities, short-lived creds, encrypted backups, OAuth/HMAC rotation, credential-scrubbed debug dumps |
| Single-box deployment (DB, service co-located) | Solo-dev posture; `infra/` exists unapplied; revisit at first external user |
| Resource exhaustion (poll volume, MIME/attachment size, parser CPU, Bedrock spend, repeated invocation, audit/DB growth, alert flooding) | Partial today (body cap, per-workflow token budget, monitoring's token-burn + queue-depth alerts); **follow-up:** per-sender/per-account throttles, max-decoded-size, parser timeouts, an explicit cost circuit-breaker |
| Generated-code execution | **No such feature exists** — the ARCHITECTURE passage is aspirational; the shipped analogue (codified rules) is human-approved + self-disabling |
| Full-trace views expose raw prompts (mail content) | **Until redaction exists: default OFF, separate privilege from ordinary admin, every view audited, exports restricted, short retention, auth headers stripped before storage** (external review §7 — role-gating alone doesn't address insider/privacy risk) |

## 9. Security test matrix (where the pins live)

`test_auth_local.py` (15) · `test_org_isolation.py` (7 invariants) ·
`test_email_triage_apply.py` (hostile payload, minimization, fences) ·
`test_auth_results.py` (8, incl. attacker-appended AR) ·
`test_codified_runtime.py` (auth gate, overlay, fail-open) ·
`test_learned_memory.py` (verbatim fence, quarantine, normalization) ·
`test_memory_transparency.py` (scoping, no-leak, audit) — plus the
schemathesis contract suite and the weekly live job.
