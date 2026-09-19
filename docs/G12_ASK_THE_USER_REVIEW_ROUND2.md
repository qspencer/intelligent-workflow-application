# G12 ask-the-user — design review, round 2 sidecar

**Still a design, still no code.** Round 1 returned it for revision with
seven decisions owed. All seven are taken in
`docs/ASK_THE_USER_PLAN.md` v2; none is disputed, because none was wrong.

| | |
|---|---|
| Design | `docs/ASK_THE_USER_PLAN.md` (v2) |
| Round-1 sidecar | `docs/G12_ASK_THE_USER_REVIEW_ROUND1.md` — **its §4 finding 4 is marked WRONG in place** |
| Protocol addition this round caused | `docs/REVIEW_FINDINGS_LEDGER.md` step **2c** |
| Gates | five green (nothing built; recorded for form, not offered as validation) |

---

## 1. The round-1 finding we got wrong, and what it cost

Our package asserted that elicited facts would land in the
**correspondent's** memory partition, called it *"what may be the real
design error"*, and led with it. It is false.
`learned_memory.user_id` is the mailbox **owner** and the correspondent is
the recall **query**.

**We asserted a defect in our own system from a misreading of our own
workflow file, inside the section headed "what we would find if paid to
fail this".** Protocol step 2 — execute every claim — had been run
against the design's claims and not against the sidecar's own, and §4 is
made of factual sentences like any other section. That is now step **2c**:
*a self-found finding is a claim; execute it.*

What it cost was one reviewer paragraph, and only because the underlying
problem turned out to be real and broader. That is luck, not process.

---

## 2. The seven decisions, and where each landed

| # | round-1 requirement | v2 |
|---|---|---|
| 1 | separate ownership / subject / recall query | §1a — three roles tabled; elicited owner facts get their own fixed **profile read**, because recall is query-driven and an owner fact is not reliably selected by a correspondent query. Worked example: one answer, two correspondents, a fact about neither. **No second namespace** — moved to the not-doing list. |
| 2 | bind answer to immutable question + authorized respondent | §2a — the question record freezes org, subject, topic, catalog revision, wording shown, answers offered, fact mapping; resolution reads that record and an answer index; the user confirms the **assertion**; templates may not interpolate message text, enforced at catalog load. |
| 3 | budget enforceable without the classifier | §3 — the conditional is demoted to a heuristic and the caps are the budget, said outright. **`ask_user_question` withdrawn**; the classifier emits a structured candidate and keeps `tools: []`, the engine validates and schedules. Caps move to per-**recipient** across senders and workflows; reservations cover concurrency and retries. |
| 4 | answer lifecycle before the UI | §4 — status separated from factual content; `unanswered` / `declined` / `answered: unknown` / `expired` distinct; durable response **before** ingestion, retryable, keyed on the question id; resolved never implies ingested; revision supersedes; retirement does not retract; "answer absent" is a defined check. |
| 5 | workflow effects + the rubric mismatch | §5a — classification proceeds while pending; answers affect future messages only (the label path is add-only). The rubric question is tabled with three candidate outcomes; **recommendation: a separate personal-priority signal**, so `review` keeps its pinned definition. |
| 6 | correction rate is a diagnostic | §5 — `available` / `influential` / `correct` kept distinct; coverage and unanswered rate always reported; benefit by matched comparison against burden and latency. |
| 7 | validity separate from inferred volatility | §6 — explicit validity policy on the answer, allowed to disagree with the distiller. **"Longevity = usefulness" withdrawn.** First release: owner-level profile questions only. |

---

## 3. Counterpart enumeration over v2's NEW mechanisms (step 0)

v2 adds four mechanisms that did not exist in v1 — the question record,
reservations, retryable ingestion, and the profile read. Running the list
over them found five gaps. **None is fixed; all are listed.**

| pair | state |
|---|---|
| reserve ↔ **release** | ⚠️ A run that crashes after reserving `(subject, topic)` and before creating the question locks that topic **forever** — "asked once ever" with nothing ever asked. Reservations need a TTL or a compensating release. |
| outstanding ↔ **released from outstanding** | ⚠️ The per-recipient outstanding cap has no expiry path. Questions nobody answers accumulate against the cap until asking stops entirely — the budget deadlocks itself, quietly. |
| ingest retry ↔ **give up** | ⚠️ §4 makes ingestion retryable and never says when it stops. Infinite retry or a dead-letter; unspecified is neither. |
| profile read ↔ **profile read FAILS** | ⚠️ The correspondent recall degrades to no-memory on failure and audits. v2 does not say the profile read does the same — and a silent absence is indistinguishable from "the owner never answered", which §4 was careful to make distinguishable everywhere else. |
| C1 shadow ↔ **the real rules it is supposed to exercise** | ⚠️ Round 1 asked for shadow logging *using the actual dedupe and budget rules*. If shadow takes real reservations it consumes "once ever" for questions never asked; if it does not, it is not exercising the rule. v2 says "runs the real rules" without resolving this. |

