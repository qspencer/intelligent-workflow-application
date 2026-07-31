# External Review — Requested Documentation Bundle

Prepared 2026-07-31 in response to the external review of `VISION.md` +
`ARCHITECTURE.md`. The reviewer read those two documents only; most of
what they requested already exists in the repo. This index maps each
request to the artifacts that answer it, and names the two documents
that genuinely did not exist (now tracked as G19/G20 in
`docs/NEXT_STEPS.md`).

Reviewer's note that mattered most: ARCHITECTURE.md mixed current and
aspirational statements. Two known-stale passages were corrected in this
pass (multi-tenancy enforcement — S2/S3 shipped 2026-07-18 with seven
test-pinned isolation invariants; "complete chain-of-thought" reworded to
what is actually recorded).

## 1. Current-state implementation map
- `CLAUDE.md` — the living status document: phase-by-phase what landed,
  with test counts per milestone; the design-docs table marks every plan
  **Proposed / Built / Built+cut-over** with dates.
- `docs/BUILD_PLAN.md` — sequencing + gates; `docs/NEXT_STEPS.md` — the
  single backlog (G-numbered, triggers named, Done items point at the
  authoritative plan-doc status paragraphs).
- Complete workflows that run in production today: `examples/
  email_triage_apply/` (agentic, mutating, live on a real mailbox with
  codified-sender routing), `examples/dmarc_ingest/` (fully
  deterministic, zero LLM tokens). `git log --oneline` is the honest
  fine-grained record.

## 2. Workflow engine and state model
- Exists in code + tests: `backend/src/workflow_platform/engine/executor.py`
  (state machines, parallel dispatch, conditional edges + skip
  propagation, retries/timeouts, pause/resume/kill/fork), Alembic
  migrations for the entity model, `docs/EMAIL_TRIAGE_ACT_PLAN.md` §6b
  (partial-failure semantics for the acting path).
- **`docs/EXECUTION_SEMANTICS.md` (written 2026-07-31)** — the formal
  contract: state machines, per-trigger delivery guarantees, the
  at-least-once consequence, retry rules, budgets, crash recovery,
  fork lineage, and an explicit not-provided list.
- Historical note (G19 was the gap): a formal execution-semantics document
  (delivery guarantees, idempotency, retry classification,
  compensation, cancellation, version/fork lineage). The reviewer is
  right that "retry once" without side-effect classification is unsafe;
  today's mitigations are per-step `retries` defaulting to 0 and the
  `Tool.effect` classification, but the contract is not written down.

## 3. Security and threat model
- Exists piecewise: capability intersection (enforced in Agent dispatch
  AND inside tools), `docs/AUTH_PLAN.md` (test-pinned security
  acceptance criteria), `docs/ROLES_PLAN.md` (tenant isolation, seven
  pinned invariants), `docs/EMAIL_TRIAGE_ACT_PLAN.md` +
  `EMAIL_TRIAGE_CODIFY_PLAN.md` (prompt-injection threat model for the
  mail surface: enum gates, input minimization, add-only label fence,
  DKIM/DMARC-gated codification, ordered Authentication-Results
  parsing), `docs/SEMANTICS.md` + `COALA_NOTES.md` (memory-injection
  posture), `docs/RELEASE_READINESS.md` (self-named gaps).
- **`docs/THREAT_MODEL.md` (written 2026-07-31)** — the single picture:
  assets, trust-boundary data flow, adversaries, the mail-surface
  injection defense ladder, tenant isolation, auth surface, known gaps
  with dispositions, and the security test matrix.

