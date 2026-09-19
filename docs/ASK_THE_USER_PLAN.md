# Ask-the-user: clarification elicitation (G12)

Status: **design v4, 2026-09-19** — round 3 **ACCEPTED for C1 shadow
development**; C2/C3 remain gated. v4 carries round 3's consistency
edits, the indirect-route closure (§6a), retention (§6b), and an honest
restatement of the C3b prerequisite. Previously v3 — revised after external review round 2
(**RETURNED for a narrower revision**; C1 approved in principle, gated on
four decisions). v3's changes are listed in §0. Previously revised after
round 1
(**RETURNED for design revision**; the feature is worth pursuing and the
fixed catalog is a sound starting point, but seven decisions were owed).
Both start triggers are met — the two-axis split landed 2026-07-26,
outcome emission 2026-07-19. Effort M–L.

**Round-1 verdict, kept at the top because it reframes §2 and §3:**

| question | reviewer's assessment |
|---|---|
| Is the fixed catalog sufficient? | It constrains the WORDING. It does not yet establish that an answer becomes *the right assertion about the right subject, under the wording the user actually saw*. |
| Is the budget real? | The hard caps can make it real. A model-produced conditional is a relevance heuristic, not proof a question is useful. |

Both are accepted in full. §2 and §3 are rewritten around them.

---

## 0a. What round 3 changed (v4) — and C1 is accepted

Round 3 **ACCEPTED C1 shadow development**. C2 and C3 stay gated; this is
not approval to enable live questions or profile context.

| # | round-3 item | where |
|---|---|---|
| i | deferring ingestion is right, **but v3's "single route" claim was false** — `steps.record.summary` carries a profile answer into memory indirectly | **§6a**, closed before C3, with an acceptance case |
| ii | retention, decided before C2 persists a real answer | **§6b** |
| iii | §4a **overstated** the worker: a crash after the downstream write, before completion is recorded, is not resolved | **§4a** — restated as an open C3b prerequisite |
| iv | "inside one transaction" needs an enforcing mechanism | **§3a** — conditional counter update, with the test establishing it |
| v | four consistency edits (`review` in §1, memory in §1/§9, ingestion items in the C2/C3 list, eligible lookup vs truncated rendering) | §1, §1a, §8, §9 |
| vi | read authority for answers, separate from escalation-resolve; finite `valid_until` for changeable topics | §6b |

**The finding that matters is (i), and it is the same mistake in a new
place.** v3 answered "is there a second route?" by looking at the route
it had designed. The route already in the workflow — the classification
summary, interpolated into an observation — was not examined. Round 1's
finding was a misread of that same file; round 3's is a part of it we
never read.

---

## 0. What round 2 changed (v3)

Round 2 accepted the immutable question record, engine-controlled
scheduling, the namespace correction and future-messages-only, and
returned three material issues plus a gap-timing table. All accepted.

| # | round-2 requirement | where |
|---|---|---|
| a | three of the five gaps are **before C1**, not C2 | §3a |
| b | capacity check + reservation must be **atomic** — a `(subject, topic)` reservation does not stop two *different* topics taking the last recipient slot | §3a |
| c | shadow runs the same code against **separate shadow state** | §3b |
| d | subject authorization is an **explicit binding to platform identities**, not a reinterpretation of `learned_memory.user_id` | §1b |
| e | a fixed `profile` query guarantees another retrieval, not a **current confirmed profile** | §1a — rewritten |
| f | validity must govern **both routes** to classification, and "never re-asked" contradicted `review_after` | §6 |
| g | ingestion identity must be per **(question, answer revision, operation)** | §4a |
| h | concrete experimental output vocabulary before C1; measurement identities captured **during** C2/C3 | §5a, §5 |

**The correction that matters most is (e), because v2's §1a was wrong in
the same shape as v1's §1a** — it answered "how do we retrieve this?" with
another recall query, when the question was "what makes an answer
authoritative?". A label saying `profile` does not make what a generic
recall returned into a confirmed answer.

The idea, from the 2026-07-19 labelling session: the system should be able
to **ask for the one context fact that would change its answer**, instead
of only passively accumulating context. Indeed job alerts are routine
notifications — unless you are out of work. An event thread classifies
differently if the system knows you plan to attend.

---

## 1. The shape, in one paragraph

The classifier already produces a verdict. It may additionally declare a
**conditional**: *"notification — but if `employment_status = seeking`
then `priority: relevant`"*. A conditional naming a topic the operator
has catalogued, whose answer the system does not have, becomes a
**question**. The question text is the operator's, the answer is chosen
from the operator's enum, and the **answer record** is composed by the
engine from those two. Nothing the email contains reaches any of it.

*(Two things this sentence used to say and no longer does: the outcome is
`priority`, not `attention`/`review` — §5a, so C1 cannot quietly redefine
the two-axis rubric; and the answer is written to an **answer record**,
not to memory — §6, ingestion is deferred.)*

---