The first two are the same shape — an acquire with no release — and both
would present as *the feature quietly stopping*, which is the failure mode
hardest to notice in production.

---

## 4. What we would find if paid to fail v2 (step 7 — claims executed)

1. **§2a requires authorizing a respondent "for the subject", and the
   subject cannot be authorized against anything.** `LearnedMemorySpec.
   user_id` is a free `str` in the YAML (`qspencer@gmail.com`), and it
   **never joins to the `users` table** — executed: no `users.get` or
   `get_by_login_email` call exists anywhere under `engine/` or
   `memory/`. So "the owner may answer questions about themselves" has no
   join to evaluate. This is the same distinction G-Trace-Subject-Identity
   drew between `subject_from_user_id` and a mailbox key, arriving from
   the other direction. **We think this must be settled before C2**, and
   we have not settled it.
2. **§5a's recommendation is a recommendation, not a design.** "A separate
   personal-priority signal" is one line; a third axis needs its own label
   namespace, rubric wording, act-path allowlist entry and dashboard
   treatment, none of which exist. Presenting it in a decision table
   risks it reading as decided.
3. **C1 validates our guesses, not the caps.** Shadow logging shows what
   volume our invented numbers produce. It cannot show the caps are
   right — only that they are or are not binding. Calibration needs real
   answers, which needs C2, which the caps were supposed to gate.
4. **The matched comparison in §5 may not be affordable.** Classifying the
   same messages with and without the profile block doubles Bedrock spend
   on the compared set and needs a harness that does not exist. v2 says
   "matched cases or a controlled pilot" without saying which is
   feasible here. Honest position: this is a C4 research task with its
   own cost, not a reporting feature.
5. **The profile block is a new injection point into the same prompt that
   reads hostile mail.** Its content is operator-authored plus a
   user-confirmed answer, so no attacker bytes — but it is one more
   labelled block in a prompt whose fencing discipline now carries three
   sources. Not a defect; a surface worth naming before it grows a
   fourth.

---

## 5. Claims executed (step 2 — including this sidecar's own, per 2c)

| claim | check | result |
|---|---|---|
| `learned_memory.user_id` is the owner; correspondent is the query | read the workflow YAML | confirmed — `qspencer@gmail.com` / `trigger.from_address.address` |
| live rows carry the owner namespace | `select distinct detail->>'user_id'` | `org:default:user:qspencer@gmail.com` |
| recall is query-driven and budget-bounded | `mem.recall(ns, q, token_budget=600)` for three queries | 40 edges / 12 episodes each |
| the classifier holds no tools | read the YAML | `tools: []` and `capabilities.tools: []` |
| the label path is add-only | `EMAIL_TRIAGE_ACT_PLAN` §… | *"the tool path only adds labels (`addLabelIds`)"* + no-create fence |
| two-axis defines `review` as consequential | read the plan | *"verify/decide when convenient; consequential but not time-critical"*, with a narrow operational definition added against over-application |
| `observe()` / `remember` take no volatility | `inspect.signature` on both | confirmed |
| user-authored facts are the assertable class | read `Edge.assertable` / `quarantined` / `use_only` | confirmed, mechanism named |
| **§4.1: `spec.user_id` never joins to `users`** | grep for `users.get` / `get_by_login_email` under `engine/`, `memory/` | **no call exists** |

---

## 6. What we want from round 2

1. **The five counterpart gaps in §3** — are any of them load-bearing
   enough to block C1, or are they all C2 work?
2. **§4.1, the subject-authorization gap.** We believe it blocks C2. Is
   the right answer to require `learned_memory.user_id` to resolve to a
   platform user for any workflow that enables questions, or to carry an
   explicit `subject` on the catalogue?
3. **Is C1 worth running** given §4.3 — that it calibrates nothing? We
   think yes (it makes volume observable and exercises the plumbing) but
   the reviewer's ordering rested partly on C1 validating the budget, and
   it cannot.
