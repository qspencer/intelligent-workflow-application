# Reviewer's guide — P1 re-primitive (F1 path-scoped projection + F5 one version)

**Branch:** `p1-reprimitive` · **Base:** `main` · **Scope of this review:** the
projection primitive only — **F1** and **F5** from the third code review. This
is a *foundation* review: F3, F4 and F6 are deliberately **NOT** built and are
**out of scope** (see §7). We are asking one question: **is the projector now
correct enough that work can be built on top of it?**

---

## 0. Why you are seeing this

This subsystem has **failed three consecutive external code reviews**. You do not
need to trust that the fixes are right — the whole reason for this review is that
the author's own confidence has been wrong three times running. Please **re-run
the reproductions below against the shipped source** rather than reading for
plausibility.

The third review's central finding was a *method* problem, and it is the thing to
hold us to: **we enumerated instances; you tested the property.** Every prior fix
patched the field name a review cited and the same class of bypass survived under
a name nobody had written down yet. So this round the boundary is encoded as
**generative properties** (`tests/test_trace_boundary_properties.py`), not
field-name examples — and the first thing worth doing is trying to break those
properties, not the examples.

---

## 1. What the projector is for (one paragraph)

A workflow step's raw output (mail bodies, tool inputs/results, model free-text,
recalled correspondent history) is sensitive. Two contracts protect it:

- **Contract A (read-time):** a below-grant reader — including an ungranted
  Administrator — sees a *projected* (safe) form on every read surface; raw is
  released only under a per-user raw-trace grant.
- **Contract B1 (at-rest):** under the `TRACE_SAFE_ONLY` flip the *operational*
  store holds only the projected form; raw lives encrypted in a separate vault.

Both contracts call the **same projector** (`redact_tool_data`). If the projector
lets raw survive, Contract A leaks on reads **and** — because the same function
decides "does this need the vault?" — Contract B1 writes plaintext at rest that
the zero-raw verifier then certifies clean. F1 is therefore the linchpin of both.

**The flip is currently OFF in every deployment** (`TRACE_SAFE_ONLY` unset), so
the at-rest path is not live; the read surfaces are. Contract A / B1 are **NOT
claimed** as established — that claim waits on this review.

---

## 2. The F1 finding, and the re-primitive that answers it

### 2.1 What was wrong (third review, verbatim class)

The projector was a **process-global, NAME-keyed registry** consulted at every
depth while walking the **data**. Safety became "a key appears *somewhere*", so an
undeclared container laundered raw whenever a descendant happened to spell a
registered name:

```python
redact_tool_data({"unregistered_map": {"model": "SSN123456789"}})
# -> survived unchanged: the walker recursed into the unknown container and
#    `model` was on the global allowlist
```

Plus: the one permissive validator accepted `alice@example.com` on `model`;
`safe_trigger_payload` copied routing ids with **no** validation; `safe_tool_call`
exported raw parameter **names**; and the redaction marker was **prefix-matched**,
making it an input capability.

### 2.2 What changed — recursion is driven by the SCHEMA, not the data

`backend/src/workflow_platform/trace_projection.py` is the file to read. The core:

- A **typed schema** (`Leaf` / `Obj` / `Seq` / `ToolCalls` / `TriggerPayload`)
  describes each asset. `_project(node, value)` walks the **schema**; a key not
  declared **at this path** is redacted **whole and never recursed**
  (`_project`, the `node is None` branch). An undeclared container therefore
  cannot launder anything — there is no path *into* it.
- **Leaf validators are purpose-split** (§F1b): `_short_token` excludes `@`, so a
  token path can never receive an email; `_opaque_id` is for identity paths that
  legitimately hold operator identity. The old single regex is gone.
- `safe_trigger_payload` **validates** each retained routing id (§F1c).
- `safe_tool_call` exports `input_key_count`, an integer arity — **never** the
  parameter names (§F1d).
- `_marker` matches **exactly** against `_GENERATED_MARKERS`, the closed set this
  module emits (§F2). A forged `"[redacted …]"` fails and is itself redacted.
- `null` projects to `null` at any path — it carries no content — so it produces
  no spurious markers.

### 2.3 The second half — projection is keyed by (asset kind, path)

The path half alone is not enough: a *step output*, an *instance dump* and an
*audit detail* are different assets with different safe shapes. `redact_tool_data`
now takes a **required** `kind` and dispatches over five schemas
(`SCHEMAS` in `trace_projection.py`): `step_output`, `step_row`, `instance`,
`context`, `audit_detail`. **There is no default** — an unknown/absent kind
**raises**, because a default kind is exactly the guess this design removes. All
14 production call sites declare their kind; grep is the audit:

```
grep -rn "redact_tool_data(" backend/src | grep -v "def " | grep -v "kind="
# -> empty
```

Audit details dispatch on the entry's **action** (`project_audit_detail` in
`api/redaction.py`): a `tool_call` entry's detail *is* a tool-call record and goes
through `safe_tool_call`; everything else uses the `audit_detail` schema. The
projector is path-keyed and stateless, so it cannot see the action — the dispatch
lives at the boundary that holds it (`api/workflows.py` `_project_audit`,
`api/ws.py` `_redact_ws_event`).

### 2.4 F5 — one authoritative projector version

There were two: `trace_projection.PROJECTOR_VERSION = "2"` and a hardcoded
`"trace-projector@1"` on the `RawTrace` model, so operational rows and vault rows
identified different projectors and the §4.3 agreement predicate compared against
a version that never wrote the row. `persistence/models.py` now imports the single
constant. `test_p5_single_authoritative_projector_version` asserts the value a
vault row **actually writes** (the `RawTrace` field default), not a re-exported
symbol.

---

## 3. Reproduce the F1/F2/F5 fixes (please run these)

These are the third review's own reproductions, re-pointed at the shipped source.
Each previously **survived**; each should now be **redacted**. Run from `backend/`:

```python
from workflow_platform.trace_projection import (
    redact_tool_data, safe_trigger_payload, safe_tool_call, PROJECTOR_VERSION)
from workflow_platform.trace_vault import output_has_raw
K = dict(kind="step_output")

# F1a — undeclared container no longer launders a registered-named descendant
redact_tool_data({"unregistered_map": {"model": "SSN123456789"}}, False, **K)
#   -> {"unregistered_map": "[redacted — raw-trace grant required]"}
redact_tool_data({"summary_map": {"state": "TOPSECRET"}}, False, **K)         # redacted

# F1b — a token path rejects an email
redact_tool_data({"model": "alice@example.com"}, False, **K)                  # redacted

# F1c — trigger routing is validated, not copied verbatim
safe_trigger_payload({"id": "customer secret with spaces"})                   # id redacted

# F1d — parameter NAMES are not exported (only arity)
safe_tool_call({"name": "t", "input": {"customer SSN 123-45-6789": 1}, "result": {}})
#   -> has "input_key_count": 1, and NO "input_keys"

# F1e (B1) — the vault decision agrees: registered-looking raw still needs the vault
output_has_raw({"model": "SSN123456789"})                                     # depends: see note*

# F2 — a forged marker is not trusted
redact_tool_data({"_redacted": "[redacted SSN123456789]"}, False, **K)        # redacted

# F5 — one version
from workflow_platform.persistence.models import RawTrace
RawTrace.model_fields["projector_version"].default == PROJECTOR_VERSION        # True
```

\* **Note on F1e / the token residual — read §6.** `output_has_raw({"model": X})`
is `False` when `X` is *token-shaped* (`SSN123456789` is), and that is **correct
for `model`**, which is platform-computed. `"SSN123456789"` reaching `model` is
not a path that exists at runtime. The general residual — a token-shaped secret on
a token path that *could* carry attacker content — is real, latent, and pinned
(§6). This is the sharpest thing to challenge.

---

## 4. The boundary properties are the contract

`tests/test_trace_boundary_properties.py` — 8 properties, generative over depth /
container shape / key choice. These are what "correct" means; break them and you
have found a real defect.

| test | property |
|---|---|
| `test_p1_sentinel_never_survives_nesting` | a non-token sentinel survives **no** nesting under **any** key at depths 1–4, in any container shape |
| `test_p1_token_path_rejects_email_and_prose` | a token path rejects `@` (email) and whitespace (prose) |
| `test_p1_trigger_routing_fields_are_validated` | trigger routing ids pass the same validator; an id-shaped `message_id` is kept by design |
| `test_p1_tool_parameter_names_are_not_content` | parameter names never persist |
| `test_p2_forged_marker_is_not_trusted` | a forged `[redacted…]` cannot be supplied as input |
| `test_p3_content_bearing_field_requires_the_vault` | `output_text`/`summary`/`reasoning`/`recall` raw requires the vault |
| `test_p3_token_path_residual_is_accepted_and_bounded` | the accepted residual, pinned (§6) |
| `test_p5_single_authoritative_projector_version` | vault writes the projector's version |