## 1a. Three roles, separated — and a correction to our own round-1 claim

**We got this wrong in the round-1 sidecar and the reviewer caught it.**
The sidecar asserted that an elicited fact would land in the
*correspondent's* memory partition. It would not.
`examples/email_triage_apply/workflow.yaml` fixes
`learned_memory.user_id: qspencer@gmail.com` — the mailbox **owner** — and
`recall.query_from: trigger.from_address.address` makes the correspondent
the **query**. The service keeps those arguments separate; there is one
partition per owner. Verified against the live store: every
`memory_observed` / `memory_recalled` row carries
`org:default:user:qspencer@gmail.com`.

So no second namespace is needed, and the design does not introduce one.

**The real problem is the one the correction exposes, and it is broader.**
Three roles have been running together in our heads:

| role | today | for an elicited fact |
|---|---|---|
| **memory ownership** (the partition) | the mailbox owner | unchanged — the owner |
| **fact subject** (who the assertion is about) | implicit, usually the correspondent or an org | **the owner**, explicitly |
| **recall query** (what selects the subgraph) | the correspondent's address | **must not be** the correspondent |

That third row is the design problem. Recall is query-driven: measured
against the production store, `recall(owner_ns, query)` returns a
token-budget-bounded slice (40 edges at `token_budget: 600`) selected
around the query. An owner-level fact — *"the owner is seeking
employment"* — is in the right partition but is **not reliably selected
by a query that is a correspondent's address**, and its whole value is
that it should apply to messages from senders it has nothing to do with.

**v2 answered this with a second fixed-query recall. That was wrong, and
round 2 said why:** *"§1a replaces one bounded recall query with another.
That guarantees another retrieval attempt, not that the desired answer is
included."* The same partition holds thousands of other observations; a
query labelled `profile` selects a subgraph, it does not select
*confirmed owner answers*, and labelling the block "profile" would have
conferred confirmed-answer status on whatever came back.

**Decision (v3): the structured answer record is the source of truth, and
the profile block is built from it — not from recall at all.**

- An accepted answer is persisted as a first-class **answer record**
  (§4), keyed by question, subject and revision. That record — not
  memory — is what a later classification reads.
- **Two distinct operations, and round 3 is right that conflating them
  would be a bug.** The **eligible-answer lookup** returns every current,
  applicable answer record for the subject (`status = answered`, not
  superseded, not withdrawn, valid now per §6) — it is the authority for
  "does an answer exist?", and it never truncates. The **profile
  rendering** is what fits in the prompt: the same set, ordered by
  catalogue topic priority, truncated with the truncation recorded in the
  block rather than silent. **An answer omitted for space is still an
  existing answer** — the scheduler asks the lookup, never the rendering,
  so a long profile can never cause a question to be re-asked.
- **Memory ingestion is deferred** to a later stage, for a reason that
  only surfaced when §6's retirement rule was checked against what
  veracium actually exposes: there is no primitive for retiring a fact
  that merely expired. Ingesting an answer we cannot retire on our own
  terms is how a withdrawn answer keeps influencing decisions. See §6.

So "the answer is absent" (§4) and "what the profile block contains" are
now the **same query over the same records**, which is what makes them
consistent. v2 had one reading records and the other reading recall, and
that divergence is exactly how a recall miss would have been read as
"never answered".

**Worked example — one answer, two correspondents, a fact about neither.**
The owner answers `employment_status = seeking`. Subject: the owner
(§1b). Stored as an answer record; not ingested into memory (§6). A
later Indeed alert and a later LinkedIn alert are classified
under different correspondent queries; both get the same profile block,
assembled from the answer record and not from either recall. Neither
`indeed.com` nor `linkedin.com` gains an edge.

---

## 1b. Subject authorization: an explicit binding, not a reinterpretation

Round 2, and it settles the blocking gap our round-2 sidecar raised:
*"use an explicit binding — not a new interpretation of
`learned_memory.user_id`."*

**The memory namespace contract is unchanged.** `learned_memory.user_id`
stays what it is: a free string naming whose memory partition this is. We
do not overload it, and we do not require it to resolve to a user.

**A separate, authoritative binding carries identity.** Declared in
configuration and validated when questions are enabled for a workflow:

| field | first release |
|---|---|
| `subject` | a `platform_user`, identified by `users.id` |
| `org_id` | recorded explicitly, never inferred from the subject |
| `recipient` | who is asked — a platform user; may differ from the subject only under a declared delegation |

**A catalogue may reference the binding; referencing is not authority.**
An unchecked `subject` field on a catalogue entry establishes nothing —
round 2's point. The binding is validated at enable time, and the
respondent's permission is checked **again at answer time** against
current state, because authority can be revoked between asking and
answering.

**The mailbox pseudonym from `TRACE_SUBJECT_IDENTITY_PLAN` is not used
here.** It is an audit correlator; it is not an authorization mechanism
and not a directory key. Round 2 says so explicitly and it is right —
`subject_from_user_id` is the relevant half of that design, and only for
`platform_user` subjects.

