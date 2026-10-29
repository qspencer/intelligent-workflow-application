# TRACE G29 review results

**Review date:** 2026-10-29  
**Package:** `trace-g29-review-0ecfa72.tar.gz`  
**Scope:** G29 per-workflow released vocabularies, evaluated against the F1/F5 projector contract and the package's review guides.

## Verdict

**Request changes. Accept disposition (a), withdrawal, for the current implementation. Do not approve G29 as an active projection feature.**

The package's disclosed fixed-point defect reproduces. More importantly, the shipped source has not actually disabled the defective mechanism: `released_fields` remains accepted by `WorkflowDefinition`, is persisted by definition-writing paths, and is consumed by all three write projections. Removing it from the bundled email-triage definition prevents current known use but does not prevent another definition—or the model-backed scaffolder—from reactivating the same path.

The F1/F5 projector remains supportable only under the invariant `released_fields is None`. If that invariant is enforced in code, the existing projector behavior appears intact. If a non-null vocabulary is allowed, the projector is not a function of `(version, asset kind, raw value)` and the agreement, read, and zero-raw contracts do not hold.

## Rulings requested by the guide

### 1. Disposition

Choose **(a) withdraw now**. Preserve **(b) row-bound policy after F3/F4** as the design direction if the product value justifies reopening G29.

A future implementation should bind an immutable projection-policy identity to each row and authenticate that identity with the raw/stored pair. `PROJECTOR_VERSION` alone cannot identify a transformation when the output also depends on a mutable per-workflow argument. The row-bound policy must participate in the F3/F4 integrity construction, historical replay, migration, read projection, and the zero-raw verifier.

Withdrawal needs to be enforced, not conventional: reject or strip non-null `released_fields` at every authoring/import/scaffold boundary, or remove the field and `vocab` plumbing until the replacement design is ready.

### 2. Is a saved definition a sufficient provenance boundary?

**Not in the current product.** The scaffold path persists model output immediately; it does not wait for a human to inspect and explicitly approve the generated definition. The source says this directly in `src/workflow_platform/scaffold.py:3-7`, and `src/workflow_platform/api/workflows.py:408-456` validates and saves the returned model object.

An end-to-end probe supplied `released_fields` in the scaffold model response. The endpoint returned HTTP 200 and a subsequent GET returned the same persisted declaration:

```text
create_status 200
persisted_released_fields {'category': ['operator-looking-token']}
```

This contradicts the provenance claim in `src/workflow_platform/workflow/definition.py:276-280` that the declaration is never model output. A role-gated request to generate a draft is not field-level approval of a disclosure policy.

For a future G29, privileged projection-policy fields should be absent from model-generated drafts, or staged separately and activated only by a distinct, audited confirmation made after the exact field/value set is shown to the operator.

### 3. List-valued fields

Do **not** make generic list values declarable in the first version. Start with scalar enums only.

The current declaration does not state cardinality. Every field automatically accepts either one member or any ordered, repeated sequence of up to 32 members (`trace_projection.py:674-688`). Thus a declaration that visually reads as a scalar vocabulary also authorizes a list channel:

```text
vocab = {'category': ['promotion', 'spam']}
category = 'spam'                 -> released
category = ['spam']               -> released
category = ['spam'] * 32          -> released
category = ['spam', 'promotion']  -> released
```

If multi-valued release is later needed, the declaration should name its shape explicitly. For set semantics, deduplicate and emit members in declaration order so the released value is a subset, not a sequence. If order is truly meaningful, it needs a separately reviewed channel budget and a producer-specific schema rather than the generic vocabulary mechanism.

## Findings

### P0 — The parameterized projector breaks its authoritative fixed point and four consumers

`redact_tool_data(..., vocab=...)` releases fields at `_STEP_OUTPUT` (`src/workflow_platform/trace_projection.py:1222-1237`), but stored rows do not carry an immutable, authenticated vocabulary identity. Consumers re-project without the argument:

- instance read surfaces: `src/workflow_platform/api/workflows.py:1222-1245`;
- agreement predicate: `src/workflow_platform/trace_rehydrate.py:80-100`;
- zero-raw verifier and backfill: `src/workflow_platform/trace_migration.py:72-75`, `169`, `205`, and `218`;
- vault decision: `src/workflow_platform/trace_vault.py:36-41`.

All C0-C5 package reproductions passed, confirming that:

