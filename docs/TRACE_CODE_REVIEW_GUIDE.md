# Trace Governance — external code review guide

The **design** of this system was reviewed exhaustively (six external rounds,
folded into `docs/TRACE_GOVERNANCE_PLAN.md`). This asks for a **code-level**
adversarial review of the *implementation* — the security-critical surfaces
below. Prior code-level reviews of this project found real bypasses in
security code, so please execute and probe, not just read.

> ## Third code review (SHA `29a42f5`, 2026-08-03)
>
> **This subsystem has FAILED two external code reviews.** Round 1 (`e397b8b`)
> reproduced ten bypasses; round 2 (`2cfacfc`) passed that regression suite but
> reproduced *new, deeper* ones, correctly concluding the fixes had patched
> **surfaces and field-names rather than the boundaries the design promises**.
>
> A design review then established that most of the remediation was
> **build-conformance to already-frozen contracts**, not new architecture. Four
> shared primitives were built in response — please probe each rather than
> trusting its tests:
>
> | | What changed | Where |
> |---|---|---|
> | **P1** | key-allowlist → field→**validator** registry (§1.4 CONTRACT 1); the "numbers/bools safe by type" pass is gone | `trace_projection.py` |
> | **P2** | explain / escalations / dry-run / run-batch routed through grant + release audit + projector | `api/workflows.py` |
> | **P4** | real compare-and-set for grant activation/revocation + a vault content commitment | `auth/raw_trace_grants.py`, `persistence/*`, Alembic `0010` |
> | **P3a** | persisted projection stamp + the §4.3 `project(raw, recorded_version) == stored` predicate, replacing marker scans | `trace_rehydrate.py`, Alembic `0011` |
>
> Start from `backend/tests/test_trace_review_fixes.py` (one adversarial test per
> reproduced bypass, F1–F10 + the round-2 and P1–P4 findings) and
> `backend/tests/test_trace_surface_inventory.py`, plus the per-finding → commit
> map in `docs/NEXT_STEPS.md` (**G-Trace-Review-2**). **Re-run the reproductions;
> do not trust the tests.**
>
> **Known residuals — documented, not claimed as fixed:**
> - append-only pre-flip `audit_log` raw is *reported* by the verifier (the gate
>   fails on it) but is not yet encrypted or migrated;
> - P2 is a set of fixes, **not yet a mechanical inventory** — a NEW endpoint
>   returning a raw field would not be caught automatically;
> - the read-surface `merge_output` still falls back to a marker scan for rows
>   with no projection stamp (the 1,340 backfilled rows); the execution path does
>   not;
> - `_clear_stale_active`'s expiry transition still uses a blind save;
> - per-workflow business vocabularies (`category`, `attention`) currently
>   **over-redact** — §1.4a, the declassification-approval design that restores
>   them, has had three design rounds and is **NOT build-authorized**, so it is
>   out of scope for this review except as context.

## What the system protects, and the contract

**Asset:** raw trace content — inbound mail bodies, tool-call input/result,
free-form model output, recalled correspondent history, error text.

**Contracts (see `TRACE_GOVERNANCE_PLAN.md` §0.1):**
- **A** — application governance: raw is grant-gated at every read surface.
- **B1** — DB-operator resistance: with the flip + encryption on, the
  operational store is projected (intended zero-raw at rest) and the vault
  holds per-org AES-GCM ciphertext, key held outside the DB. **NOT claimed as
  delivered.** Two reviews have found it undelivered; the four primitives above
  are the response. Whether A/B1 now hold is precisely what this review decides
  — the residuals above are known gaps, not the whole question.
- **B2** — host-operator resistance: NOT built (infra design). The honest
  residual is *DB-operator-resistant, infra-operator-trusted*.

Two runtime toggles gate the posture: `WORKFLOW_PLATFORM_TRACE_SAFE_ONLY`
(the flip) and `WORKFLOW_PLATFORM_TRACE_MASTER_KEY[_SECRET]` (encryption).
Default OFF = dark dual-write (raw inline, redact-at-read).

## The surfaces, by concern (~1,300 LOC, `backend/src/workflow_platform/`)