**Delegation, if an administrator may answer for someone.** Three things
are preserved separately and none is collapsed into the others: **who
answered**, **whom the answer concerns**, and **the delegated authority
under which they answered**. An administrator's response is recorded as
an administrator's response — it is never described, stored or displayed
as confirmation by the owner. If the first release does not need
delegation, the fields still exist and the answer path refuses when
respondent ≠ subject.

---

## 2. Constraint 2 first, because it decides the whole design

> **Questions are an injection surface.** Question generation derived from
> third-party mail is tainted content; a hostile email must not be able to
> induce a manipulative question or smuggle framing into the USER-authored
> answer fact.

This is the harder constraint and it is not a filter problem. The answer
lands in veracium as `author="user"`, and **that is the only ingest class
whose edges the gate will state as fact**. Verified rather than assumed:
`Edge.assertable` is `active ∧ ¬quarantined ∧ ¬use_only ∧ valid_now`;
third-party CLAIMS are quarantined by relation (`QUARANTINE_RELATION =
"third_party_claim"`), and benign third-party *inferences* are marked
`use_only` at ingest. Either way, content arriving as `third_party` is not
assertable and user-authored content is. So a path from email text into a
user-authored fact is a **privilege escalation across exactly that
boundary** — the one `EMAIL_TRIAGE_ACT_PLAN` spent its design on for
labels.

*(Precision matters here: `assertable` is a property of the EDGE, not a
field called "trust". Author drives it indirectly, through quarantine and
`use_only`. An earlier draft of this document said "highest trust class",
which is the right conclusion from the wrong mechanism.)*

**So the same answer: enum-derived, never free text.**

- The operator declares a **question catalog** in the workflow YAML. Each
  topic carries a fixed `prompt`, a closed `answers` enum, a `volatility`
  hint, and the `fact` template to store.
- The classifier's ONLY output into this path is a **topic id**, validated
  against the catalog. It cannot write a question, a framing, or a fact.
- The stored fact is composed by the ENGINE from `fact` + the answer the
  user picked. **Both are operator-authored**, so the user-authored fact
  contains zero attacker-influenced bytes.
- A topic id that is not in the catalog is dropped and audited
  (`question_topic_rejected`), exactly as `category_valid` gates the
  category today.

### 2a. The channel the "zero influenced bytes" argument missed

Round 1: *"the association between otherwise valid values. An answer can
use only approved words and still become the wrong fact if its subject,
catalog version, or template changes."*

Accepted — this is the real hole, and it is not about bytes. Every value
can be individually approved while the **binding between them** is
substituted: a question displayed under catalog revision 3 resolved
against revision 4's answer-to-fact mapping produces a fact the user never
agreed to, out of nothing but valid parts.

**So the question is an immutable record, and resolution reads it.**
Written when the question is asked, never rewritten:

| field | why it is bound at ask time |
|---|---|
| `org_id` | tenant of the asking run |
| `subject` | WHO the resulting assertion is about (§1a) |
| `topic` | catalogue id |
| `catalog_revision` | the revision in force when it was displayed |
| `prompt_shown` | the exact wording the user saw |
| `answers_offered` | the exact options the user saw |
| `fact_mapping` | answer → assertion, frozen at ask time |

Resolution takes an authenticated respondent and an answer **index into
`answers_offered`**. Everything else comes from the record. Nothing
client-supplied replaces any of it, and a revision of the catalog cannot
reach a question already asked.

**The respondent must be authorized for the subject.** A question about
the owner's employment is resolvable by that owner (or an org admin
acting for them), not by whoever can reach the escalation surface. Answer
authority is not the same permission as escalation-resolution authority,
and conflating them was implicit in reusing the escalation resolve path.

**The answer must confirm the assertion, not merely select a word.** The
UI shows the assertion that will be stored — *"You are currently seeking
employment"* — and the user confirms that sentence. A bare enum pick
leaves the user agreeing to a token whose meaning lives in a mapping they
never saw.

**Templates may not interpolate message text or model output.** Only
catalogue-authored literals and the chosen answer. Enforced at catalog
load, not by review: a `fact` template containing a `{trigger.*}` or
`{steps.*}` placeholder is rejected.

**Withdrawn:** *"the worst case is an irrelevant question, not a
manipulative one."* Round 1 is right that this is too broad without the
bindings above. With them it holds for the residual below; without them
it was never true.

**The residual, restated:** a hostile email can still influence *which*
catalogued question gets asked. Bounded by a small operator-authored
catalog and by §3's caps. That is a nuisance channel, not an authority
channel — but the claim now rests on the bindings, not on the catalog
alone.

---

## 3. Constraint 1: the budget, enforced without trusting the classifier

Round 1: *"A conditional with two different permitted outcomes proves only
that the model proposed a difference."* Accepted. The conditional is
**demoted to a relevance heuristic**; the enforceable budget is the caps,
and the design now says so outright instead of leading with the
conditional.

