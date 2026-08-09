# P1 re-primitive — state of the branch `p1-reprimitive`

**Not ready for main.** Boundary properties pass; ~10 tests fail; the cause is
understood and the remaining work is specified below.

## Done

Recursion is driven by the SCHEMA, not the data. A key undeclared at a given
path is redacted whole and never explored, so an undeclared container can no
longer launder raw because a descendant spells `model`/`id`/`state`. All 20
tests in `test_trace_boundary_properties.py` pass with the xfail markers
removed — F1a–F1d, F2 and F5 are closed by measurement.

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

## Why I stopped here

Fourteen security-critical call sites, each needing the right kind, is the exact
shape of change that has gone wrong three times in this subsystem when rushed.
The analysis is the hard part and it is done; the edit wants a fresh session with
room to verify each site rather than pattern-match them.

## Also outstanding

- one mypy error (`persistence.models` re-export of `PROJECTOR_VERSION`)
- **F3** — the P3a stamp is still a mutable fail-open bit
- **F4** — the content commitment is still a plaintext oracle, unauthenticated
- **F6** — release still audits all-or-nothing kinds