## 4. Tool and connector contract
- `backend/src/workflow_platform/tools/base.py` (Tool ABC: schema,
  capability check, `effect` side-effect classification),
  `docs/INTEGRATIONS.md` (connector strategy + six-method ABC),
  `workflow_platform/catalog.py` + `GET /api/catalog` (live inventory —
  nothing unwired is offered). Mock-vs-real equivalence: MockWorld +
  fake Gmail service used across 880+ tests; per-connector conformance
  suites are future work (reviewer's §12 point accepted).

## 5. Product and delivery plan
- `docs/product/` (opportunity memo, product spec, competitive
  landscape incl. Pega deep-dive, gap analysis) — explicitly proposals,
  not adopted policy; `docs/USE_CASES.md` (validation workloads and why
  each came first); `docs/CANVAS_ROADMAP.md` (the dashboard → friendly
  GUI → conversational sequence, C1–C8 shipped).
- v1-scope honesty: what actually shipped IS the reviewer's recommended
  narrow product — document/email-triggered workflows on one connector
  family with human-gated mutation.

## 6. Generated-code specification
- **No such feature exists.** The ARCHITECTURE passage is aspirational
  and predates the build; nothing generates, deploys, or rewrites code.
  The shipped counterpart is the codification loop
  (`docs/EMAIL_TRIAGE_CODIFY_PLAN.md`): evidence-gated promotion of
  stable agent behavior to a deterministic rule with a human approval
  step (operator CLI `--apply`), shadow sampling, immediate runtime
  rollback (disable overlay), and version binding — i.e., the
  reviewer's "safer lifecycle," built for rules rather than code.

## 7. Memory and learning specification
- `docs/SEMANTICS.md` (adoption trigger log), `docs/COALA_NOTES.md`
  (N1: no agent-facing memory tools — the reviewer's reconciliation
  guess is exactly right: the PLATFORM writes engine-rendered,
  provenance-tagged observations; agents never hold a memory tool),
  `docs/EMAIL_TRIAGE_ACT_PLAN.md` + two-axis plan (observation
  templates, `third_party` quarantine, never-assert fence test-pinned),
  `docs/VERACIUM_041_ADOPTION_PLAN.md` (inspect/delete story: the
  org-scoped, audited memory transparency surface, live).
- Learning currently produces evidence and recommendations; the one
  autonomous consumer (codified sender rules) is human-approved at
  promotion and self-disabling on contrary evidence.

## 8. Testing evidence
- `docs/TESTING.md` (roadmap + inventory), `docs/MANUAL_TESTING.md`
  (operator playbook), CI: ~885 backend unit/replay tests, ~197
  frontend, Playwright+axe e2e, schemathesis contract suite on every
  GET, weekly live Bedrock+Gmail+browser job; Postgres/Bedrock/Gmail
  suites deselect by default (replay-mode by design — no AWS in CI).

## Addendum (2026-07-31): "production," operationally defined

Per the reviewer's follow-up — what "production today" means here,
stated so the assurance level is not oversold:

- **Users:** one — the platform operator, on his personal Gmail
  mailbox. No external users, no multi-organization service, no SLOs.
- **Environment:** a single EC2 box; systemd user service; shared
  Postgres; secrets on local disk (0600); the Terraform in `infra/`
  is written but unapplied.
- **Duration & volume:** acting (mutating) email triage live since
  2026-07-22, full-mailbox coverage since 2026-07-26 — 400+ runs, a
  Gmail label mutation on essentially every categorized message;
  codified-sender routing live since 2026-07-30 (7 senders, 1-in-5
  shadow sampling). DMARC ingest deterministic pipeline since
  2026-07-13.
- **Human intervention:** operator corrections are the learning input
  (one taxonomy-changing correction to date plus the historical
  labeling sessions); escalation volume otherwise zero.
- **Failure history (honest):** three defects found and fixed on the
  acting pipeline's first live day; a token-budget pause wave on
  full-coverage day one (fixed same day); one Gmail token revocation
  outage (~40 min, cursor-backfilled, zero loss); one silent trigger
  blindness (DMARC label filter, 10 days, zero data loss — backfilled;
  produced the `alert_stale_trigger` monitor).
- **Data sensitivity:** production data IS sensitive third-party
  content (the operator's real mail). This is why the corpus is
  metadata-only and gitignored, memory quarantines third-party claims,
  and the review artifacts below are redacted.

This is single-operator production: real stakes, real mutations, real
third-party adversarial content — but not multi-tenant service
assurance. The tenant-isolation machinery is built and test-pinned
ahead of that need, not proven by it.

## Addendum: next-bundle artifacts

The reviewer's six requested items map to: (1) `CLAUDE.md` +
`docs/BUILD_PLAN.md` (in-repo); (2) `backend/src/workflow_platform/
engine/executor.py` + `backend/tests/test_engine*.py` +
`backend/alembic/` — reviewed against `docs/EXECUTION_SEMANTICS.md`
(now written); (3) `backend/src/workflow_platform/tools/base.py`,
`security/` capability resolution, `tools/email.py`
(`EmailLabelApplyTool` — the mutating Gmail tool with allowlist
enforcement); (4) `docs/ROLES_PLAN.md` +
`backend/tests/test_org_isolation.py`; (5) the two email-triage plans
(in-repo); (6) a sanitized real run artifact:
`docs/review-artifacts/sample-run.md` (generated from a live instance,
mail content redacted).
