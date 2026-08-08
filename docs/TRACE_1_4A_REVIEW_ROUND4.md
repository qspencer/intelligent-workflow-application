# §1.4a — external design review, round 4

**What to review:** `docs/TRACE_GOVERNANCE_PLAN.md` **§1.4a** (lines ~330–660),
plus acceptance criteria **37–57** near the end of the same file.

**Companion docs** (§1.4a references both): `docs/THREAT_MODEL.md` (the
declassification-channel known-gap row) and `docs/EXECUTION_SEMANTICS.md` §3a
(the immutable-attempt model the versioning couples to).

**Nothing in §1.4a is built.** P1's over-redaction of per-workflow business
vocabularies (`category`, `attention`, …) remains in force, per round 3's
instruction.

---

## Where this stands

This subsystem's *implementation* failed two external code reviews. A design
review then established that most of the remediation was **build-conformance to
already-frozen contracts**, not new architecture — and identified §1.4a as the
one genuine design gap: §1.4 froze *what* may persist un-vaulted, but never said
**who declares** a per-workflow business field as one.

§1.4a has now had three rounds. Each approved the direction and deferred
authorization, and each found something real:

| Round | Central finding | Disposition |
|---|---|---|
| 1 (internal) | six conditions incl. enum-list representability | adopt-with-conditions |
| 2 (external) | **the codebook attack** — approving the declaration alone lets an ordinary Org User repurpose the approved alphabet by editing only the *prompt*; capacity claim wrong | direction approved, deferred |
| 3 (external) | **authorization identity + lifecycle** — rows bound declaration *content*, so revoke-then-reapprove resurrected withheld rows; no atomic transitions; hashes described but not specified | revised direction approved, deferred for one focused amendment |

Round 3's closing instruction was: *"turn `SafeOutputApproval` into a
first-class, atomically managed authorization object whose exact ID follows every
produced row and destination."* That amendment is what this round is being asked
to confirm.

## Round-3 findings → where each is addressed

| # | Round-3 finding | Now |
|---|---|---|
| 1 | Rows not bound to the approval that authorized them (revoke A1 → re-approve identical bytes as A2 → historical row released) | **§1.4a.2** — `approval_id` + `generation` are first-class and bind the step attempt, operational row, vault object, every destination copy, and release/revocation audit. Re-approval never reactivates earlier rows. Criterion **51** |
| 2 | No transactional lifecycle (legal transitions, atomic activation, linearization point, revocation race, `superseded` meaning) | **§1.4a.3** — state machine frozen; activation is one transaction with all predicates; attempt binding is the linearization point. **The revocation race is ruled HARD** (an in-flight attempt whose approval is revoked persists vault-only) — chosen because raw is never lost, so the whole cost is over-redaction. `superseded` vs `revoked` distinguished. Criteria **52, 53** |
| 3 | Tenant artifact scoped too broadly | **§1.4a.4** — binds org/workflow/step/declaration/step-revision/destinations/roles/retention/capacity/validity; any mismatch rejects activation; reuse across steps, clones, or capacity increases impossible. Criterion **54** |
| 4 | Neither hash actually frozen | **§1.4a.8** — RFC 8785 JCS + UTF-8 + NFC + duplicate-key rejection, SHA-256 over a domain-separated prefix, canonical bytes stored beside the digest, digest/bytes mismatch **fatal**. `step_semantic_hash` is a dependency closure over the immutable executable revision, not a hand-maintained field list. Criterion **57** |
| 5 | Capacity undercounts observable states; `POLICY_BUDGET_BITS` unset | **§1.4a.5** — capacity counts **accepted + absent + redacted + status**; exact list cardinality formulas; integer lattice replaces the float form; **`POLICY_BUDGET_BITS = 32`**, raising it needs the same dual-control. Criteria **50, 55** |
| 6 | Retroactive-revocation claim overstated | **§1.4a.6** — narrowed explicitly: revocation blocks new declassification, removes application read authorization, scrubs **live** stores; it **cannot** retract bytes already in dumps, backups, replicas, query logs or exports. *"A wrongly-approved declaration is an exposure INCIDENT, not a reversible configuration mistake."* Revocation reports scrubbed vs unscrubbable rows + backup exposure |
| 7 | Destination scope stated but not in the projection identity | **§1.4a.7** — `project(asset_kind, destination, raw_value, approval_id, projector_version)`; destinations live in the canonical bytes, approval scope, artifact, attempt binding and verifier inventory. Criterion **56** |
| nb | `safe_output` naming vs declassification | YAML key renamed **`declassify`**; operator surfaces say *declassification approval*, never "safe" |

## What we would most like challenged

1. **Is the hard-revocation ruling right?** We chose it over lease semantics
   because raw is never lost (it is in the vault), so the entire cost of hard
   revocation is over-redaction — the safe direction. The counter-argument is
   operational surprise: a long-running attempt can be invalidated mid-flight.
2. **Is `step_semantic_hash` actually closed?** It is specified as a dependency
   closure over the immutable executable revision plus transitive artifacts. If
   there is a reachable input that changes the emitted value and escapes that
   closure, the codebook attack returns.
3. **Is `POLICY_BUDGET_BITS = 32` defensible**, given the channel is finite per
   attempt but unbounded over repeated execution? The aggregate budget + rate
   boundary is deferred behind "first external tenant" — is that the right
   trigger, or does it need to precede any build?
4. **Is the review converging?** Findings have narrowed redesign → capacity
   math → authorization lifecycle. If round 4 produces only wording-level
   comments, we would treat the design as terminal and let build-time tests
   (criteria 37–57) pin the remainder, per our standing "converge specs to
   build" practice. We would rather be told that explicitly than keep iterating.

## Scope note

§1.4a buys back below-grant visibility of per-workflow business vocabularies —
i.e. dashboard badges. **It is not on the Contract A / B1 path.** Those depend on
the four build-conformance primitives (all now built, awaiting a third *code*
review). Please do not treat §1.4a as gating them, or vice versa.
