# G12 ask-the-user — design review, round 3 sidecar

**Still a design, still no code.** Round 2 returned it for a narrower
revision: C1 approved in principle, gated on four decisions, plus three
material issues and two staging adjustments. All taken in
`docs/ASK_THE_USER_PLAN.md` v3. Nothing disputed.

| | |
|---|---|
| Design | `docs/ASK_THE_USER_PLAN.md` (v3) — §0 tables every round-2 item against where it landed |
| Prior sidecars | rounds 1 and 2 (round 1's §4 finding 4 is marked WRONG in place) |
| Gates | five green — recorded for form; round 2 correctly declined to treat them as validation of G12 |

---

## 1. The four C1 gates

| gate | v3 |
|---|---|
| reservation/crash | **atomic creation** of capacity-consume + reservation + question record in one transaction. No interval to compensate for, so no compensating release to get wrong. The expiring-reservation fallback is documented and unused (Postgres can do it in one transaction). |
| outstanding capacity | pending questions **expire and release capacity**; the `(subject, topic)` "once ever" history is a **separate, permanent** record. Collapsing the two is what made v2's cap deadlock itself. |
| shadow state | same scheduling code, **its own** reservations, counters and simulated lifecycle. |
| candidate vocabulary | fixed and explicitly provisional: `then: {priority: relevant \| not_relevant}` — deliberately not `attention`, so C1 cannot quietly redefine the two-axis rubric. The opening example is corrected to match. |

**The recipient-slot race was a real one we had not seen.** A
`(subject, topic)` uniqueness constraint stops two runs asking the same
question and does nothing about two runs asking *different* questions
that both take the last slot. Capacity is now checked and consumed inside
the same transaction.

---

## 2. A rule of our own, withdrawn — step 2 on v3's own text

v3's first draft carried §6's two-route rule as: *"the memory edge is
retired at the source"* at expiry, withdrawal or supersession.

**Executed against the installed library.** veracium exposes
`Memory.correct(user_id, edge_id, corrected_value, …)` — supersedes an
edge **and records a replacement value**,
`invalidation_reason="corrected"` — and `revoke_source(...)` for
source-level revocation. There is **no primitive for "this fact merely
expired, retire it"**, and `LearnedMemoryService` wraps neither. So
supersession-by-a-new-answer maps onto `correct()`; expiry and
withdrawal-without-replacement do not map at all.

**So the rule is withdrawn and the design changed rather than the rule
being softened: elicited answers are not ingested into memory in the
first release.** One route instead of two, validity acting only where we
control the semantics, and the loss stated — elicited answers do not
enrich the correspondent subgraph, and veracium carries no history of
them. C3b re-enables ingestion when an expiry primitive exists; that ask
goes to the veracium side beside the explicit `volatility`-on-ingest ask
from round 1.

This is the **third** time the protocol's step 2 has caught a claim in
our own package (round 1: the partition misreading; round 2: `spec.
user_id` never joins to `users`, which we found *and* reported; here: a
retirement primitive we assumed). The first was an error we shipped; the
last two were caught before the package left. That is the protocol
working, and it is worth saying that the rate is not obviously falling.

---

## 3. The three material issues

**(1) Profile retrieval.** Accepted in full — *"a fixed query guarantees
another retrieval attempt, not that the desired answer is included"*.
v2's §1a answered "how do we retrieve this?" with another recall, when
the question was "what makes an answer authoritative?". v3: the **answer
record** is the source of truth; the profile block is assembled from
current, applicable records with declared eligibility, provenance,
ordering and overflow (truncation recorded, never silent). "The answer is
absent" and "what the profile block contains" are now the same query over
the same records — v2 had one reading records and the other reading
recall, which is precisely how a recall miss becomes "never answered".

**(2) Validity across both routes.** Accepted, and see §2 — with
ingestion deferred there is one route, so the rule holds by construction
rather than by reconciliation. The contradiction round 2 found is
resolved the way it suggested: **no automatic repeat questions**;
`review_after` marks a visible **stale** status; reconfirmation is
user-initiated. A stale answer stays current for decisions until it
expires or is revised.

**(3) Ingestion identity.** Accepted. The operation key is
`(question_id, answer_revision, operation)`. A retry reuses its key; a
correction is a new operation, not a retry; a delayed retry checks its
revision is still current and terminates as `superseded` rather than
restoring a corrected-away fact. The write-succeeded-but-marking-failed
case converges by re-running the idempotent operation. **Enforced in the
answer-record store and the ingestion worker, not in `observe()`** —
round 2 is right that the supplied wrapper establishes none of this, and
v3 names the worker rather than asking veracium to change.

---

## 4. Staging

C1's claim is narrowed to round 2's wording, with a table in §8 of what
it can and cannot show. Our round-2 sidecar asked whether C1 was worth
running given it calibrates nothing; round 2's answer — it was never
meant to — is the correct reading of its own round-1 instruction, and we
had over-read it. Recorded as such.

Measurement identities are captured **as the events happen**: C2 records
question↔answer-revision, C3 records supplied-context. C4 builds the
analysis on records that already exist.

Answer-lookup failure is distinguished in **C1**, not deferred with
profile-read failure to C3 — round 2's note, and the reason is sharp: a
lookup that fails must not be counted as "no current answer", or the
shadow log's suppression counts are wrong in the direction that looks
like success.

---

## 5. What we would find if paid to fail v3 (claims executed)

1. **Deferring ingestion makes the first release less useful than the
   pitch.** G12 was motivated by the system *learning* context. v3's
   first release stores answers in its own table and shows them to the
   classifier; veracium learns nothing from them. That is defensible
   given §2, but it means the first release is "a profile the operator
   filled in by being asked", which is a smaller claim than the backlog
   entry makes. We would rather say so than let C3 ship under the
   original framing.
2. **The answer-record store is new persistence we have not designed.**
   Tables, migration, org scoping, and the retention question — answer
   records are personal data about the operator, and nothing in this plan
   says how long they live. Round 3 should probably ask for that.
3. **`review_after` with no automatic re-ask may be inert.** A visible
   stale badge on a dashboard nobody opens is not a mechanism. The design
   has no story for surfacing staleness where the operator will see it,
   and "user-initiated reconfirmation" assumes a user who looks.
4. **C1 can pass while the catalogue is useless.** Every C1 metric is
   about the *mechanism* — frequency, suppression, caps, recovery. A
   catalogue of well-formed, correctly-budgeted, completely pointless
   questions produces a clean C1 report. The first genuinely product-
   level signal arrives at C4, which is three stages away.

---

## 6. Claims executed this round (step 2, including §5's own — step 2c)

| claim | check | result |
|---|---|---|
| veracium has no expiry/retire primitive | grep for `def .*retire` / `revoke` / `correct` / `forget` across the package | `correct()` (supersede **with** replacement), `revoke_source()`, `forget(user_id)`; **no plain retire** |
| `LearnedMemoryService` wraps neither | read the service | only `observe` / `recall_context` / `record_outcomes` / `introspect_namespace` |
| `correct()` is a protected host API | read its docstring | *"never exposes this on a surface a model can reach"* — consistent with engine-driven use |
| Postgres can do the atomic path | repo write pattern | 53 `async with self._sf()` scopes, `s.add(...)` + commit per scope — one transaction per call site is the existing idiom |
| two-axis defines `review` as consequential | read the plan | *"verify/decide when convenient; consequential but not time-critical"* + a narrow operational definition added against over-application |
| the label path is add-only | `EMAIL_TRIAGE_ACT_PLAN` | *"the tool path only adds labels (`addLabelIds`)"* + no-create fence |
| `spec.user_id` never joins to `users` | grep under `engine/`, `memory/` | no `users.get` / `get_by_login_email` — unchanged from round 2 |

---

## 7. What we want from round 3

1. **Is deferring memory ingestion (§2) the right call**, or is the
   better move to ask veracium for the expiry primitive first and keep
   ingestion in the first release?
2. **Retention for answer records** (§5.2) — we think this needs a
   decision before C2 persists anything, and the plan currently has none.
3. **Anything that blocks C1 now.** We believe the four gates are closed
   and would start C1 on a yes.