Also accepted, and it was a genuine hole: `ask_user_question(topic)`
receives no conditional, so nothing tied the asked topic to an accepted
one. **The tool is withdrawn.**

**The classifier emits a structured candidate; the ENGINE schedules.**
The classifier keeps `tools: []` — the read-only fence the act plan exists
to preserve — and emits, in its JSON verdict, an optional
`question_candidate: {topic, if_answer, then}` in catalogue vocabulary.
After classification the engine validates it (topic catalogued, branch
values in the topic's enum, subject resolvable) and only then schedules a
question. There is no path from model output to an asked question that
does not pass server-side validation, and no tool call to authorize.

**Caps, at the level the load actually lands.**

| cap | scope | why |
|---|---|---|
| outstanding questions | **per recipient, across all senders and workflows** | per-sender limits do not control the operator's combined load — round 1's point, and correct |
| questions per period | per recipient | a burst ceiling; "once ever" is a total, not a rate |
| once ever | per `(subject, topic)` | a total ceiling, and only while the catalog and dedupe scope are fixed — see §4 on retirement |

### 3a. Scheduling, made atomic — three things round 2 moved before C1

**One transaction, not a reservation protocol.** v2 reserved
`(subject, topic)` and then created the question, which leaves a window:
a run that crashes between the two locks the topic forever under "asked
once ever", and nothing ever asks it. Round 2's preferred disposition,
adopted: **the capacity check, the reservation and the question record
are created atomically**. A crash either leaves nothing or leaves a
complete question. No compensating release to get right, because there is
no interval to compensate for.

*(An expiring reservation with duplicate-safe recovery is the fallback if
a future backend cannot do this in one transaction. Postgres can, so the
fallback is documented and unused.)*

**The capacity check is inside that transaction, and "inside a
transaction" is not by itself an enforcing mechanism** (round 3). A
`(subject, topic)` uniqueness constraint stops two runs asking the same
question; it does **not** stop two runs asking *different* questions that
both take the last recipient slot.

**The enforcement is a conditional UPDATE of one counter row** —
`UPDATE shadow_capacity SET outstanding = outstanding + 1 WHERE
recipient = ? AND outstanding < :cap RETURNING outstanding` — and the
question is created only if that statement affected a row. The second
concurrent transaction **blocks on that row's lock** and then
re-evaluates its predicate against the committed value.

**We first implemented this as a conditional INSERT whose predicate
counted pending rows, and the concurrency test proved it wrong:** under
READ COMMITTED each transaction's subselect reads a snapshot without the
other's uncommitted row, so both saw the last slot and both took it —
2 questions, cap 1, measured. Counting rows cannot serialize; locking one
row can. Round 3 named "a conditional counter update **or** appropriate
locking" and we had implemented neither.

**The test that establishes this is deterministic, not a race.** An
`asyncio.gather` of two schedulers caught the original defect once and
then passed when the defect was deliberately restored — two coroutines do
not reliably interleave inside their transactions, and a race test that
passes is not evidence the race cannot happen. So the invariant is proved
by holding transaction A open past its capacity update and showing B's
identical statement **blocks**; the opportunistic race test is kept
beside it as a smoke check, labelled as such.

**Pending questions expire, and expiry releases capacity.** An unanswered
question cannot hold a slot indefinitely, or the budget deadlocks itself
into never asking again. Two records, deliberately separate:

- **outstanding capacity** — released when a question is answered,
  declined or expires;
- **the "once ever" history** — permanent, keyed `(subject, topic)`, and
  unaffected by expiry. An expired question was still asked.

Collapsing those two is what made v2's cap look self-limiting; keeping
them apart is what lets capacity recover without re-asking.

### 3b. Shadow state is separate state

Round 2: *"identical rules do not require shared mutable state."*
Adopted, and it dissolves the dilemma our sidecar raised. C1 runs **the
same scheduling code** — same validation, same atomic path, same caps,
same dedupe — against **its own reservations, counters and simulated
question lifecycle**. Nothing it does consumes a real slot or a real
"once ever" entry, and nothing about the rules is faked to achieve that.
The shadow lifecycle simulates answer/decline/expiry so recovery
behaviour is exercised rather than assumed.

## 4. The answer lifecycle — before the UI, not after

Round 1 is right that these affect the data model and cannot all wait for
C2.

**Question status is not factual content.** They are two records. A
`declined` question is a preference *about being asked*; it must never
become an employment-status fact. Four states that are routinely
conflated and here are not:

| state | meaning | writes a fact? |
|---|---|---|
| `unanswered` | asked, no response yet | no |
| `declined` | the user refused to answer | no — a preference, stored on the question |
| `answered: unknown` | the user answered, and the answer is "I don't know" | yes, if the catalogue offers it — *"the owner's employment status is unknown"* is a real assertion |
| `expired` | validity elapsed (§7) | no; the prior fact ages out on its own terms |

**The accepted response is persisted durably as the answer record** (§1a)
— that write, and not a memory write, is what makes an answer real. In
the first release there is no ingestion step at all (§6), so nothing can
report an answer as stored when it is not.

**§4a below specifies the ingestion identity anyway**, because round 2
asked for it to be *defined* before C2 and *implemented* before C3, and
because C3b re-enables ingestion once veracium can retire an expired
fact. Defining it now is cheap; discovering it after the answers exist
is not.

### 4a. Ingestion identity — per (question, answer revision, operation)

v2 keyed retries on the question id, and round 2 caught the conflict:
the same design allows multiple answer revisions, so a question-id key
cannot distinguish "retry revision 2" from "ingest revision 3". Three
rules:

- **The operation key is `(question_id, answer_revision, operation)`.** A
  retry of a revision reuses its key and cannot duplicate; a correction
  is a NEW revision and therefore a new operation, not a retry.
- **A superseded revision's operation can never resurrect it.** A delayed
  retry checks that its revision is still current before writing; if the
  answer has been corrected or withdrawn, the operation terminates as
  `superseded` rather than restoring an old fact. Round 2's scenario,
  and without this check a slow retry silently undoes a correction.
- **The write-succeeded-but-marking-failed case is NOT resolved by the
  worker, and v3 said it was.** Round 3: *"§4a still overstates what the
  worker guarantees. It says Veracium will recognize the same operation
  key while also saying `observe()` remains a write without that key."*
  Both cannot be true, and the second is the one that is true —
  `observe()` carries no operation key, so a crash after the downstream
  write succeeds and before completion is recorded leaves a fact with no
  record that it exists, and a re-run duplicates it.

  **Stated as an open prerequisite for C3b, not as solved.** Closing it
  needs either a supported idempotent write on veracium's side (keyed by
  our operation id) or an equivalent recovery protocol — plus ordering
  that prevents an older operation from restoring a superseded answer.
  Both go in the same coordination ask as the expiry primitive (§6).
  Until then C3b does not start, which costs nothing because ingestion
  is deferred anyway.

