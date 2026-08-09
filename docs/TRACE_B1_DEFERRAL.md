# Trace Contract B1 — deferred (2026-08-09), and what building it right requires

**Decision:** stop active work on Contract B1 (zero-raw-at-rest / DB-operator
resistance). **Trigger to resume:** the first real external tenant — i.e. when
someone other than the sole operator can reach the operational store or its
audit trail. Until then, B1 is **explicitly not claimed**.

**Why now.** Six external code reviews of the projector (the linchpin of both
Contract A read-gating and Contract B1 at-rest) each reproduced real P0/P1 leaks.
The through-line is not a run of bugs — it is **architectural**, and the sixth
review named it exactly: the projector validates by *shape* (a token regex), and
a token-shaped secret is indistinguishable from a safe token by shape alone. That
cannot be closed by more shape-validation; it needs **provenance** (a value's
source), which is unbuilt. Against a boundary no tenant currently crosses, the
right call is to bank the checkpoint and stop, not to run a seventh patch round.

This document is the come-back artifact: the honest current posture, what is
already built and must NOT be rebuilt, and the architecture a correct B1 needs.

---

## 1. Current live posture (honest)

- **The flip is ON** in the deployment (`WORKFLOW_PLATFORM_TRACE_SAFE_ONLY=1`),
  and vault **encryption works** — the live vault is 100% AES-GCM sealed, master
  key from a secret manager. So *most* raw is encrypted at rest.
- **B1 is NOT established.** Known at-rest leaks remain, all of the same shape
  class: token-shaped raw survives projection (a dict key like an SSN; an
  externally-supplied webhook `id`; a model-chosen tool `name`). The audit
  at-rest path is a denylist that keeps missing model-derived fields
  (`query`, `corrected_value`, `old_category`, …). Neither is closed.
- **Contract A (read gating) is the active protection** and is materially better
  than `main` (path-scoped projector, grant-gated raw). It is also imperfect for
  the same token-shape reason, but a below-grant reader sees far less than on
  `main`.
- **Acceptable only because there is a single operator.** The moment a second
  party can read the operational DB or audit log, the token-shape leaks and the
  audit denylist gaps become real exposure. That is the resume trigger.

**Flip stays ON — operator decision, 2026-08-09.** Flip-ON encrypts the vaulted
majority of raw; flip-OFF would keep *all* raw inline, so ON is the safer at-rest
posture even with B1 deferred. This must **not** be read as "B1 holds": the
token-shape leaks and audit-denylist gaps above still land raw at rest. The
binding constraint while deferred is access, not the flip: **do not enable any
second-party access to the operational store or audit log.** (Turning the flip
off is not a security upgrade — see [[project_trace_flip_on_secure_system]].)

---

## 2. Built and salvageable — do NOT rebuild these

All on branch **`p1-reprimitive`** (NOT merged to `main` — it failed review; it is
the record, not a landing candidate). Six review packages + the reviewer's guide
are in `docs/archives/trace-f1-review-*` and `docs/TRACE_F1_REVIEW_GUIDE.md`.

| Piece | State |
|---|---|
| **Path-scoped, kind-dispatched projector** (`trace_projection.py`) | Sound design (schema-driven recursion; `redact_tool_data(obj, *, kind)` over 5 asset schemas). The *shape-vs-provenance* gap is the only fundamental hole. |
| **Vault** — per-org AES-GCM, AAD-bound to (org, instance, step_attempt, kind, schema), key via secret manager, content-commitment CAS | Working; 100% sealed in prod. |
| **Read-surface grant gating** (Contract A) — grant request/approve/revoke, per-surface release audit | Working. |
| **Backfill + zero-raw verifier** (`trace_migration.py`, `tools/trace_migration.py`) | Rehearsed against a byte-verified prod copy; completes cleanly on the pre-flip + vaulted-but-unstamped populations. **Not run against prod.** |
| **Boundary property test harness** (`tests/test_trace_boundary_properties.py`) | The generative-property approach is right; it must be **extended** (see §3.4) — its generators missed token-shaped keys, which is how a "fixed the class" claim slipped through. |

---

## 3. What a correct B1 requires (the architecture, not a patch list)

### 3.1 Provenance — the missing primitive (closes the F1/F2 class)

The projector must validate by **(asset path, provenance)**, not shape. Every
value reaching a persistable surface carries a source tag:

- `platform_computed` — engine/config-authored (model id, token counts, step
  ids, capability globs, actor identity). Token-shaped and **safe by source**.
- `model_derived` — anything from a model response (tool name, output text,
  category, corrected_value, recall query). **Raw by taint**, regardless of
  shape — a model-chosen `"SSN123456789"` is redacted because of where it came
  from, not what it looks like.
- `third_party_derived` — externally supplied (webhook `id`, mail fields,
  correspondent keys). **Raw by taint.**

This is **§1.4a** in `TRACE_GOVERNANCE_PLAN.md` (seven design rounds, *not*
build-authorized). Building B1 = building §1.4a first. Without it, no shape rule
can separate an SSN from a hash. The projector then keeps a value only if its
path declares it safe **and** its provenance is `platform_computed` (or a
per-field declassification approval — §1.4a's approval object — grants an
exception).

Practical consequence: provenance must be *carried* from the point a value enters
(agent result, trigger payload, function output) to the projector. That is the
real work — a taint-tracking channel through the engine, not a validator tweak.

### 3.2 Positive audit model (closes the F3-audit/F4 class)

The at-rest audit projection is a **denylist** of raw field names and has missed a
field in three consecutive reviews. Replace it with a positive model:
per-action **allowlist** of safe operational fields (ids, counts, flags), with
everything else vaulted-then-projected — the same default-deny the projector uses.
Audit details are engine-authored, so the allowlist is enumerable per action.

### 3.3 Totality (closes F3)

`safe_tool_call` and every validator must be **total** over hostile input
(`input=1`, `result=["raw"]`, non-string markers) — never raise. A hostile
deterministic output must not 500 a read or fail safe-only persistence.

### 3.4 Projector-version upgrade sweep (closes the F5 follow-up)

Backfill must reconcile a repaired row's stamp with the **vault row's recorded
version** — stamping an old-version vault projection as current creates a §4.3
disagreement and `rehydrate` fails. Re-projecting stamped old-version rows to the
current version, updating both the operational form and the recorded version
consistently, is a distinct migration from the pre-flip backfill.

### 3.5 Acceptance bar (how we know it's done, this time for real)

- Generative boundary properties extended to **token-shaped** and
  **provenance-tagged** inputs (the generators must produce SSNs and emails as
  keys *and* values, model- and third-party-tagged), plus the six reviews'
  findings as pinned regressions.
- The full gate run **standalone** (no piping ruff/pytest through `tail` — that
  masked a real lint failure in review 6; see
  [[feedback_run_ci_checks_before_push]]).
- An external review that says it holds — not "green under our tests," which is
  0-for-6.

---

## 4. What to read first when resuming

1. This file.
2. `docs/TRACE_F1_REVIEW_GUIDE.md` — the six-round history + reproductions.
3. `docs/archives/trace-f1-review-r3-*` (latest) and the round-1..3 packages —
   each review's findings verbatim; they ARE the requirements.
4. `TRACE_GOVERNANCE_PLAN.md` §1.4a — the provenance/declassification design.
5. `NEXT_STEPS.md` G-Trace-Review round-3 entry — the six findings' disposition.

Do not resume by re-reading the projector and patching; resume by building §1.4a
provenance, because the projector is as good as shape-validation gets and shape
is the ceiling.
