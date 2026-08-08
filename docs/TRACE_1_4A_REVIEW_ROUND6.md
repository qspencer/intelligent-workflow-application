# §1.4a — external design review, round 6

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
| 3 (external) | **authorization identity + lifecycle** — rows bound declaration *content*, so revoke-then-reapprove resurrected withheld rows; no atomic transitions; hashes described but not specified | revised direction approved, deferred |
| 5 (external) | **influence graph ≠ DAG ancestry** — parallel siblings visible through the shared mutable `context.steps`, control/topology semantics, and mutable external write→read tool paths all bypass an output-based closure; `expired` had no historical-row or replacement semantics; the audit sidecar was an unscoped destination | direction approved, deferred for one focused amendment |
| 4 (external) | **dependency closure + race/lifetime/storage semantics** — the semantic hash stopped at the declassifying step, so the codebook attack moved one edge upstream; hard revocation had no finish-side fence; append-only audit could not be both a declassification destination and a scrubbable store; artifact validity had no approval-expiry consequence | direction approved, deferred for one focused amendment |

Round 4 approved the round-3 amendment as *"the correct architectural center"*
and explicitly listed 18 decisions **not to reopen**. It then found four
load-bearing gaps and two contract corrections. **Those are what this round is
asked to confirm** — the eight-item acceptance bar round 4 set.

## Round-5 findings → where each is addressed

Round 5 confirmed the completion fence and canonicalization as **closed**, kept
all 18 prior approvals, and found three HIGH + one MEDIUM. **I verified each
counterexample against the engine before folding — all three were real:**

| # | Round-5 finding | Verified? | Now |
|---|---|---|---|
| B1 | **Closure too narrow** — (a) `inputs:`-omitted reads `prior_steps: context.steps`, a **shared mutable dict** written by `asyncio.create_task` branches, so a **non-ancestor sibling** is observable by finishing first; (b) edges/conditions decide which producer runs; (c) `file_write`→`file_read`, `connector_send`→`connector_query`, browser write→read let an edited step encode into external state a **byte-identical** declassifying step reads | **Yes — all three.** Confirmed the shared dict, the concurrent scheduling, and all three tool pairs in the catalog | **§1.4a.2** — the boundary is now **semantic influence, not DAG ancestry**. Declassifying agentic steps **must** declare explicit `inputs:` (omission = not approvable); inputs bind producer revision **and** controlling topology; a step whose tools can read state another step writes must declare that effect dependency or be refused; anything non-enumerable **fails closed to a whole-workflow hash**. Criterion **42** widened with all three cases |
| B2 | **`expired` lifecycle incomplete** — historical-row semantics undefined; the fence inspected materialized state not time; an unswept ACTIVE row blocks its own replacement; definition-edit invalidation had no transition owner | n/a (spec gap) | **§1.4a.3** — `expired` behaves like **revoked** for historical rows (follows from "a tenant authorization cannot mint a longer-lived one"); the fence predicate is `status == ACTIVE AND expires_at > now`; replacement activation transitions stale rows (`→EXPIRED` by time, `→SUPERSEDED` on semantic mismatch) **in the same transaction**. Criteria **53/54** |
| B3 | **Audit sidecar was an unscoped destination** — absent from the closed vocabulary, approval identity, artifact, finish transaction, scrub and verifier | n/a (spec gap) | **§1.4a.7** — **the sidecar is withdrawn.** An audit view may only dereference an **already-authorized destination copy**, under that destination's own roles/retention/revocation. A future independent projection must first become a first-class `audit_view` destination. Criterion **56** |
| C1 | **Grammar not executable** — the `attention` example omitted the fields the rule calls required; `org_admin`/`org_user` match no runtime `Role`; `retention_window` had no bound | **Yes** | **§1.4a.5** — example fixed (no inheritance); role **wire tokens** frozen (`ADMINISTRATOR`/`ORG_ADMIN`/`ORG_USER`/`ORG_VIEWER`) and mapped once to the runtime enum, so a display rename never changes an approval hash; bare `retention_window` withdrawn for explicit bounded `seconds` or an immutable `retention_policy_id` |

## Round-4 findings → where each was addressed

| # | Round-4 finding | Now |
|---|---|---|
| B1 | **`step_semantic_hash` not closed** — binding only the declassifying step moves the codebook attack upstream. Verified in our code: the email-triage `record` step reads `steps.triage.output_text`, and `_build_user_message` defaults to `prior_steps: context.steps` (ALL prior outputs) when `inputs:` is omitted | **§1.4a.2** — the closure now spans every upstream producer whose output can reach the declassifying computation, their transitive artifacts, the conservative all-prior case for `inputs:`-omitted, and mutable rubrics (`agent_memory.md`). An explicit `steps.foo.category` binds foo's *executable revision*, not the path string. Runtime data values are explicitly NOT hashed. Criterion **42** widened |
| B2 | **Hard revocation had no finish-side fence** — persistence is not one transaction, so a delayed writer can land a declassified row after revoke's scrub | **§1.4a.3** — a SECOND linearization point at completion: `finish_declassified_attempt(...)` CAS-checks the bound approval inside one transaction, leaving only two legal orderings. Criterion **53** now exercises **both**, including scrub-then-late-writer |
| B3 | **Append-only audit vs revocation scrub** — three properties not simultaneously satisfiable | **§1.4a.7** — we drop "audit carries business bytes". Audit rows hold content-free governance identity only; an operator view resolves a **revocable projection sidecar**. Criterion **56** pins it |
| B4 | **Artifact validity had no approval-expiry consequence** — a month-long artifact activated on its last day minted an indefinite approval | **§1.4a.2/.3/.4** — `expires_at` on the approval, capped by `artifact.valid_until`; new `expired` state; **use-time authoritative** (binding refuses a lapsed approval before any sweeper runs). Criterion **54** widened to roles/retention/validity + expiry |
| C1 | **Criterion 48 required fields the grammar never defined** | **§1.4a.5** — `consumer` / `roles` / `retention` / `provenance` / `opaque_sufficient` frozen as CLOSED ENUMS (no prose, per v6 F2), required per field, and the same values the tenant artifact binds |
| C2 | **Canonicalization not exact enough** | **§1.4a.8** — integers confined to the **I-JSON safe-integer range**; unordered lists canonicalized by **NFC then Unicode code-point sort** |

