# Reviewer's guide — F1/F5 foundation, round 4 (the confirmation round)

**Package:** `trace-f1-review-r4-<sha>.tar.gz` · branch `p1-reprimitive` ·
built with `git archive` (tracked files only — no secrets, see §7).

---

## 0. Read this section if you read nothing else

This round asks **one** question, and it is narrower than every round before it:

> **Is the F1/F5 foundation sound enough to carry Contract A's read-time
> gating, which is ALREADY RUNNING IN PRODUCTION?**

Three things make that question live, and you should know all three before you
start:

1. **The flip is ON.** `WORKFLOW_PLATFORM_TRACE_SAFE_ONLY=1` is set on the
   deployed box (verified in the running process, 2026-09-18). Real mail flows
   through this projector every day. This is no longer a pre-ship review of an
   unused primitive — **something rests on it now.**
2. **Six reviews have failed.** After the sixth, we STOPPED patching and
   deferred Contract B1 (`docs/TRACE_B1_DEFERRAL.md`). Five findings from that
   round are logged and deliberately **unpatched** — see §3. We are not asking
   you to re-find them.
3. **You have not seen the last two commits.** Your previous package was
   `ebceb6a`. The final remediation landed after it (§2). That gap is the main
   reason this package exists.

**What we most want from you is a disposition, not a finding list:** given §3's
argument that the remaining defects are architectural and cannot be closed by
shape validation, is it defensible to run Contract A in production on this
foundation while B1 stays deferred — or should the flip come off?

---

## 1. Scope — in, out, and why

**IN SCOPE**

- **F1 — path-scoped, kind-dispatched projection.** Recursion is driven by the
  SCHEMA, not the data. `redact_tool_data(obj, admin, *, kind)` over five asset
  schemas (`step_output`, `step_row`, `instance`, `context`, `audit_detail`).
  There is no default kind: a default would be guessing, which is the exact
  (asset kind, path) confusion this removed.
- **F5 — one authoritative projector version.** `PROJECTOR_VERSION = "2"`,
  pinned by `test_p5_single_authoritative_projector_version`.
- **The class-level fixes in §2**, which no reviewer has seen.

**OUT OF SCOPE** (please do not spend time here)

- **F3** (the P3a stamp is a mutable fail-open bit), **F4** (the content
  commitment is a plaintext low-entropy oracle, unauthenticated), **F6**
  (release audits all-or-nothing kinds; Contract 5 wants per-kind
  returned/withheld). All three are **separate primitives, knowingly open**,
  recorded in `docs/P1_REPRIMITIVE_STATE.md`.
- **Contract B1** (zero-raw-at-rest / DB-operator resistance) — deferred behind
  the first real external tenant. The come-back spec is
  `docs/TRACE_B1_DEFERRAL.md`; if you think the deferral REASONING is wrong,
  that is in scope, but the unbuilt implementation is not.

---

## 2. What changed since your last package (`ebceb6a`)

Two commits, neither reviewed by anyone:

### `8e9d0d1` — fix the CLASSES, not the instances (6 of 7)

Each previous finding was a class we had already touched, recurring one function
over. So these fix the class:

- **Keys are content.** The `Obj` projection emitted every dict KEY verbatim, so
  a raw key leaked whole — `{"usage": {"victim@example.com": 1}}` kept the
  address, and the leak was broader than the reported wildcard case. `_project`
  now validates KEYS: a declared child key is a schema literal; any other key
  must be a field-name token (`_safe_key`) to survive; an email/prose key is
  **dropped entirely**.
- **Lists don't decompose strings.** `pinned` / `pin_overrides` use
  `_token_list`, so a bare string no longer explodes into per-character tokens.
- **Routing ids.** `safe_trigger_payload` validated externally-supplied routing
  ids with `_opaque_id`, which admits `@` (deliberately, for the operator's own
  `actor_id`/`sub` paths) — so `id: victim@example.com` survived. Routing now
  uses `_short_token`.
- **Marker totality + exactness.** One shared `is_generated_marker()`: total
  over non-strings (a hostile `{"_redacted": []}` no longer raises) and EXACT (a
  forged `"[redacted victim@…]"` is not a marker). Critically, the
  marker-as-input-capability bug was still live in the migration's
  `_error_has_raw` via prefix match — certifying forged errors clean. All three
  call sites now route through the one predicate.
- **Audit denylist.** `query` (the correspondent-derived recall query) was the
  live gap the previous guide itself flagged. Added.

### `29e2c4e` — the masked lint, and the disposition

- The tree was **not** ruff-clean (RUF005 + F841). We had reported "gates clean"
  after piping ruff through `tail`, which hides the exit code. Fixed, re-run
  standalone.
- The other five findings were **logged, not patched**. §3 is that argument.

---

## 3. What we did NOT fix, and the argument for it

**The claim: the two remaining P0s cannot be closed by shape validation.**

The projector authorises by SHAPE. A token-shaped raw value — an SSN used as a
dict key, a webhook id, a model-chosen tool name — is **indistinguishable from a
safe token by shape**. Every round, we tightened the shape test; every round,
the next reviewer found another surface where a hostile value is shaped like a
legitimate one. Six rounds, six real P0/P1 sets.

Closing this class needs **provenance**, not more shape rules: §1.4a per-field
provenance (`platform_computed` / `model_derived` / `third_party_derived`),
which is **unbuilt**. A value's SOURCE is the thing that separates a safe token
from a token-shaped secret; its spelling never will.

