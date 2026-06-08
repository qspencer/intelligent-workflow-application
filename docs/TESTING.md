# Test-suite roadmap

Where the automated test suite is today and where it goes next. This is the
*forward-looking* companion to the testing material already in the corpus —
don't duplicate those, link to them:

| Doc | Role |
|---|---|
| `docs/ARCHITECTURE.md` §D11 + "TDD by Layer" | The **philosophy** — how each layer is tested (unit / replay / contract) and why. |
| `docs/MANUAL_TESTING.md` | The **by-hand** playbook (Part A API, Part B canvas GUI) — what a human verifies on a checkout. |
| `docs/NEXT_STEPS.md` | The **product-gap** backlog (P0–P2 + G-items); some bleed into testing (e.g. G1 recordings). |
| **this doc** | The **test-suite roadmap** — planned automated-testing investments, prioritized, with triggers. Fulfils ARCHITECTURE's "further testing strategies … to be designed later." |

Items here are *plans*, not commitments. Pull one in when its trigger fires or
it's the highest-leverage thing left; delete it when done (move a one-line note
to the relevant doc's status).

---

## Current state (snapshot)

Keep this honest; it's the baseline the roadmap improves on.

| Layer | Where | Notes |
|---|---|---|
| Backend unit | `backend/tests/` (~685 tests) | pytest; `BEDROCK_MODE=replay` default (no accidental AWS); in-memory repos; `FakeBedrock` + `MockWorld`. |
| Engine / security / cost | unit | Deterministic — DAG order, parallel spawn, conditional-edge skip, pause/resume/retry, capability intersection, cost math. No LLM. |
| Agent decisions | replay tests | Record a real Bedrock response once, replay deterministically. |
| Contract / fuzz | `test_schema_conformance.py` (`schema` marker) | Schemathesis over the OpenAPI **GET** endpoints; `not_a_server_error`. Opt-in `SCHEMA_TESTS=1`; PR gate (own CI job). |
| Integration | `integration` marker | Postgres round-trip. PR gate (CI Postgres service). |
| Live (drift) | `live` / `gmail_live` / `browser_live` | Real Bedrock / Gmail / Chromium. Weekly cron, not a PR gate. |
| Frontend | `frontend/src/**/*.test.tsx` (~146 tests) | Vitest + Testing Library + jsdom; behavior, not pixels. `vite build` typechecks. |
| E2E / a11y | `frontend/e2e/*.spec.ts` (Playwright + `@axe-core/playwright`) | Real-browser canvas flows (create → edit → save → delete) + axe scans on home/templates/canvas. Self-contained webServer (in-memory backend, replay Bedrock, no triggers). PR gate (own CI `e2e` job). |
| Migrations | CI | Alembic upgrade → downgrade → upgrade sanity. |
| Supply chain | CI | `pip-audit` (backend) + `npm audit` scoped to prod deps (frontend). |

Gaps the roadmap targets: **no coverage floor** (measured, not gated);
**schemathesis is GET-only / server-error-only**; **TS types hand-mirror
Pydantic** (drift risk); **no property-based or mutation testing** on the
security-critical core; **PDF-classifier replay path isn't exercised in CI**
(non-portable recordings). (Browser-level E2E + a11y — formerly the top gap —
shipped as T4 below.)

---

## Planned improvements

### Tier 1 — high leverage, low effort

**T1. Widen schemathesis beyond GET / server-errors.**
Add `response_schema_conformance` (+ `status_code_conformance`) and extend to the
safe POSTs (`/workflows/validate`) once endpoint *response* models are fully
annotated in the OpenAPI schema. Catches response drift, not just crashes.
*Trigger:* response models annotated enough that documented schemas are complete
(otherwise the stricter checks flag every undocumented 404/422 as noise).

**T2. Coverage floor.**
CI already runs `pytest --cov` but enforces nothing. Read the current
`term-missing` number, pick a realistic floor, add `--cov-fail-under=N` so
coverage can't silently regress. Start lenient; ratchet up.
*Trigger:* now — one-line CI change once a baseline is chosen.