**Retries are bounded and failure is visible.** N automatic attempts with
backoff, then the question sits in a `ingestion_failed` status that is
shown, not buried, with an explicit operator retry. **The accepted answer
is preserved throughout** — an ingestion failure never discards what the
user said.

**Where this is enforced:** in the answer-record store and the ingestion
worker, NOT in `LearnedMemoryService.observe`. Round 2 is right that the
supplied wrapper establishes none of these guarantees — it takes no
operation key and has no notion of revision. Either it grows one, or the
worker owns the idempotence and `observe` stays a dumb write. **v3
chooses the worker**, so veracium needs no change for the first release.

**Revision, withdrawal, supersession.** A user may correct or withdraw an
answer. A correction writes a new answer revision and supersedes the
prior fact rather than editing it; a withdrawal supersedes without a
replacement. "Asked once ever" binds the QUESTION, not the answer — the
system never re-asks, and the user can always revise. See §6 for how that
reconciles with staleness, which v2 got contradictory.

**Catalog retirement.** Retiring a topic does not retract facts already
written from it: they were true assertions the user confirmed under a
wording that is recorded (§2a). A retired topic stops being askable and
its existing facts keep their own validity. A topic whose `fact_mapping`
is *reworked* is a NEW topic with a new id, never a revision of the old
one — otherwise the frozen mapping in old question records disagrees with
the catalog under the same name.

**"The answer is absent" is a defined check, not a recall miss.** Whether
a question should be asked is decided by querying current, applicable
answers for `(subject, topic)` — not by noticing that a token-bounded
recall slice did not happen to include one. Round 1's point, and it is
the same class as §1a: recall selection is not a membership test.

---

## 5. Measurement — correction rate is a diagnostic, not the evidence

v1 proposed correction rate as the measure. Round 1: *"Low correction
rates can reflect sparse review, easy cases, or answers that never
influenced a decision. Questions also target unusually uncertain cases,
making comparisons with ordinary classifications misleading."* Accepted —
selection bias alone sinks the naive version, because questions are asked
precisely where the model was unsure.

**Recorded links, so influence is traceable rather than assumed:**
question → answer revision → the classification that ran → the context
actually supplied → the later judgment. Three things that are not the
same and were one thing in v1:

- **available** — the profile block was injected for this classification
- **influential** — the verdict differs from the same classification run
  without the block
- **correct** — a later judgment agreed

**Reported alongside, always:** review coverage (what fraction of
classifications were ever judged) and unanswered-question rate. A
correction rate without its coverage is a number whose denominator is
hidden.

**The identities are captured when the events happen, not reconstructed
later.** Round 2: the comparison harness and the analysis can wait for
C4; the evidence they need cannot be added afterwards. So C2 records the
question↔answer-revision link as it resolves, and C3 records the
supplied-context link (which answer revisions were in the profile block
for this classification) as it classifies. C4 builds the analysis on
records that already exist.