**The accepted residual, stated plainly:** a token-shaped value on a
token-declared path survives projection. ~~That is correct for every token path
today (all are platform-computed).~~ **[CORRECTED 2026-09-18 — the round-4
reviewer executed this and it is FALSE, and the correction governs.** Three
retained token paths take externally-supplied or model-derived content today:
a token-shaped **dict key** (`{"usage": {"AKIAIOSFODNN7EXAMPLE": 1}}` survives
verbatim), an externally-supplied **webhook `id`** when token-shaped, and a
**model-chosen tool `name`** even when dispatch rejects the tool. All three
reproduced. **And the reviewer's sharper point, accepted: the ceiling argument
is right but its conclusion was too broad.** Conservative containment does not
need §1.4a — omit unknown dict keys, keep externally-supplied ids grant-gated,
display the RESOLVED catalog name rather than the requested string. Those are
source-aware policies, not tighter regexes. Deferring the architecture must not
make every remaining defect architectural by association.**]** It is pinned by
`test_p3_token_path_residual_is_accepted_and_bounded`.

**This is the part we want challenged.** Specifically:

- Is the shape-vs-provenance ceiling argument **correct**, or is there a
  shape-level fix we have failed to see?
- Is "run Contract A behind the flip while B1 is deferred" a **defensible
  posture**, or does the residual make the flip actively misleading — i.e. does
  it advertise a guarantee the projector cannot keep?
- Is the residual **bounded** where we claim, or does a path we believe is
  platform-computed actually admit third-party content?

---

## 4. Reproduce it (please run these)

```sh
cd backend && uv sync

# the contract, as generative properties (the review's own point:
# assert the CLASS, not the example)
uv run pytest tests/test_trace_boundary_properties.py -v

# the whole suite + the gates, standalone (no pipes — that is how we
# masked a failure once already)
uv run pytest -q
uv run ruff check . ; uv run ruff format --check . ; uv run mypy src tests
```

The 15 boundary properties are the contract. The four that encode the round-3
classes specifically:

| Property | Encodes |
|---|---|
| `test_projector_is_total_over_hostile_input` | totality at every kind |
| `test_dict_keys_are_never_a_leak_channel` | keys-are-content |
| `test_marker_predicate_is_total_and_exact` | marker-as-input-capability |
| `test_tool_call_list_fields_never_decompose_a_string` | list decomposition |

Note on that second one: an earlier version of that test only generated
NON-token sentinels, so it passed while missing the token-shaped-key case it
claimed to cover — a fig leaf a previous reviewer caught. ~~The sentinel is now
deliberately non-token-shaped AND the token-shaped case is exercised.~~
**[CORRECTED 2026-09-18 — the reviewer checked, and this claim was FALSE. The
test still uses only the non-token sentinel; the token-shaped KEY case is NOT
exercised, and the separate residual test covers a `model` VALUE, not a key. So
the fig leaf was reported as fixed while still standing — the same failure, one
round later, inside the very paragraph warning about it. The invitation to
"confirm we fixed the test and not just its name" was the right instinct and the
answer was no.]**

---

## 5. Where to look

| Path | What it is |
|---|---|
| `backend/src/workflow_platform/trace_projection.py` | the projector (the linchpin) |
| `backend/tests/test_trace_boundary_properties.py` | the contract, as properties |
| `backend/src/workflow_platform/trace_migration.py` | at-rest backfill + `_audit_has_raw` |
| `backend/src/workflow_platform/engine/executor.py` | `_project_step_output_onto`, the `_audit` chokepoint |
| `docs/TRACE_B1_DEFERRAL.md` | the deferral + what a correct B1 needs |
| `docs/P1_REPRIMITIVE_STATE.md` | branch state; F3/F4/F6 open by intent |

**Known doc drift, so you don't report it as a finding:**
`P1_REPRIMITIVE_STATE.md` says "the flip stays OFF." That line is stale — the
operator turned it ON on 2026-08-09 (`docs/TRACE_B1_DEFERRAL.md` §1 records the
decision correctly, and the live process confirms it).

---

## 6. What a useful verdict looks like

Previous rounds produced finding lists, and we patched them, and the next round
found the same class one function over. That loop has stopped being productive.
For this round:

1. **A disposition on the posture** — flip stays on, or comes off, and why.
2. **Anything in §2 that is wrong** — the class-level fixes are unreviewed, and
   they touch the recursion core. A regression there is worth more than a new
   edge case.
3. **A challenge to §3's ceiling argument**, if you have one. If you agree the
   class is architectural, saying so plainly is itself the most valuable
   outcome — it converts six failed rounds into a settled design constraint.
4. Only then, new findings — and please mark whether each is shape-closable or
   needs provenance, because that distinction now drives what we build.

---

## 7. Package provenance

Built with `git archive` from a commit SHA: **tracked files only**. Everything
sensitive (`.secrets/`, `.env`, refresh tokens, `.memory/` learned-memory
store with real mail content, `.venv`) is gitignored and therefore cannot be
present. Verify:

```sh
tar tzf trace-f1-review-r4-<sha>.tar.gz | grep -iE '\.secrets|\.env$|refresh_token|\.venv|\.memory/'
# expect: nothing (the `secrets/` SOURCE MODULE is code, not credentials)
```

Integrity hashes for this package are recorded in `docs/archives/README.md`.