## Round-3 findings → where each was addressed (unchanged, for reference)

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

Round 5's six-item bar is what this round should judge. Carrying forward:

1. **Is "explicit `inputs:` required" the right first-build cut?** It makes
   omission not-approvable rather than inferring a closure from a parallel global
   context. Usable, or too blunt for real workflows?
2. **Is the effect-dependency rule enforceable as stated?** "A step whose tools
   can read state another step writes must declare it or be refused" is checkable
   from the tool catalog, but the *state* itself (a path, a connector target) is
   runtime data. Is refusing the step the right default?
3. **Does withdrawing the sidecar leave the audit view actually usable?** It can
   now only dereference an already-authorized destination copy — if none is
   authorized, the operator sees governance identity and nothing else. Correct,
   or too austere?
4. **Is it terminal now?** Round 5 said one more focused amendment, not another
   architecture cycle. We believe this is that amendment.

## (Earlier) round-4 questions, answered by round 5

Round 4 answered our previous four questions: hard revocation **approved** (with
the fence now added), `step_semantic_hash` **not closed** (now extended
upstream), `POLICY_BUDGET_BITS = 32` **acceptable for the internal build** with
the external-tenant aggregate gate kept hard, and the review **converging but not
wording-only**. Carrying forward:

1. **Is the upstream closure now correctly bounded?** It must catch the real
   attack without becoming "any edit anywhere invalidates every approval". Our
   rule is *executable producers reachable to the declassifying computation* —
   with the `inputs:`-omitted case collapsing to all prior producers. Is that the
   right cut, and is the conservative default going to be usable in practice?
2. **Is the completion fence sufficient given separate commits?** We serialize
   the declassified-copy commit with revoke via CAS on the bound approval. The
   vault write and audit append remain separate commits — we believe that is
   safe because neither carries declassified business bytes after B3, but that
   reasoning depends on B3 holding.
3. **Does the sidecar re-introduce anything?** Moving business content out of
   append-only audit into a revocable read model solves the scrub conflict, but
   it is a new store with its own authorization. Is that a net reduction?
4. **Is it terminal now?** Round 4 said it would treat §1.4a as terminal if this
   amendment closes the eight items. We believe it does. If any item is only
   partly closed we would rather hear that than have it pass.

## The code this design touches

Nothing here is implemented — but **round 3's blocking finding came from reading
the code**, not the spec (that `attention` is a deduped *list*, which the
scalar-only draft would have redacted entirely). So the archive is the whole
repo, not just docs, and these are the files worth opening:

| File | Why |
|---|---|
| `backend/src/workflow_platform/trace_projection.py` | The projector §1.4a extends. `_SAFE_FIELDS` is the platform-global registry; per-workflow vocabularies are deliberately absent, which is the over-redaction §1.4a fixes |
| `backend/src/workflow_platform/engine/functions.py` | What the `record_*` functions actually emit. `_extract_email_triage` (~line 359) normalizes `attention` to a deduped list; `apply_labels` (~548) and paper-triage `tags` (~307) are the same shape. **This is where round 3 found the scalar-only bug** |
| `backend/src/workflow_platform/auth/rbac.py` | `ORG_WRITE_ROLES` includes Organization User — the reason §1.4a.4 moves declassification authority to `ORG_ADMIN_ROLES` |
| `backend/src/workflow_platform/auth/raw_trace_grants.py` | The existing grant state machine + CAS that §1.4a.3's lifecycle is modelled on (`update_if`) |
| `backend/src/workflow_platform/trace_rehydrate.py` | `verify_projection_agreement` — the §4.3 predicate §1.4a.8's versioning must stay compatible with |
| `backend/src/workflow_platform/trace_migration.py` | The zero-raw verifier that must resolve per-row declarations (§1.4a.8) |
| `examples/*/workflow.yaml` | Real workloads whose vocabularies a declaration would have to express |

Run the suite if useful: `cd backend && uv sync && uv run pytest -q`
(996 passed / 14 skipped at this SHA; the skips are Postgres/Bedrock/Gmail/
browser/schemathesis suites, all opt-in via env vars).

## Scope note

§1.4a buys back below-grant visibility of per-workflow business vocabularies —
i.e. dashboard badges. **It is not on the Contract A / B1 path.** Those depend on
the four build-conformance primitives (all now built, awaiting a third *code*
review). Please do not treat §1.4a as gating them, or vice versa.