| Concern | Files |
|---|---|
| Grant privilege + lifecycle (state machine, dual-control, expiry) | `auth/raw_trace_grants.py`, `api/raw_trace_grants.py` |
| Projection (the safe form; idempotent) | `trace_projection.py` (re-exported by `api/redaction.py`) |
| Read-surface redaction + release-boundary audit | `api/redaction.py`, `api/raw_trace_audit.py`, `api/workflows.py` (instance/explain/audit endpoints), `api/ws.py` |
| The flip (write-time projection, durable-or-fail, rehydration) | `engine/executor.py` (`run`/`_run_step_once`/`_mark_instance`/`resume`/`fork`/`_rehydrate_context`) |
| Vault (opaque-id repo, idempotency key) | `trace_vault.py`, `persistence/{models,sqlalchemy_models,repository,memory,postgres}.py`, `alembic/versions/0006-0011` |
| Rehydration + system-access audit (fail-closed) | `trace_rehydrate.py` |
| Envelope encryption (B1) | `trace_cipher.py`, key resolution in `main.py::_resolve_trace_master_key` |
| Backfill + zero-raw verifier | `trace_migration.py`, `tools/trace_migration.py` |
| Immutable-attempt execution model | `docs/EXECUTION_SEMANTICS.md` §3a + the `attempt` column |

## Claims to adversarially verify (the ones that matter)

1. **No raw reaches a below-grant reader from ANY read surface** — instance
   detail, explain, both audit endpoints, WS — including trigger payload,
   `output_text` echo, recall, and error text. (Prior reviews found leaks via
   the list endpoint, the `output_text` echo, and the detail-endpoint trigger.)
2. **Grant scope is honored** — an org-A grant does not read org-B raw;
   cross-org needs a platform-wide or target-org grant.
3. **Dual-control cannot be bypassed** — no single Administrator activates a
   raw grant when enforcement is on; recipient excluded from approval.
4. **The flip is zero-raw at rest** — with `TRACE_SAFE_ONLY=1`, no plaintext
   raw in `workflow_instances` (trigger/context/**error**),
   `step_executions.output`/**`error`**, or new `audit_log.detail` entries.
   The verifier (`trace_migration.py`) is the structural check and now refuses
   to certify a **capped** (non-exhaustive) scan. Probe the projector directly:
   a value survives only by passing its field's VALIDATOR, so try registered
   keys carrying unregistered shapes, and unregistered fields of every type.
5. **Rehydration is faithful AND fail-closed** — resume/fork reconstruct the
   exact raw; a missing/unrecordable vault read raises rather than running on
   projected data; system-access is audited BEFORE any fetch/decrypt. P3a: the
   decision to fetch comes from the row's PERSISTED projection stamp, and the
   fetched raw is re-projected and compared with the stored safe row (§4.3).
   Probe: tamper an operational row, abort/relabel a vault row, remove the key.
6. **Durable-or-fail holds** — under the flip a lost vault write fails the
   step, never a projected-but-unrecoverable COMPLETED.
7. **The cipher binds correctly** — AEAD associated data binds ciphertext to
   `(org, instance, step_attempt, kind, schema)`; a substituted / relabeled /
   wrong-org / wrong-key row must fail to open. Per-org key derivation (HKDF).
8. **The release-boundary audit is honest** — `raw_trace_release_decided`
   commits before any raw byte leaves; no claim of client *receipt*.
9. **Idempotency-key collision-freedom** — two different steps on the same
   attempt number get distinct vault objects (keyed on the immutable
   `step_attempt_id`, not `(instance, attempt)`).

Also welcome: anything the design corpus over-claims vs. what the code does;
any raw-bearing surface not in the list (exports, logs, metrics, error
reporting, memory introspection).

## Test map + how to run

Trace-governance tests: `tests/test_{audit_redaction,raw_trace_grants,
raw_trace_grants_api,raw_trace_rehydrate,raw_trace_release_audit,
raw_trace_vault,step_attempt_model,trace_cipher,trace_migration,
trace_safe_only_flip,trace_safe_only_read_merge}.py`, plus the two
review-driven suites: **`test_trace_review_fixes.py`** (one adversarial test per
reproduced bypass across both rounds + P1–P4) and
**`test_trace_surface_inventory.py`** (P2's grant-gated surfaces).

Suite size at this SHA: **988 passed, 14 skipped**; ruff / ruff-format / mypy
strict clean.

```
cd backend
uv sync
uv run pytest -q            # full suite (Postgres/Bedrock/Gmail suites deselect)
uv run ruff check . && uv run ruff format --check . && uv run mypy src tests
```

Deps are locked (`uv.lock`); tests default to `BEDROCK_MODE=replay` (no AWS).
The design contract is `docs/TRACE_GOVERNANCE_PLAN.md`; the normative
execution model is `docs/EXECUTION_SEMANTICS.md` §3a; the trust-boundary
disposition is `docs/THREAT_MODEL.md` §5a.