**T3. Generated frontend types from OpenAPI.**
`frontend/src/types.ts` hand-mirrors the Pydantic models; they drift silently.
Generate types with `openapi-typescript` and add a CI check that the committed
types match a fresh generation. Turns a whole class of contract bugs into a diff.
*Trigger:* now — small tooling add; pairs naturally with T1.

### Tier 2 — real coverage gaps

**T4. Playwright E2E for the canvas. — ✅ shipped (with C8.2).**
`frontend/e2e/` covers create → edit → save (the "Saved ✓" path) → delete in a
real browser, plus `@axe-core/playwright` scans on home / templates / canvas
(serious+critical gate; the first run caught a `mode-pill` contrast bug, since
fixed). Self-contained webServer (in-memory backend, replay Bedrock, no
triggers); PR gate via the CI `e2e` job. *Remaining to deepen later:* the
run/dry-run/live-status legs (need a fixtured Bedrock recording or a stubbed
run), and broader axe coverage of the live-run + validation-error states.

**T5. Property-based tests for the engine + security core.**
Use hypothesis directly (not just via schemathesis) on the deterministic invariants:
topological-order validity for arbitrary DAGs, skip-propagation for arbitrary
conditional-edge graphs, and capability intersection (most-restrictive-wins) over
arbitrary layer stacks. These are pure functions with strong invariants — ideal
property targets, and the security ones are worth the rigor. Fulfils the
ARCHITECTURE note. *Trigger:* now for capability/DAG; high value-per-effort.

**T6. Portable PDF-classifier recordings (NEXT_STEPS G1).**
The classifier's replay path isn't in CI because its request hash includes
absolute file paths. Normalize paths before hashing (or fixture a stable path) so
a committed recording replays anywhere, then add it to the default suite.
*Trigger:* when touching the classifier or the Bedrock hash logic.

### Tier 3 — confidence multipliers (larger effort)

**T7. Mutation testing on the security-critical core.**
Run `mutmut` (or cosmic-ray) scoped to `security/` (capability intersection),
`workflow/topology.py` + `workflow/validation.py` (DAG / validation), and the
conditional-edge evaluator. Measures whether the tests actually *catch* breakage,
not just execute lines. Scope tight — mutation testing is slow; the payoff is
highest exactly where a silent regression is most dangerous.
*Trigger:* once T2/T5 land (mutation testing rewards a strong existing suite).

**T8. Stateful API testing.**
Schemathesis state-machine mode over endpoint *sequences* (create → run → fetch →
pause → resume → kill), driven by OpenAPI links. Catches state-transition bugs
single-request fuzzing can't. *Trigger:* after T1; needs OpenAPI links declared.

**T9. Engine throughput / perf guard.**
A lightweight benchmark on parallel-DAG execution with a wall-clock ceiling, so a
concurrency regression fails loudly. *Trigger:* if/when parallel execution is
reworked, or a perf complaint surfaces.

### Cross-cutting hygiene

- **Determinism:** pin hypothesis/schemathesis seeds + deadlines in CI; record
  seeds on failure so a flake is reproducible.
- **Live-suite health:** the weekly cron is the canary for provider drift — treat
  a red cron as a real signal, not noise. Consider a status badge.
- **Live workload validation:** run `email_triage` against real mail (same
  rubric-iteration loop as PR-/paper-triage) to validate the example end-to-end.

---

## Explicitly deferred (and why)

- **Full conversational-agent eval harness.** Research-gated (see
  `docs/LEARNING_IMPLEMENTATION.md` open questions). The LLM-as-judge loop on the
  PDF classifier (`RAG_PRODUCTION_NOTES.md` R1) covers the current need.
- **Visual-regression screenshots.** Brittle and high-maintenance for a solo dev;
  component tests + `axe` + the manual Part B walkthrough cover it. Revisit only
  if a real visual regression slips through repeatedly.
- **Load / soak testing of the deployed stack.** Blocked on the stack actually
  being deployed (NEXT_STEPS P2.3); premature until then.

---

## When this doc goes stale

- A planned item ships → delete it here, leave a one-line note in the owning doc.
- A new test layer / marker is added → add a row to **Current state**.
- The "gaps the roadmap targets" line stops being true → fix it (it's the honest
  baseline the whole doc rests on).