**To establish benefit, compare matched cases** — the same messages
classified with and without the elicited context, or a controlled pilot —
and report improvement **against question burden and latency**, because a
question that improves accuracy and annoys the operator twice a day has
not obviously paid. None of this needs the per-edge use-recording loop
removed on 2026-09-19 (`docs/SEMANTICS.md`); the links above are
per-question, not per-edge.

**Why v1 got this wrong:** it inherited *"PR #9 outcome tracking is how
the system learns which question types pay for themselves"* from the
backlog, noticed only that `times_used` was gone, and substituted the
nearest available number. Replacing a broken measure with a cheap one is
not the same as measuring.

---

## 5a. What changes in the workflow — and a rubric mismatch to settle first

**While a question is pending, classification proceeds.** Holding the run
would make an unanswered question a stalled mailbox, and the act path
applies labels add-only. The triggering message is classified without the
answer.

**Answers affect future messages only, in the first release.** Revisiting
the triggering message means re-classifying and reconciling an
already-applied label, and the label path is **add-only** — it can add
`wf/x` but the design has no retraction story. Re-classification is a
separate decision with its own reconciliation semantics; it is not
smuggled in here.

**The rubric mismatch, which is a product question and not a mechanism
one.** `EMAIL_TRIAGE_TWO_AXIS_PLAN` defines `review` around *consequential
matters requiring verification or decision*. "The owner is seeking
employment" makes a job alert **more relevant** — it does not make it
consequential. Marking it `review` would quietly redefine the axis.

Three candidate outcomes, and the first release must pick one:

| outcome | what it means | cost |
|---|---|---|
| **attention** | elicited context can add `review` | redefines an axis the two-axis plan pinned |
| **personal priority** | a NEW, separate signal — relevance to the owner | a third axis, and its own label namespace |
| **rubric** | the catalogue answer changes the category rubric itself | most powerful, least reversible |

**Recommendation: personal priority**, as a separate signal, so the
attention axis keeps the definition it was given.

**But a recommendation is not a vocabulary, and C1 needs one.** Round 2:
*"Personal priority remains a recommendation, while the opening example
still changes `review`. C1 needs a defined meaning and validation rule
for `then`, even if the production product decision comes later."*
Correct — and our own round-2 sidecar had flagged the same thing as
"a recommendation masquerading as a decision" without then fixing it.

**The C1 experimental vocabulary, fixed now and explicitly provisional:**

```
then: { priority: relevant | not_relevant }
```

One enum, one field, deliberately NOT `attention` and deliberately not
the category. Validation rule: `then.priority` must be one of those two
values, and a candidate naming anything else is rejected by the same
validator that checks the topic id. The opening example is corrected to
match — *"seeking employment makes a job alert `priority: relevant`"*,
not `review`.

This is an **experimental output vocabulary for C1**, not the product
decision. It exists so the shadow log records something with a defined
meaning; whether the shipped signal is a third axis, a priority field or
a rubric change stays open, and C1's data is what informs it. Naming it
provisionally is what stops C1 quietly deciding it.

---

## 6. Answer validity — governing BOTH routes, and the contradiction fixed

Round 1 separated platform validity from veracium's inferred volatility.
Round 2 found that v2 applied it to only one of the two routes by which
an answer reaches a classification.

**The two routes.** (1) The profile block, assembled from answer records
(§1a). (2) Ordinary correspondent recall, which can surface the ingested
observation. v2's validity policy governed (1) only — so an answer whose
distilled volatility outlived our `valid_until` would vanish from the
profile block and **remain eligible through recall**, which is the
failure round 2 describes.

**We wrote a rule here that depended on a primitive that does not
exist.** The v3 draft said the memory edge is "retired at the source at
expiry/withdrawal/supersession". Executed: veracium exposes
`Memory.correct(user_id, edge_id, corrected_value, …)` — which supersedes
an edge *and records a replacement value*, with
`invalidation_reason="corrected"` — and `revoke_source(...)` for
source-level revocation. **There is no primitive for "this fact simply
expired, retire it"**, and `LearnedMemoryService` wraps neither of them.
So supersession-by-a-new-answer maps onto `correct()`; expiry and
withdrawal-without-replacement do not map at all.

**Rule (v3), which dissolves the problem instead of working around it:
elicited answers are NOT ingested into ordinary memory in the first
release.**

- **One route, not two.** The answer record (§1a) is the source of truth
  and the profile block is assembled from it. With no ingestion there is
  no second route, so "validity governs both routes" is satisfied by
  there being one. Expiry, withdrawal and supersession act on the answer
  record, where we control the semantics completely.
- **What is lost:** elicited answers do not enrich the correspondent
  subgraph, and veracium's history does not carry them. That is a real
  cost and it is the right one to pay first — the alternative is
  ingesting facts we have no way to retire on our own terms, which is how
  a withdrawn answer keeps influencing decisions.
