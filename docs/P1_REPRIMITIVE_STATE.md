# P1 re-primitive — state of the branch `p1-reprimitive`

**Not ready for main.** Boundary properties pass; ~10 tests fail; the cause is
understood and the remaining work is specified below.

## Done

Recursion is driven by the SCHEMA, not the data. A key undeclared at a given
path is redacted whole and never explored, so an undeclared container can no
longer launder raw because a descendant spells `model`/`id`/`state`. All 20
tests in `test_trace_boundary_properties.py` pass with the xfail markers
removed — F1a–F1d, F2 and F5 are closed by measurement.

## Asset-kind dispatch — DONE (2026-08-09)

`redact_tool_data(obj, admin, *, kind)` now dispatches over five schemas —
`instance`, `step_row`, `step_output`, `context`, `audit_detail` — and an unknown
kind RAISES rather than guessing. All 14 call sites declare what they hold, and
the verifier/backfill pass the kind of each column they scan. `output_has_raw`
and `verify_projection_agreement` use `step_output`, so the §4.3 predicate no
longer compares against a schema that never wrote the row.

One real schema gap found doing it: `_AUDIT_DETAIL` did not declare `output`,
but `step_completed` carries `detail={"output": execution.output, …}`. Added,
with its own `_STEP_OUTPUT` schema — that nesting is what the path-scoped model
exists to express.

Failures: **10 → 5**. Every remaining one is understood; none is a new defect.

## The 5 remaining, and what each actually is

1. **`test_p3_registered_looking_raw_still_needs_the_vault`** and
2. **`test_rr_p1_registered_key_with_unvalidated_value_is_redacted`** — both feed
   a *token-shaped* sentinel to a declared token path (`model`). It survives,
   correctly: `model` is engine-computed, so no attacker value reaches it.
   **But the general residual is real and NOT yet closed:** if any declared
   token path can carry attacker-influenced content, a token-shaped secret
   survives. §1.4a's per-field `provenance` (`platform_computed` vs
   `model_derived` vs `third_party_derived`) is exactly the missing control, and
   it is unbuilt. Narrow these tests **only together with** recording that
   residual — do not simply make them green.
3. **`test_redact_projects_trigger_payload_and_recall`** — the fixture nests
   `steps.classify.output.category`, but the real `context.steps` maps step id →
   output *directly* (`record_step_output` sets `self.steps[step_id] = output`).
   The fixture never matched reality; the old projector just didn't care.
4. **`test_raw_tool_payloads_hidden_without_grant`** /
5. **`test_ws_redacts_tool_secrets_without_grant`** — need reading; likely the
   same fixture-shape class, but **verify before assuming**, because these two
   assert the actual no-leak property.

## The remaining flaw, precisely

Projection must be keyed by **(asset kind, path)**. I built the *path* half and
left *asset kind* conflated into a single `ROOT`. Every caller — step outputs,
instance dumps, audit details — is projected against the same root schema, so a
step output loses `model` and `cost_usd` (they are declared in `_STEP_OUTPUT`,
not `ROOT`), and downstream equality checks then disagree.

The projector IS idempotent; that was ruled out by direct test. The failure is
schema selection, not the walk.

## The change

`redact_tool_data(obj, admin, *, kind)` dispatching to one of four schemas, and
14 call sites annotated with the kind they actually hold:

| kind | schema | call sites |
|---|---|---|
| `step_output` | `_STEP_OUTPUT` | `executor` ×2, `trace_vault`, `trace_migration.step.output`, `workflows` step dumps + explain output |
| `instance` | new `_INSTANCE` (wrapping `context`) | `executor` dumped, `workflows` instance_dump |
| `context` | existing `ROOT.context` subtree | `trace_migration.inst.context`, `workflows` detail context |
| `audit_detail` | `_AUDIT_DETAIL` | `ws`, `workflows` ×2 |
| — | `trace_migration.record` is generic; it must take the kind from the row it scanned, not guess |

`verify_projection_agreement` and `output_has_raw` must take the same kind, or
the §4.3 predicate compares against a different schema than wrote the row.

## Why I stopped here (second time)

The dispatch is done and the failure set is understood. What remains is editing
security tests, and three of the five are cases where the *test* is wrong rather
than the code — which is precisely the situation where a tired pass makes
something green that should have stayed red. Items 4 and 5 in particular assert
the real no-leak property and must be read, not pattern-matched.

## Also outstanding

- one mypy error (`persistence.models` re-export of `PROJECTOR_VERSION`)
- **F3** — the P3a stamp is still a mutable fail-open bit
- **F4** — the content commitment is still a plaintext oracle, unauthenticated
- **F6** — release still audits all-or-nothing kinds