The sentinel is deliberately **non-token-shaped** (`"SENTINEL raw victim@… 9f3c2a"`)
so that survival can mean *only* laundering — isolating the P1 property from the
token residual. A good attack is a container shape or nesting the generator does
not currently produce.

---

## 5. How to run the suite (environment caveat)

The locked env pins `veracium==0.6.0`; if your registry can't fetch it, the full
`pytest` won't import. Two options:

- **Preferred:** `uv sync` then `uv run pytest tests/test_trace_boundary_properties.py
  tests/test_trace_review_fixes.py tests/test_trace_surface_inventory.py
  tests/test_org_isolation.py tests/test_raw_trace_rehydrate.py
  tests/test_audit_redaction.py tests/test_trace_safe_only_flip.py
  tests/test_trace_safe_only_read_merge.py -p no:randomly`
- **No-deps:** the §3 snippet imports only `trace_projection` + `trace_vault` +
  `persistence.models`, none of which import veracium — run it directly against
  the source.

**Verified state at the branch tip:** full suite **1017 passed, 14 skipped** under
**both** `DATABASE_URL` unset **and** `DATABASE_URL` set to an unmigrated DB (the
CI shape). Ruff, ruff-format, mypy strict all clean. We do not ask you to trust
that number — we note it so a discrepancy on your machine is itself a finding.

---

## 6. Known residuals — stated, not hidden

1. **Token-path provenance residual (the one to challenge).** A token-shaped
   value on a token-declared path survives projection. Correct for every token
   path *today* (`model`, `stop_reason`, `memory_hash`, … are all
   platform-computed — no attacker value reaches them). It becomes a real leak
   only if a future token path is fed model-derived or third-party content.
   §1.4a's per-field `provenance` (`platform_computed` / `model_derived` /
   `third_party_derived`) is the general control and is **unbuilt**. Pinned in
   `test_p3_token_path_residual_is_accepted_and_bounded`. **If you think a token
   path fed by non-platform content already exists, that is a finding.**
2. **Per-workflow business vocabularies over-redact.** `category`, `attention`,
   `relevance_bucket`, etc. are per-workflow, so the platform-global schema can't
   validate them and redacts them by default (the safe direction). §1.4a
   (declassification approval) is the intended opt-in and has had 7 design rounds
   without being built.
3. **`capabilities` in `_CONTEXT`** is declared via a no-whitespace `_CONFIG`
   validator (`trace_projection.py`), justified by provenance: capability entries
   are workflow-DECLARED config (glob paths, host patterns), never third-party
   content. Worth a skeptical look — it is the most permissive leaf validator.

---

## 7. Explicitly OUT of scope (do not spend the review here)

Built on top of F1's projector, and deliberately unbuilt so F1 can be reviewed
first — a fault in F1 would invalidate them:

- **F3** — the P3a projection stamp is still a mutable fail-open bit; safe-only
  execution must not depend on operational metadata a DB adversary controls.
- **F4** — the vault content commitment is still a plaintext low-entropy oracle,
  and unauthenticated (a domain-separated HMAC over canonical raw + immutable
  identity, keyed outside the DB, is the intended fix).
- **F6** — release still audits all-or-nothing kinds; Contract 5 wants exact
  per-kind returned/withheld sets.
- **§1.4a** (declassification approval / per-field provenance) — governs
  dashboard visibility of business vocabularies; independent of whether F1 holds.

---

## 8. What we most want challenged

1. **Is schema-driven recursion actually complete?** The claim is that a key
   undeclared *at its path* cannot leak. Find a real read/at-rest asset whose
   runtime shape has a raw-bearing field we failed to declare — or a container we
   declared too permissively (start with `_CONFIG` on capabilities, §6.3).
2. **The token residual (§6.1).** Is there a token-declared path, today, reachable
   by model-derived or third-party content? If so, F1 is not sufficient and
   §1.4a provenance is not optional.
3. **Asset-kind dispatch correctness.** Does every one of the 14 call sites pass
   the kind that matches the object it actually holds? A wrong kind projects a
   real asset against the wrong schema — over-redacting (loud) or, worse, applying
   a schema that declares a field the object shouldn't expose.
4. **F5 completeness.** Both the operational stamp and the vault row now use one
   constant — but does *every* write path stamp it, so the §4.3 predicate can
   never compare against an unstamped row?

If F1/F5 hold, they land on `main` and F3/F4/F6 become the next, separately
reviewed, primitives. If they don't, we would rather know now — before building
three more primitives on a projector that leaks.