- **What re-opens it:** an expiry/retire primitive on veracium's side
  (raised as a coordination item alongside the explicit `volatility` on
  ingest from §4). With that, ingestion returns as a later stage and the
  two-route rule above becomes implementable as first drafted.
- **Historical evidence is still preserved** — in the answer records,
  which are append-only revisions with supersession, not edits.

**The contradiction, resolved.** §4 said the user is never re-asked; §6
(v2) defined `review_after` as "re-ask or re-confirm". Both cannot hold.
Round 2's suggested first-release choice is adopted and it is the right
one:

> **No automatic repeat questions.** An answer past `review_after` gets a
> visible **stale** status, and reconfirmation is **user-initiated**. The
> system never re-asks on its own.

So `review_after` marks staleness, it does not schedule a question. A
stale answer stays current for decisions until it expires or is revised —
staleness is a prompt to the human, not a change of fact.

**Longevity is not usefulness — v1 conflated them.** v1 made only
`durable`-or-longer topics askable, reasoning that a short-lived fact is
not worth a question. Round 1's counterexample is decisive: event
attendance is short-lived and useful across many messages in the window
it covers. Usefulness is *reuse count within validity*, not duration.

**First release: owner-level profile questions only.** Employment status,
role, working pattern — things about the owner that hold across senders,
which is also what §1a's answer-record read is shaped for.
**Event-scoped questions need an explicit event scope** (which event,
valid until when, how a message is matched to it) and that scope does not
exist; they are out of the first release rather than approximated.

---

## 6a. The INDIRECT route — found by round 3, and v3's "one route" was wrong

v3 claimed that deferring ingestion leaves a single route to
classification. **It does not.** Round 3 read the workflow and found the
second one, and it is already live:

```yaml
- text: >
    The triage agent classified the email ... Reason: {steps.record.summary}
  author: system
  derived_from: third_party
```

`steps.record.summary` is the classifier's own free-text reason. Once a
profile answer reaches the classifier, that reason can repeat it —
*"relevant because the owner is seeking employment"* — and the sentence
is written into learned memory as an ordinary observation. **It then
outlives the answer record**, and ordinary correspondent recall can
return it after the answer expires or is withdrawn.

`derived_from: third_party` constrains how that observation is *treated*;
round 3 is right that it does not establish the observation can never
influence a later classification. Our "one route" claim was about the
route we built and blind to the route already there.

**Decision, before C3 — the simple one.** A workflow with questions
enabled **omits profile-dependent classification observations** from
learned memory. The independent received-email observation is kept; the
triage-summary observation is dropped for that workflow. Blunt, and it
cannot leak what it does not write.

*(The capable alternative — track which answer revisions a classification
consumed and enforce their validity at retrieval — is what C3b's
dependency tracking would give us. It is not first-release work.)*

**Acceptance case, required before C3:** answer supplied → the
classification explanation repeats it → answer withdrawn → a later
classification receives no usable copy through **either** the profile
block **or** learned-memory recall.

---

## 6b. Retention — decided before C2 persists a real answer

Round 3's framing is adopted: **decision eligibility and physical
retention are separate questions**, and "append-only revisions" means
revisions are not silently rewritten, not that answer content lives
forever.

| record | first release |
|---|---|
| current answer | retained while applicable and the feature is enabled for that subject |
| expired / superseded | **excluded from current context immediately**; payload kept for a bounded review window — **30 days**, a pilot starting point and not a load-bearing number |
| withdrawn | excluded immediately; payload deleted on a documented schedule |
| "already asked" history | subject · topic · status · timestamps, **no answer content**, for as long as the feature is associated with that subject — this is what makes "once ever" survive payload deletion |
| audit / trace / export / backup | **stated explicitly rather than assumed**: deleting the answer row does not remove these. Audit entries carry the question and outcome, never the answer payload; the vault is not used for answers; exports exclude payloads; backup expiry is the operator's retention policy and is named in the runbook, not implied. |

**Read authority is its own permission.** Who may read current answers,
and who may read historical payloads, are decided separately from who may
resolve an escalation — round 3's point, and the same mistake §2a already
corrected for answering.

**Changeable topics carry a finite `valid_until`.** A stale badge nobody
notices must not license indefinite use, so for topics like employment
status the catalogue sets an expiry as well as `review_after`. Expiry
excludes the answer from context; it does not re-ask (§6).

## 7. The asking channel

The escalations plumbing is the right channel and is nearly right:
`RequestHumanReviewTool` → `GET /api/escalations` → `POST
/api/escalations/{id}/resolve` → dashboard panel, all audited.

What it lacks is structure: `reason`/`context` are free text and
resolution is a free-text string. G12 needs **topic id in, enum answer
out**. Extend the escalation record with an optional `question` block
(`topic`, `prompt`, `answers`) and accept a validated `answer` on resolve,
rather than building a parallel surface — the plumbing, the dashboard and
the audit trail are already there.

