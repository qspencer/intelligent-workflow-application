# Ask-the-user: clarification elicitation (G12)

Status: **design, 2026-09-19.** Both start triggers are met — the two-axis
split landed 2026-07-26, outcome emission 2026-07-19. Effort M–L, so this
is a plan first, per the same convention as `AUTH_PLAN` / `ROLES_PLAN` /
`EMAIL_TRIAGE_ACT_PLAN`.

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

## 2. Constraint 2 first, because it decides the whole design

> **Questions are an injection surface.** Question generation derived from
> third-party mail is tainted content; a hostile email must not be able to
> induce a manipulative question or smuggle framing into the USER-authored
> answer fact.

This is the harder constraint and it is not a filter problem. The answer
lands in veracium as `author="user"` — the **highest trust class, and
assertable**. A path from email text to a user-authored fact is a
privilege escalation from `third_party` to `user`, which is the exact
boundary `EMAIL_TRIAGE_ACT_PLAN` spent its design on for labels.

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

**The residual, stated rather than waved at:** a hostile email can still
influence *which* catalogued question gets asked. That is bounded by the
catalog being small and operator-authored — the worst case is an
irrelevant question, not a manipulative one — and further bounded by the
budget in §3. It is a nuisance channel, not an authority channel, and the
distinction is the whole point.

---

## 3. Constraint 1: the question budget

> **Ask only when the answer is durable/reusable AND classification is
> sensitive to it. Otherwise it's a nagging machine.**

Two gates, both structural rather than advisory:

**Decision-relevance is proved by construction, not asserted.** A question
exists only where the classifier emitted a conditional naming the topic —
so "would the answer change the verdict?" is answered by the verdict
itself carrying both branches, in enum vocabulary. A model that cannot
express the conditional in the catalog's terms produces no question.

**Reusability is the topic's property, not the model's.** Each catalogue
entry declares its volatility; only `permanent` / `durable` / `slow`
topics are askable. An `ephemeral` topic is by definition not worth a
question — you would be asking again next week.

**Hard caps, because both gates are about a single question and nagging is
a rate:** at most one question per run, N per sender per period, and a
topic is asked **once ever** — answered or declined, it is never re-asked.
A decline is itself a recorded fact (`declined`), so silence and refusal
are distinguishable.

---

## 4. Where the answer lives, and one thing veracium does not expose

The answer becomes `observe(..., author="user")` — correct, and the only
class in veracium whose facts are assertable rather than fenced.

**`LearnedMemoryService.observe` has no `volatility` parameter**, and
neither does veracium's ingest: volatility is inferred by the distiller
from the fact text (`Volatility.PERMANENT` … `EPHEMERAL`, defaulting to
`DURABLE`). So a catalogue entry's declared volatility is, today, a hint
to whoever writes the `fact` template rather than something the platform
can enforce.

Two ways forward, and the second needs the other side:
1. **Phrase for the distiller and test it** — pin each catalogue entry's
   inferred volatility in the suite, so a template that distils to the
   wrong class fails the build rather than expiring wrongly in production.
2. **Ask veracium for an explicit `volatility` on ingest** — the right
   fix, and a coordination-ledger item rather than something to work
   around here.

Start with (1); raise (2).

---

## 5. Measurement — and what changed under it today

The backlog entry says *"PR #9 outcome tracking is how the system learns
which question types actually pay for themselves"*.

**That story has to be rebuilt on judgments, not uses.** Act-time
`unreviewed` use recording was removed on 2026-09-19 — it was quadratic
and self-feeding (`docs/SEMANTICS.md`), so `times_used` no longer
accumulates. Judgment outcomes (`corrected`, review labels, judge
verdicts) are unaffected and still recorded.

So question value is measured as: **of the classifications that consumed
an elicited fact, what fraction were later corrected?** That is a judgment
rate, which is what "paid for itself" actually means — a use count never
did.

---

## 6. The asking channel

The escalations plumbing is the right channel and is nearly right:
`RequestHumanReviewTool` → `GET /api/escalations` → `POST
/api/escalations/{id}/resolve` → dashboard panel, all audited.

What it lacks is structure: `reason`/`context` are free text and
resolution is a free-text string. G12 needs **topic id in, enum answer
out**. Extend the escalation record with an optional `question` block
(`topic`, `prompt`, `answers`) and accept a validated `answer` on resolve,
rather than building a parallel surface — the plumbing, the dashboard and
the audit trail are already there.

**The tool the classifier holds is NOT `request_human_review`.** A
separate `ask_user_question(topic)` whose only parameter is a catalogue
id, so the free-text escalation path and the fact-writing path cannot be
confused — the privilege-split shape from `EMAIL_TRIAGE_ACT_PLAN` §3.

---

## 7. Stages

| | what | notes |
|---|---|---|
| C1 | Question catalog in the YAML + conditional emission + `question_topic_rejected` gate | **No asking.** Log what it WOULD ask and measure the rate against the budget before anyone is nagged. |
| C2 | `ask_user_question` tool, escalation `question` block, enum answer on resolve, dashboard control | |
| C3 | Compose the fact engine-side and write it `author="user"`; volatility pinned by test | |
| C4 | Correction-rate measurement per topic (§5) | |

C1 first is the load-bearing ordering: it makes the budget observable
before it is spent, and a catalogue that asks nothing is a catalogue you
can tune without apologising to anyone.

---

## 8. Deliberately not doing

- **Free-text questions.** §2.
- **Asking on the acting path.** The act step holds a mutating tool and
  minimal inputs; adding a question there widens the surface that design
  exists to keep narrow.
- **Auto-asking with no budget**, or re-asking a declined topic.
- **Inferring context instead of asking.** That is the passive
  accumulation this item exists to complement, not replace.
