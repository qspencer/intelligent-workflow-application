# Reviewer's guide — round 7: ownership typing

**Package:** `trace-f1-review-r7-<sha>.tar.gz` · branch `p1-reprimitive`.
**New in this package:** `docs/archives/CODE_MANIFEST_R7.txt` — the test-time
code manifest you asked for (§5).

---

## 0. What this round is

You named the class behind six rounds:

> the projector knows the asset KIND and the field PATH, but does not establish
> which PRODUCER supplied the value

and prescribed distinguishing **engine-owned metadata, approved configuration,
business output and stored projection metadata at their boundaries**, with
unapproved business fields withheld until release rules exist. **This round is
that build**, not another patch cycle. Round 6's three defects are also fixed
(§4).

---

## 1. The taxonomy

`Owner` has four values — `ENGINE`, `CONFIG`, `BUSINESS`, `PROJECTION` — and
`_OWNERSHIP` declares one for **every root field of every typed schema**: 57
fields across `step_output`, `step_row`, `instance`, `context`.

**There is no default.** An unclassified field fails
`test_every_declared_field_has_a_declared_owner`, for the same reason there is
no default asset kind: a default is a guess, and guessing about the producer is
the whole defect. The test also fails on ownership declared for a field that no
longer exists, so the table cannot rot in either direction.

## 2. BUSINESS is withheld by default

`faithfulness_score`, `category_score`, `relevance_score`, `needs_tests`,
`concern_count` — parsed from model output. A float is not a measurement
because it is a float.

`_RELEASED_BUSINESS` holds exactly one name, **`parse_ok`**, with its rule
written beside it: our own code computes it, a boolean cannot carry content,
and it is the signal that says *the step ran but the model returned junk* —
which an operator cannot infer from anything else still visible.
`concern_count` stays withheld precisely because a count CAN carry a value,
which is the channel the withheld-count defect opened.

**This is the disclosure decision to judge.** Is the release rule for
`parse_ok` sound, and is one-name-with-a-reason the right shape for that list?

## 3. ENGINE is defended at the boundary, not at projection

Your reproduction: the registered `noop` returns its config unchanged into the
same schema engine output uses, so `model`, `memory_hash` and
`usage.input_tokens` survived verbatim with `output_has_raw()` clean.

A function's output now **cannot carry engine-owned keys** — they are dropped
and logged where the value enters.

**Why there and not in the projector:** `StepExecution` does not persist the
step type, so a verifier re-projecting a stored row could not re-derive the
producer. Resolving at the boundary keeps projection a pure function of the
record — which is the property your round-5 catalog finding turned on. Stored
output simply never contains a forged engine field.

## 4. Round 6's three defects

| Finding | Fix |
|---|---|
| Scaffold substring rewrite (`pdf_extract`→`pdf_step_1`, a path, a comparison literal; duplicates minted apart) | Declared reference positions only (`id`, edge `from`/`to`, `inputs`) plus real `steps['x']`/`steps.x` refs in free text; ids validated **before** the mapping, duplicates raise. The test that *rewarded* the defect now asserts the id SET. |
| Withholding vs completeness disagreed (3 cases) | ONE predicate `is_withheld_marker`, used by completeness and compatibility, recognising the round-5 `_withheld_key_count` without ever emitting it; an invalid reserved value now RAISES the flag instead of vanishing. |
| `PROJECTION_SCHEMA_VERSION` still 1, declared twice | Bumped to **2**, projector is the one authority, `persistence.models` re-exports it, pinned by a test. |

## 5. The manifest (your packaging ask)

`CODE_MANIFEST_R7.txt` hashes every source file the gates ran against, with an
aggregate. Checkable from the extracted package with no git:

```sh
cd backend && find src tests -name '*.py' -type f | sort | xargs sha256sum | sha256sum
# expect 75b1c3af3505cb89f657fde81f2b97ab358ac3b9bd36ec96b7251dca8d7d30c5
```

`GATE_OUTPUT_R7.txt` reads `$?` before any pipe and points at the manifest.

## 6. Open, and NOT claimed

Named rather than discovered:

- **CONFIG has no approval rule yet.** `workflow_id` still derives from the
  model's proposed name, and slugging preserves recognisable content. Model
  selection and capability paths/hosts are also definition-derived. Minting
  step ids established the origin of step ids and nothing else.
- **Audit at rest**: `tool_param_override_blocked` still retains the requested
  tool name; `tool_pin_unresolved` retains definition-derived values.
- **Tool-summary numbers**: the already-projected branch still accepts supplied
  `input_key_count` / `content_bytes` on shape.
- **The catalog digest** (your §4.1 answer) and **the golden-fixture version
  guard** (§4.3) are designed-but-unbuilt.

**The question for this round:** is the taxonomy the right cut, and is
resolving at the boundary the right place — before we extend it to CONFIG
approval and the audit actions? Getting the shape wrong now is expensive to
undo later.

F3/F4/F6 and B1 unchanged. Contract A not claimed. Single-operator posture
unchanged.
