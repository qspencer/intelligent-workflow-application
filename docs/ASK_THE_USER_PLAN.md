# Ask-the-user: clarification elicitation (G12)

Status: **design v2, 2026-09-19** — revised after external review round 1
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

The idea, from the 2026-07-19 labelling session: the system should be able
to **ask for the one context fact that would change its answer**, instead
of only passively accumulating context. Indeed job alerts are routine
notifications — unless you are out of work. An event thread classifies
differently if the system knows you plan to attend.

---

## 1. The shape, in one paragraph

The classifier already produces a verdict. It may additionally declare a
**conditional**: *"notification — but if `employment_status = seeking`
then attention gains `review`"*. A conditional naming a topic the operator
has catalogued, whose answer the system does not have, becomes a
**question**. The question text is the operator's, the answer is chosen
from the operator's enum, and the fact written to memory is composed by
the engine from those two. Nothing the email contains reaches any of it.

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

**Decision: elicited owner facts are retrieved by their own read, not by
the correspondent query.** A second, fixed-query recall (`profile`) whose
result is injected as a separate, labelled block. It does not compete for
the correspondent budget, and it is present for every sender by
construction rather than by luck of subgraph selection.

**Worked example — one answer, two correspondents, a fact about neither.**
The owner answers `employment_status = seeking`. Subject: the owner.
Partition: the owner's. Stored once. A later Indeed alert and a later
LinkedIn alert are classified by different correspondent queries; both
receive the same profile block. Neither `indeed.com` nor `linkedin.com`
gains an edge, and nothing about either correspondent was asserted.

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

**Reservations, because runs are concurrent.** Scheduling takes a
reservation on `(subject, topic)` before the question is created, so two
concurrent runs for the same subject cannot both ask, and a retried run
re-uses its reservation instead of asking twice. Dedupe keyed on the
reservation, not on a scan of existing questions — a scan races.

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

**Ingestion is a separate, retryable step, and resolution does not imply
it.** The accepted response is persisted durably FIRST, then memory
ingestion is attempted. A resolved question carries `ingested: false`
until the write lands, and a failed write is retryable from the stored
response. Retry is keyed on the question id so a second attempt cannot
create a second observation. **A resolved question must never silently
imply the fact exists** — that was implicit in the v1 staging and it is
the same "begins ↔ completes" counterpart the ledger keeps finding.

**Revision, withdrawal, supersession.** A user may correct or withdraw an
answer. A correction writes a new answer revision and supersedes the
prior fact rather than editing it; a withdrawal supersedes without a
replacement. "Asked once ever" binds the QUESTION, not the answer — the
user is never re-asked, and can always revise.

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
attention axis keeps the definition it was given. Worked examples
distinguishing the three belong in the C1 shadow log before the choice is
locked.

---

## 6. Answer validity, kept separate from inferred volatility

Round 1: template tests *"cannot enforce the lifetime of every future
distilled fact"*, and longevity is not usefulness.

**The accepted answer carries its own validity policy**, on the question
record, independent of whatever volatility veracium's distiller infers
from the fact text. `review_after` (when to re-ask or re-confirm) and
`valid_until` where the catalogue can state one. The distilled volatility
governs veracium's internal ageing; our policy governs whether we still
believe the answer. They are allowed to disagree, and when they do, ours
decides whether the profile block still carries it.

**Longevity is not usefulness — v1 conflated them.** v1 made only
`durable`-or-longer topics askable, reasoning that a short-lived fact is
not worth a question. Round 1's counterexample is decisive: event
attendance is short-lived and useful across many messages in the window
it covers. Usefulness is *reuse count within validity*, not duration.

**First release: owner-level profile questions only.** Employment status,
role, working pattern — things about the owner that hold across senders,
which is also what §1a's profile read is shaped for. **Event-scoped
questions need an explicit event scope** (which event, valid until when,
how a message is matched to it) and that scope does not exist; they are
out of the first release rather than approximated.

---

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

| | what | notes |
|---|---|---|
| C1 | Catalog + candidate emission + validation + **the real dedupe, reservation and cap rules** | **Shadow only.** Logs what it WOULD ask, having passed every check that would gate a real ask. |
| C2 | Question record (§2a), escalation surface, authorized respondent, enum answer + assertion confirmation | needs the worked examples below |
| C3 | Durable response → retryable ingestion → the fact; validity policy (§6) | |
| C4 | The measurement links and a matched comparison (§5) | |

**C1 runs the real rules, not a sketch.** Round 1's instruction, and it
is the difference between a shadow log that measures the budget and one
that measures an intention.

**Required before C2/C3 are enabled** — worked examples, each written
down and each exercised:

1. successful answer → fact
2. decline → no fact, preference recorded
3. expiry → the profile block stops carrying it
4. catalog change → an in-flight question resolves under the wording it
   was asked with
5. two concurrent runs for one subject → one question
6. memory-write failure → resolved, `ingested: false`, retry succeeds
   without duplicating

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
- **Event-scoped questions**, in the first release. §6.
- **Re-classifying the triggering message.** §5a — the label path is
  add-only and retraction is not designed.
- **Correction rate as the evidence that asking paid off.** §5. It stays
  as a diagnostic.