- a write stores the released verdict;
- a below-grant read strips it;
- agreement reports `mismatch` for a correctly written row;
- the zero-raw verifier reports raw on a correctly projected row;
- backfill would silently remove the released field.

This is architectural, not a missing keyword argument. Supplying the current definition would make historical interpretation depend on mutable state.

### P0 — The claimed withdrawal is configuration-only; the defective path remains reachable

The built-in workflow is guarded to keep `released_fields=None`, but the implementation remains active:

- `WorkflowDefinition.released_fields` is still a public accepted field (`workflow/definition.py:265-286`);
- the executor passes it into step, preserved-step, and context projections (`engine/executor.py:1164`, `1323`, `2044`);
- definition and scaffold endpoints can persist it.

An independent safe-only engine probe with a valid non-null declaration completed and stored the released value in both operational locations:

```text
instance_state completed
stored_step_output {'category': 'spam'}
stored_context_step {'category': 'spam'}
```

Therefore the source does not enforce the operational condition under which the guide says the feature is withdrawn. A new saved definition can recreate the control failures without a code deployment.

### P0 — The scaffold path can create the disclosure policy from model output without review

The scaffold response is model-authored, validated, and persisted in one request. `released_fields` is neither removed nor held for approval. The independent HTTP reproduction above proves the model-provided declaration survives into the stored definition.

This invalidates the central G29 soundness argument: configuration is not automatically operator-provenanced in this path. The same concern should be checked for other privileged definition fields, but G29 is independently blocked without resolving that broader question.

### P1 — A scalar-looking declaration silently authorizes an ordered-list channel

`_vocab_member` accepts both strings and lists for every declared field. The author cannot say that `category` is scalar-only. Repetition and ordering therefore enlarge the release channel beyond what the declaration communicates. The guide's approximately 53-bit example for a three-member vocabulary reproduces, and the behavior is not limited to fields intended to be lists.

### P2 — Authoring and projection validators disagree

The definition validator accepts any nonempty string up to 120 characters (`workflow/definition.py:327-330`), while projection normalization retains only `_short_token` values (`trace_projection.py:708-711`). Declarations containing spaces or `@` save successfully and then silently release a smaller set. This fails closed but creates an authoring trap and two definitions of the rule.

### P3 — The documented no-dependency reproduction path is not dependency-free

Running the supplied reproduction with ambient Python failed before the checks because importing `trace_migration` traverses persistence modules and requires SQLAlchemy. The locked environment worked. The guide should remove the no-deps claim or reduce the reproduction's imports.

## Required changes before reconsideration

1. Enforce withdrawal in source: reject/strip non-null `released_fields`, or remove the implementation until its integrity dependencies exist.
2. Add an explicit approval boundary for any model-generated privileged definition field. Draft creation must not itself be approval.
3. Design an immutable row-bound projection policy, authenticated with the F3/F4 construction and included in historical replay.
4. Make policy shape explicit; begin with scalar enums. Canonicalize any future set-valued output.
5. Use one validator at authoring and projection boundaries.
6. Test the consumer contract, not only same-argument idempotence: every stored projection must be reproducible from immutable row-bound inputs by reads, agreement, migration, and verification.

## Verification

- Package G29 reproduction: **all 29 checks reproduced**.
- Focused projector/read/vault suite plus G29 and golden tests: **231 passed, 2 skipped**.
- Ruff and format gates: **passed**; 251 files formatted.
- Code manifest: every listed source/test hash matched. The checker reported six non-hash formatting lines but no content mismatch.
- Full suite with `DATABASE_URL` unset: **1413 passed, 24 skipped, 14 failed**.
  - All 14 failures were caused by the installed Veracium runtime rejecting this environment's unqualified SQLite build identity.
  - The count reconciles with the package's 1428/23 result: 14 environment failures plus one additional skip because the archive has no Git history.
  - No full-suite failure contradicted the focused G29 results.
- Independent probes:
  - model-provided `released_fields` persisted through `/api/workflows/scaffold`;
  - safe-only execution stored the declared value operationally;
  - a scalar-looking declaration released ordered and repeated list values.

## Final assessment

The package correctly identified the central fixed-point defect and responsibly removed the live declaration, but the implementation is still reachable and its provenance premise is false on the scaffold path. Treat G29 as withdrawn at the product level and disable it at the code boundary. Revisit row-recorded vocabularies only after F3/F4 can authenticate the complete projection policy and after author approval and value cardinality are explicit.
