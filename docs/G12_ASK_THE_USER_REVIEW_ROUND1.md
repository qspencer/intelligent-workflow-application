# G12 ask-the-user — design review, round 1 sidecar

**Scope: a DESIGN, no code.** The artifact under review is
`docs/ASK_THE_USER_PLAN.md`. Nothing is built, so the pre-package
protocol's build-shaped steps (forgery, round-trip, predicate agreement,
corpus) have nothing to run against. The steps that DO apply to a design —
0 (counterparts), 2 (execute every claim), 7 (fail it yourself) — were
run, and §3 and §4 below are their output.

| | |
|---|---|
| Design | `docs/ASK_THE_USER_PLAN.md` |
| Backlog entry | `docs/NEXT_STEPS.md` § G12 |
| Precedent it copies | `docs/EMAIL_TRIAGE_ACT_PLAN.md` (privilege split, enum gating) |
| Gates | five green (nothing to break; recorded for form) |

---

## 1. What we want reviewed

Two questions, in priority order.

**(a) Is the injection answer actually sufficient?** The design's claim is
that an operator-authored question catalog, with the classifier emitting
only a topic id, leaves the USER-authored fact free of attacker-influenced
bytes. We think that is right and we have stated the residual (topic
*selection* is still steerable). What we want is someone trying to find a
third channel we have not named.

**(b) Is the budget real or merely stated?** "Ask only when the answer is
decision-relevant and reusable" is easy to write. Our mechanism is that a
question can only exist where the classifier emitted a *conditional*
naming a catalogued topic, so relevance is carried by the verdict rather
than asserted by the model. We would like that attacked: is a conditional
gameable, and does it actually bound question volume?

---

## 2. What changed under this design while it was being written

Recorded because it invalidates a sentence in the backlog entry that a
builder would otherwise inherit.

The entry says *"PR #9 outcome tracking is how the system learns which
question types actually pay for themselves."* On 2026-09-19 act-time
`unreviewed` use recording was **removed** — it was linear in the episode
count and 95% of the episodes it scanned were the use records it had
itself written, so cost was quadratic in cumulative volume
(`docs/SEMANTICS.md`). `times_used` therefore no longer accumulates.

Judgment outcomes are unaffected. §5 of the design rebuilds the
measurement on **correction rate** instead, which is what "paid for
itself" always meant.

---

## 3. Counterpart enumeration (protocol step 0)

The M9 question — for each pair, which side does the design specify?

| pair | specified? |
|---|---|
| ask ↔ **answer** | both: `ask_user_question(topic)` → escalation `question` block → validated `answer` on resolve |
| ask ↔ **decline** | both: a decline is recorded as a fact, so silence and refusal are distinguishable |
| ask ↔ **never answered** | ⚠️ **NOT specified.** An escalation that is never resolved: does the topic stay un-askable forever, or become askable again after a period? The design says "asked once ever", which silently chooses the first. Flagged, not resolved. |
| write ↔ **read** | the fact is written `author="user"` and read by ordinary recall; no new read surface |
| catalogue entry ↔ **retired entry** | ⚠️ **NOT specified.** What happens to facts already written from a topic the operator later deletes or reworks? |
| answer ↔ **changed answer** | ⚠️ **NOT specified.** The user's employment status changes. Volatility expiry is veracium's mechanism, but the design does not say whether a user may revise an answer, nor how that reconciles with "asked once ever". |
| one surface ↔ **all surfaces** | escalations list, detail and the dashboard panel all serve this; the design extends the existing record rather than adding a surface, so the three move together |
| succeeds ↔ **fails** | ⚠️ partially. The design does not say what the classification does while a question is outstanding — proceed with the unconditioned verdict (and re-classify later?) or hold the run? |

**Four unspecified counterparts, found by running the list rather than by
review.** They are exactly the shape the ledger predicts: lifecycle
questions about a thing that does not exist yet. We would rather the
reviewer spend their attention on §1 than re-find these, so they are
listed as known gaps for C2, not defended.

---

## 4. What we would find if paid to fail this (protocol step 7)

1. **The conditional is model output.** The budget rests on the classifier
   emitting a conditional only when one genuinely applies. A model that
   emits conditionals liberally turns the budget into a rate limiter and
   nothing more. The enum vocabulary bounds *what* it can say, not *how
   often*. Our honest position: the hard caps are the real budget and the
   conditional is a relevance heuristic — the design should probably say
   that outright rather than leading with the conditional.
2. **C1 measures a proxy.** "Log what it would ask" measures question
   *volume*, not question *value*. Value needs C4, which needs answers,
   which needs C2. So the staging cannot validate the make-or-break
   constraint before committing to the surface — an honest limit of the
   ordering, not a flaw we can design away.
3. **Volatility is inferred from text we wrote.** Pinning the distilled
   class in the suite catches drift in OUR templates; it does not catch
   veracium changing its inference. The pin is against our side only.
4. **`user_id` here is a mailbox namespace, not a person.** The fact is
   written into `org:<org>:user:<mailbox>` — the correspondent's
   partition. But an elicited fact is about **the operator**, not the
   correspondent. ⚠️ **This may be the real design error**: employment
   status belongs to the mailbox owner and would be written into whichever
   correspondent's partition triggered the question. The design does not
   address it and we think it has to before C1.

**Finding 4 is ours, found in this pass, and we have not fixed it** — it
may change the shape of C3 (a second namespace for operator-owned facts,
or an explicit subject on the write). We would rather hand it over honestly
than repair it in the same hour we found it and present a design nobody
has slept on.

---

## 5. Claims executed (protocol step 2)

Every factual claim in the design about the current codebase was run
before it was written.

| claim | how it was checked | result |
|---|---|---|
| `observe()` takes no `volatility` | `inspect.signature` | confirmed — 8 params, none |
| veracium's ingest takes none either | `inspect.signature(Memory.remember)` | confirmed |
| volatility enum + default | read `veracium.schema` | `permanent · durable · slow · transient · ephemeral`, default `DURABLE` |
| user-authored facts are assertable | read `Edge.assertable`, `quarantined`, `use_only` | confirmed **and the wording corrected** — see below |
| escalations plumbing exists | read tool + router | `RequestHumanReviewTool`, `GET /api/escalations`, `POST /resolve` |
| start triggers met | `NEXT_STEPS` + git | two-axis 2026-07-26, outcome emission 2026-07-19 |

**One claim was wrong as first written.** The draft said user-authored
facts are "the highest trust class, and assertable". `assertable` is a
property of the EDGE — `active ∧ ¬quarantined ∧ ¬use_only ∧ valid_now` —
and author drives it *indirectly*: third-party claims are quarantined by
relation, third-party inferences are `use_only`. Right conclusion, wrong
mechanism; corrected in the design with the mechanism named. Logged here
because the protocol exists to catch exactly this, and it did.


---

## 6. A defect in this package's own provenance

The commit that added this sidecar (`bace662`) has a mangled message: it
was written with `-m` in double quotes and the shell expanded the
backticks, so the word it was quoting — the name of the property being
discussed — was executed as a command and dropped from the text. The
sentence reads *" is a property of the EDGE"*.

Recorded rather than rewritten, because the commit is pushed. Noted here
because this sidecar cites commits as evidence, and a reader who pulls
`bace662` to check the claim should not have to wonder whether the gap is
meaningful. It is not: the missing word is `assertable`, and every other
commit today used a heredoc, which is the reason this is the only one.
