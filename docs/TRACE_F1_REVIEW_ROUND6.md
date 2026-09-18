# Reviewer's guide — F1/F5 round 6 (remediation of the round-5 return)

**Package:** `trace-f1-review-r6-<sha>.tar.gz` · branch `p1-reprimitive` ·
`git archive`. **Captured gates: `docs/archives/GATE_OUTPUT_R6.txt` — read §5
first; round 5's capture was not trustworthy and you caught it.**

---

## 0. Your round-5 return, item by item

All six accepted, all six reproduced here before fixing, none disputed.

| # | Finding | Fix |
|---|---|---|
| 1 | Stored traces fail reconstruction; `PROJECTOR_VERSION` still `"2"` | Bumped to **`"3"`**. An older-version row now returns `unsupported` (degrades) instead of `mismatch`. Test added, as you asked: write under the previous projection, reconstruct under this build. |
| 2 | Process-wide catalog unsuitable; `register` **replaces** rather than widens | **Global removed entirely.** Catalog is a per-call argument; no argument ⇒ name withheld. Your conservative interim, taken. |
| 3 | Withheld count fails origin + completeness | Count → **boolean `_withheld_keys`**. A boolean cannot carry a value, so a forged one is worth what a generated one is. `has_redaction_marker` recognises it; the silent hostile-key branch now raises it. |
| 4 | `wildcard_keys="platform"` unjustified | Fixed at source: the **scaffold mints `step_1..n`** and rewrites every reference. Withholding the keys was not available — see §2. |
| 4b | §3.4 `pinned`/`pin_overrides` retain definition-config names | Collapsed to **booleans**; the action-surface-probe signal survives, the names do not. |
| 5 | Closed usage schema drops real counters | `iterations` + `tool_calls` declared; preservation tested. |
| 6 | `TypeError` on unhashable `name` | Resolution is total; list/dict/int/None in the totality tests. |

**Two of these were defects I introduced while fixing round 4**, which is worth
stating plainly: the count-as-raw-channel (#3) reintroduced the
marker-as-input-capability class that rounds 2 and 3 closed, and the version
collision (#1) broke reconstruction for every already-stored trace. Neither was
a subtle interaction; both were reachable in one line of probing.

---

## 1. The finding behind the finding: my evidence was not trustworthy

You caught that `GATE_OUTPUT_R5.txt` named commit `5ab734a` while the archive
was `d67d427`, and that it printed `exit: 0` directly beneath `1 file would be
reformatted`.

Both confirmed. The capture ran each gate as `... | tail -2` and then printed
`$?` — **the exit status of `tail`, not of the gate.** That is the identical
masking failure I have made twice before, warned *you* about in the round-4
guide, and then committed inside the artifact whose entire purpose was to prove
the gates were clean. Third occurrence of one class.

`GATE_OUTPUT_R6.txt` is built differently: each gate writes to a file, `$?` is
read **immediately**, and only then is the file tailed — nothing is piped
before the status is captured. It also states the exact tree the gates ran at
and how that relates to the archive commit, with the command for you to check
that the difference is docs-only, rather than implying identity.

Please treat this file as a claim to verify, not as evidence, if your
environment can run the suite at all.

---

## 2. Why #4 could not be fixed by withholding

The obvious containment — stop publishing step-id keys — is **unavailable**,
and the reason is worth recording: the grant-holder rehydration path walks
`context.steps` **by step id** to merge raw back
(`api/workflows.py`, the explain path). Dropping those keys would leave a grant
holder unable to recover the raw they are entitled to. Containment that breaks
the recovery path is not containment.

So the origin is fixed where it is created: the scaffold now mints the ids. It
rewrites `from`/`to`, `inputs`, and whole-word occurrences in conditions, goals
and templates — the places the model refers to its own steps
(`steps['classify']['output_text']`).

**Residual, stated rather than discovered:** a workflow scaffolded *before*
this change still carries model-chosen step ids, and the projector treats
step-id keys as platform-authored. All ten bundled examples are
operator-authored and unaffected; a sweep for scaffolded definitions is a named
follow-up, not done here. Hand-authored ids remain operator content, which is
what the declaration now claims and no more.

---

## 3. What this cost

- **Tool names are withheld everywhere**, including for real tools. That is the
  interim you sanctioned; restoring them needs the versioned catalog in §4.1.
- **Pinned parameter names are gone**; only the booleans remain.
- Operators see *that* something was withheld, not *how much* — the boolean
  discloses less than the count, per your §3.3 note.

---

## 4. Open, and what we would ask this round

### 4.1 The versioned catalog (from #2)

Resolved tool names need "an immutable, versioned catalog or equivalent
recorded resolution context." We have not designed it. The shape we would
propose: record a catalog digest alongside the projection stamp, resolve
against the catalog that digest names, and treat an unknown digest exactly as
an unknown projector version — `unsupported`, degrade, never `mismatch`. Is
that the right frame before we build it?

### 4.2 Is the boolean the right disclosure?

`_withheld_keys: true` is now the only signal. Does it still reveal structure
worth having, and is "an object dropped something" the right granularity?

### 4.3 Version-bump discipline

#1 happened because nothing forced a bump when projection changed. We bumped by
hand and wrote the rule in a comment. Would you expect a mechanical guard — a
test pinning golden projections per version, failing when output moves without
the constant moving?

### 4.4 The same question as last round, unresolved

Are there other retained values whose origin is external or model-derived and
which we still vet by shape? Two rounds have each found more, from a different
direction. We would rather have the enumeration than the next instance.

---

## 5. Unchanged

**F3/F4/F6** (mutable stamp, plaintext commitment oracle, per-kind release
audit) remain knowingly open and out of scope. **B1** stays deferred under the
corrected trigger. **§1.4a provenance** is unbuilt; none of this substitutes
for it. Contract A is **not** claimed. Posture unchanged: flip ON, single
operator across every trace surface.

Secret-cleanliness: `git archive`, tracked files only; verified at build for
secrets and for personal mail data.