**No new tool. Withdrawn in v2** — see §3. v1 gave the classifier an
`ask_user_question(topic)` tool; round 1 pointed out it received no
conditional, so nothing tied the asked topic to an accepted one. The
classifier now emits a structured candidate in its JSON verdict and keeps
`tools: []`, and the engine schedules after validating. That is a
stronger privilege split than the tool was: there is no authorized call
at all.

---

## 8. Stages

| | what | gated on |
|---|---|---|
| **C1** | catalog + candidate emission + validation + the real scheduling code against **shadow state** | §3a atomic scheduling · §3b shadow state · §4a identity grouping · §5a candidate vocabulary — **the four round-2 decisions, all now taken** |
| C2 | question record (§2a), subject binding (§1b), escalation surface, authorized respondent, enum answer + assertion confirmation, **question↔answer-revision links recorded as they happen** | authorization (§1b) |
| C3 | answer records as source of truth, profile assembly (§1a), validity on the single route (§6), **supplied-context links recorded as they happen** | retrieval, validity and revision guarantees |
| C3b | memory ingestion + the two-route validity rule | **blocked**: needs a veracium expiry/retire primitive (§6) |
| C4 | matched comparison and the analysis (§5) | C2/C3 evidence existing |

**What C1 can and cannot establish** — round 2 narrowed this and the
narrower claim is the honest one:

| C1 CAN show | C1 CANNOT show |
|---|---|
| candidate frequency | that a question is valuable |
| suppression reasons and their distribution | that the caps are set correctly |
| cap enforcement, including the recipient-slot race | user benefit |
| duplicate prevention under concurrency | |
| recovery behaviour (crash, expiry) | |
| whether a proposed catalog produces excessive demand | |

The limits for the experiment are **explicit and configurable**, and the
report says what they were and what they did. That is engineering
evidence about the mechanism; it is not a claim that the numbers are
right. *(Our round-2 sidecar asked whether C1 was worth running given it
calibrates nothing. Round 2's answer — it was never meant to; exercise
the rules, do not prove benefit — is the correct reading of its own
round-1 instruction, and we had over-read it.)*

**Answer-lookup failure is distinguished in C1, not C3.** Round 2 put
profile-read failure at C3 but noted the same distinction is needed for
answer lookups in C1: a lookup that FAILS must not be recorded as "no
current answer", or the shadow log's suppression counts are wrong in the
one direction that looks like success.

**Required before C2/C3 are enabled** — worked examples, each written
down and each exercised:

1. successful answer → answer record → fact
2. decline → no fact, preference recorded
3. expiry → gone from the eligible-answer lookup and the profile block
   *(retirement from recall belongs to C3b — there is nothing in recall
   to retire while ingestion is deferred)*
4. catalog change → an in-flight question resolves under the wording it
   was asked with
5. two concurrent runs, same subject → one question; two concurrent runs,
   **different topics, last recipient slot** → one question (§3a)
6. withdrawal → no usable copy through the profile block **or**
   learned-memory recall (§6a's acceptance case)

**C3b only** — deferred with ingestion, and listed so they are not lost:

7. memory-write failure → answer preserved, bounded retry succeeds
   without duplicating
8. crash after the downstream write, before completion is recorded →
   converges without duplicating *(open: needs the idempotent-write
   prerequisite, §4a)*
9. correction after a delayed retry → the old revision does not
   resurrect

---

## 9. Deliberately not doing

- **Free-text questions.** §2.
- **Asking on the acting path.** The act step holds a mutating tool and
  minimal inputs; adding a question there widens the surface that design
  exists to keep narrow.
- **Auto-asking with no budget**, or re-asking a declined topic.
- **Inferring context instead of asking.** That is the passive
  accumulation this item exists to complement, not replace.
- **A second memory namespace.** v1's sidecar implied one was needed; it
  was reasoning from a misreading (§1a). The owner's partition is already
  the right home.
- **Reinterpreting `learned_memory.user_id` as an identity.** §1b — the
  memory namespace contract is left alone and authority comes from a
  separate binding.
- **Using the mailbox pseudonym for authorization.** It is an audit
  correlator (`TRACE_SUBJECT_IDENTITY_PLAN`), not an authz mechanism and
  not a directory key.
- **Automatic re-asking.** §6 — staleness is visible, reconfirmation is
  user-initiated.
- **Treating recall output as confirmed answers.** §1a — the answer
  record is the source of truth, and in the first release it is the ONLY
  place answers live: memory carries no answer history, because
  ingestion is deferred (§6).
- **Writing profile-dependent classification summaries to memory.** §6a
  — the indirect route round 3 found, closed by omitting that
  observation for question-enabled workflows.
- **Event-scoped questions**, in the first release. §6.
- **Re-classifying the triggering message.** §5a — the label path is
  add-only and retraction is not designed.
- **Correction rate as the evidence that asking paid off.** §5. It stays
  as a diagnostic.
