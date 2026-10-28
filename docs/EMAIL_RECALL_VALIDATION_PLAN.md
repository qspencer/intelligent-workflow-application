# Email-triage per-entity recall — validation experiment (design)

**Status:** design, review round 1 folded. **Author:** platform session, 2026-08-10.
**Companion:** `docs/SEMANTICS.md` "Adopted: veracium"; `examples/email_triage_live/`.

## R1 — review round 1 folded (2026-08-10). These SUPERSEDE the body below.

An independent design review found the v1 design would produce an
**uninterpretable** number. Corrections, all adopted:

1. **Human labels are MANDATORY, not an optional guard (the load-bearing fix).**
   The label-free change-direction analysis measures the *mechanism* (does ON pull
   toward the first-seen label) but **cannot** tell good anchoring (consistent
   *correct* sender) from bad anchoring (a propagated *wrong* first label) — they
   have identical toward-first signatures. So consistency is uninterpretable
   without correctness, and correctness needs labels. The set is small and cheap:
   **the first message of every repeat sender** (is the anchor right?) **plus the
   changed-answer subset** (~40 + δ messages). The operator hand-labels these
   before the result is read. No labels → the run yields a *consistency-only*
   number that is explicitly **not** a verdict on recall.
2. **Pin temperature = 0.** The classifier ran at default temperature; pinning to
   0 collapses most model non-determinism, which is the cleanest attack on the
   noise problem. Set in the step `inference_config`. (Residual noise still
   measured, below.)
3. **The noise floor is a SPLIT-RATE floor, not a per-message flip rate.** A
   per-message flip rate `p` does not compare to a split rate over sender *sets*:
   a consistent sender of `m` messages splits from noise alone with prob
   `1-(1-p)^m`. **Translate** the measured `p` into an expected split-rate floor by
   simulating over the *actual* sender-size distribution, and draw the K-repeat
   sample from **real repeat-sender messages** (flip rate is heterogeneous). The ON
   split-rate must beat this simulated floor, not zero.
4. **The guard sets must use K-repeat MAJORITY VOTE.** "ON changed vs OFF" and the
   changed-answer subset are themselves noise-contaminated (a noise flip reads as a
   change). Compute each (message, arm) label as the majority over K repeats so the
   guard operates on *stable* labels.
5. **Judge, if used as a proxy, must be de-correlated, blinded, and calibrated.** A
   same-family judge (Haiku over Haiku) blesses the classifier's *systematic*
   errors — exactly the plausible-but-wrong anchors we hunt. Use a **stronger,
   different** model (Opus), **blind** it to the recalled context and to which arm
   produced the answer, and **report judge↔human agreement on the mandatory labeled
   set**; only trust the proxy where agreement is high.
6. **Path-dependence breaks the sender-independence CI.** ON's store accumulates
   ON's own noisy labels, so sender outcomes are serially correlated through the
   shared store — a binomial CI over senders is too narrow. Use a **paired McNemar**
   over senders for the on/off delta and **state that the CI understates path
   variance**; where budget allows, run the full corpus under **≥3 orderings/seeds**
   and report the spread.
7. **Pre-register the decision knobs + a power/MDE calc.** Before running: fix the
   accuracy tolerance (**accuracy(ON) ≥ accuracy(OFF) − 2pp** on the changed set),
   the test (one-sided McNemar, α=0.05), and drop "purely anchoring" as
   non-diagnostic (per #1). **Compute the minimum detectable effect at the actual
   repeat-sender N first** — at ~40 senders (~7 split) a 7→4 drop is inside noise;
   if the plausible true effect < MDE, scaling the corpus is the only fix, and a
   small run is exploratory-only by construction.
8. **Freeze the corpus; do not re-tune on it; stratify by sender difficulty.** The
   inbox that *motivated* the 18% finding is circular if reused for tuning — freeze
   it. And personal-inbox repeat senders skew to easy automated senders
   (newsletters/notifications); report the split-rate **stratified by category /
   difficulty** so a "win" on the easy denominator isn't overgeneralized.

**Net effect on scope:** this is no longer a $5 one-shot. It needs operator labels
on ~40 messages, K-repeat runs (temperature 0 keeps K small), and a power check
that may demand a larger corpus. The honest options are **(a)** run it properly
(labels + power + majority-vote + simulated floor), or **(b)** run a clearly-marked
*exploratory, consistency-only* pass that cannot conclude recall "works."

**Harness status (as built, `tools/validate_email_recall.py`):** it implements
**(b) only** — the exploratory pass, with the corrected pieces that don't need
operator input (temperature pinned unconditionally, failures excluded from the
metrics with an abort rule, recall hit-rate over eligible 2nd+ messages counting
only non-empty recalls, and the simulated *split-rate* noise floor). The
verdict-only machinery — operator labels + accuracy guard, K-repeat majority vote,
paired McNemar, MDE/power, corpus freeze, difficulty stratification — is **NOT
built**, so `--verdict` **fails closed** rather than emitting a false verdict.
Turning on the verdict path is the scoped follow-up gated on operator labels.

---
*(Original v1 design follows — superseded by R1 where they conflict.)*

---

## 1. The question (and the trap)

veracium per-entity recall is **adopted and wired** into `email-triage-live`
(`learned_memory.recall.query_from: trigger.from_address.address`), but it has
**never been demonstrated to work in practice** — every validation run so far is
*stateless* (the batch builds a fresh in-memory store per message), so recall has
nothing to recall. A live 100-message batch under the correct two-axis rubric
measured an **18% stateless same-sender split rate** (3 of 17 repeat senders
classified inconsistently). The claim we want to test:

> **H1:** Persistent per-entity recall reduces the same-sender split rate on real
> mail, **without degrading correctness.**

**The trap this design exists to avoid.** Recall injects *"you previously
classified this sender as X"* into the prompt. A model that simply **copies its
own prior answer** becomes more *consistent* while being no more *correct* — and
if the sender's **first** classification was wrong, recall **propagates that error
to every later message**. So *"the split rate dropped"* is **not** a success
result on its own. Consistency that comes from anchoring on a bad first guess is a
regression, not a win. **The experiment must separate consistency from
correctness, and must be able to detect anchoring/error-propagation.** Any harness
that only reports the split rate is measuring the wrong thing.

## 2. Design

### 2.1 Arms (within-subjects — same messages, same order)

| arm | store | recall injection | observe/write after each run |
|---|---|---|---|
| **OFF** (baseline) | persistent scratch | **disabled** | on (so the store is populated identically) |
| **ON** (treatment) | persistent scratch | **enabled** | on |

Both arms use a **persistent scratch store** and the **same observe path**, so the
*only* difference is whether the recalled sender context is injected into the
triage prompt. That isolates the **recall-injection** effect from mere store
persistence. Each arm gets its **own fresh scratch store** (reset between arms) so
OFF is never contaminated by ON.

> Scratch store only. The experiment MUST NOT write to the production
> `WORKFLOW_PLATFORM_LEARNED_MEMORY_DB` — it uses a temp DB discarded at the end.
> Mail content enters veracium as `third_party` (quarantined) exactly as in
> production; that invariant is not relaxed for the experiment.

### 2.2 Online / sequential protocol

Process messages in **ascending `received_at`** order. For message *N*, recall
reflects **only** messages *1..N-1* — the real online setting. This matters: a
sender's first message has no history (recall is empty), so recall can only affect
the 2nd+ message from a sender. Report first-vs-subsequent separately (§3).

### 2.3 Corpus

Real inbox from `qspencer@gmail.com`, fetched read-only. Repeat senders are the
only informative rows, so scale the corpus until there are enough: ~100 messages
gave 17 repeat senders; target **≥40 repeat senders** (≈250–400 messages) for a
split-rate estimate with a usable confidence interval.

## 3. Metrics

**Primary — consistency (per arm):**
- `split_rate` = (# senders with ≥2 messages whose classifications are not all
  identical) / (# senders with ≥2 messages).
- Report the paired delta `split_rate(OFF) − split_rate(ON)` and a CI.

**Guard — correctness (the crux; without it the primary is uninterpretable):**
Recall must not buy consistency with accuracy. Two complementary reads:
1. **Anchoring / change-direction analysis (label-free, always run).** For every
   message whose ON-classification differs from its OFF-classification, record the
   *direction*: did ON move the label **toward the sender's first-seen label**
   (anchoring signal) or **away** from it? A recall that only ever pulls toward the
   first label is anchoring; a recall that sometimes corrects a later message
   against an earlier consensus is doing real work. Report the split of changes.
2. **Accuracy vs a reference label set (when labels exist).** Compare each arm's
   classification to a ground-truth label per message and report accuracy(ON) vs
   accuracy(OFF), **overall and restricted to the messages where ON changed the
   answer**. Reference labels come from, in preference order: (a) a human-labeled
   subset, (b) the existing LLM-judge (`tools/judge_email_triage.py`) as a proxy —
   clearly marked as proxy, since a judge is not ground truth. If neither is
   available, the accuracy guard is **not met** and the experiment yields only a
   *consistency* result explicitly caveated as such.

**Noise floor (control for model non-determinism):** the classifier is live
Bedrock — the same message re-run can flip categories (observed: `fyi`↔`unparsed`
across runs), so *some* same-sender "inconsistency" is model noise, not
statelessness. Measure the floor: re-run a sample of **single** messages K times
under OFF and report the intra-message flip rate. **The ON improvement is only
meaningful if it exceeds this floor.** (Alternative: record/replay the OFF arm to
zero out its noise; live is preferred for realism, so we measure the floor
instead.)

**Secondary:** recall hit rate (fraction of 2nd+ messages where recall injected
non-empty sender context), overall change rate (ON vs OFF), per-category flows.

## 4. Decision rule

Recall is judged to **improve triage in practice** iff **both** hold:
1. `split_rate(ON) < split_rate(OFF)` by a margin exceeding the measured noise
   floor (and the CI excludes zero); **and**
2. correctness is **not degraded** — accuracy(ON) ≥ accuracy(OFF) within tolerance
   on the changed-answer subset, **and** the change-direction analysis does not
   show recall acting purely as first-label anchoring.

If (1) holds but (2) fails → **recall is anchoring, not helping** → do not claim
value; the finding is that per-entity recall trades correctness for consistency on
this workload, which is a decision-grade *negative*. If neither holds → recall has
no measurable effect here. Both negatives are as valuable as the positive — the
point is a verdict, not a confirmation.

## 5. Harness to build (`backend/tools/validate_email_recall.py`)

- **Inputs:** `--account`, `--corpus-dir` (fetched messages), `--repeat-only`
  (skip singletons for speed), `--noise-floor-k` (re-runs for the floor),
  `--judge` (use the LLM-judge as proxy reference).
- **Mechanics:** builds a `WorkflowEngine` + `LearnedMemoryService` on a **temp
  scratch DB**; runs both arms sequentially over the ascending-`received_at`
  corpus; ON uses `email-triage-live` (recall block intact); OFF uses the same
  workflow with the `recall` block stripped (a config toggle, same rubric + same
  observe block, so the only delta is injection).
- **Per-message record:** id, sender, received_at, arm, category, confidence,
  recall_injected (bool) + recalled-context digest (hash only — no raw), changed
  vs OFF, first-label-for-sender.
- **Aggregate output:** split_rate per arm + delta + CI; change-direction table;
  noise floor; recall hit rate; accuracy vs reference if `--judge`/labels.
  Written as JSON + a printed summary. **No raw mail content in the output**
  (sender address + subject only, consistent with the read-only posture).
- **Teardown:** delete the scratch DB.

## 6. Threats to validity (summary)

| threat | mitigation |
|---|---|
| **Anchoring / first-error propagation** | change-direction analysis + accuracy guard on changed answers; report first-vs-subsequent |
| Model non-determinism inflates baseline splits | measure the noise floor; require ON to beat it |
| Store persistence ≠ recall injection | OFF arm keeps the persistent store + observe path; only injection differs |
| Reference labels absent | LLM-judge as *proxy* (marked), or consistency-only result explicitly caveated |
| Small N of repeat senders | scale corpus to ≥40 repeat senders |
| Order sensitivity | fix ascending received_at; note as a follow-up to test order robustness |
| Scratch-store safety | temp DB, never the prod learned store; `third_party` quarantine intact |

## 7. Out of scope (named follow-ups)

- Sent-mail observation / awaiting-reply correctness (a state gap, not memory —
  per SEMANTICS).
- Cross-account or Postgres-backed store.
- Automated rubric tuning from the results.
- Production wiring changes — this is a measurement harness, not a workflow change.

## 8. Cost

~`$0.008/message × N × (2 arms + K noise-floor re-runs)`. A 300-message corpus,
both arms, K=20 floor ≈ **$5–6**. Bounded and one-off; gate a large run on a small
smoke first.

---

## Pre-registration: the Oct-2026 catch-up corpus

**Written 2026-10-28, BEFORE any message in this window was classified.** The
point of writing it first is the rule we already hold on the veracium side: *a
freeze must fix every degree of freedom that could be chosen differently after
seeing outcomes.* A run examined after the fact can always be made to say
something; this records what would count as a finding while nobody knows the
answer.

### What the corpus is

Mail to `qspencer@gmail.com` from **2026-10-16 14:07:27Z** (the `email-triage-apply`
cursor, i.e. the last message the platform processed) to whenever it is captured —
about twelve days. It exists because the platform was down: the Gmail refresh token
for that account was revoked around 10-16, the backend then crash-looped from the
10-24 reboot, and nothing processed mail in between.

### Which instrument answers which question (revised 2026-10-28, before any data)

The first draft of this section had it wrong, and the correction matters enough to
record rather than quietly fix. It proposed classifying the corpus offline with
`run_email_triage_batch.py`. **That tool cannot answer Q2 or Q3**: it wires only
`MemoryManager` (the rubric Markdown) and never constructs a
`LearnedMemoryService`, so recall never fires in it. A question about whether
memory anchors or pays rent cannot be answered by an instrument with no memory.

So the two halves run on different instruments, and the cursor is NOT advanced:

- **Q1–Q4 come from the LIVE catch-up run.** On re-consent the `email-triage-apply`
  trigger resumes from its 2026-10-16 cursor and processes the window itself:
  real workflow, recall ON, production store, sequentially in arrival order. That
  is the production regime these questions are about, it needs no extra step, and
  the structured results land in `step_executions.output`. Accepted cost: `wf/*`
  labels are written to ~12 days of already-handled mail (add-only, allowlisted,
  reversible).
- **Q5 and every replay come from a CAPTURED corpus**, because the live path's
  inputs are withheld and vaulted (`{"_withheld_keys": true}`) and so cannot be
  re-run. `label_email_recall.py` reads bodies from the captured JSON, and
  `validate_email_recall.py` takes `fixtures: list[Path]` and runs each arm against
  its own **scratch** store — the OFF/ON instrument, and the one for G18's sweep.

Capture is read-only and independent of the cursor, so it is simply done first:

```
fetch_gmail_inbox.py --account qspencer@gmail.com \
    --since 2026-10-16T14:07:27Z --oldest-first \
    --query "-in:sent -in:draft -in:chat" --out-subdir catchup-2026-10 --count <n>
```

Three deliberate choices, each with a reason:

- **`--oldest-first`** so file order is arrival order. Half the questions below are
  about order; a newest-first corpus cannot answer them.
- **`--out-subdir`** so this corpus cannot overwrite
  `data/email_triage/qspencer@gmail.com/` — 100 files whose bodies the existing
  55-message label CSV reads back by `message_id`. The filenames are
  `NN_<slug>.json` and would collide.
- **`--query` matching the trigger's own scope** (`-in:sent -in:draft -in:chat`), so
  the captured corpus and the live run cover the same population. If they diverge,
  Q5's accuracy estimate stops describing the run that Q1–Q4 measured.

### Baseline fingerprint (so results stay attributable)

| | |
|---|---|
| platform commit | `5b48e34` |
| veracium | 0.27.1, store schema v16 |
| runtime | Python 3.14.7, SQLite 3.45.1 |
| model | `us.anthropic.claude-haiku-4-5-20251001-v1` |
| rubric | `examples/email_triage_apply/agent_memory.md`, sha256 `3bef3b8b35614a77`, 234 lines |
| learned store at T0 | **7154 edges / 75370 episodes** |
| store snapshot | `.memory/backups/learned.db.PRE-CATCHUP-BASELINE.20261028T212823Z` |

The snapshot is the load-bearing artifact. It is what makes the identical corpus
re-runnable with **recall OFF** later, which is the properly-powered version of the
OFF/ON comparison the 55-message crux set could not deliver (split 0.176 → 0.125,
McNemar needing ≥5 same-way flips at N=17). Once the live run writes observations,
that baseline is unrecoverable.

### Questions, with the query and what would count as a finding

Q1–Q4 need **no labels**. That is the whole reason they are listed first.

| # | Question | Measure | Would be a finding |
|---|---|---|---|
| **Q1** | Is the classifier self-consistent? | For senders appearing ≥3 times: share of the modal category | Modal share **< 0.8** on senders with no plausible category change — unreliability independent of correctness |
| **Q2** | Do earlier verdicts anchor later ones? | For repeat senders, compare first-third vs last-third verdicts in arrival order | A **directional** shift toward the first verdict. Must be compared against the recall-OFF replay — the 55-message run's mixed-sender flag blamed recall before checking that OFF collapsed too, and that error changed the verdict's meaning |
| **Q3** | How often does memory actually fire, and does it move anything? | `recall` + `memory_hash` on each agent step output; verdict with vs without a non-empty recall block | Recall firing on **> 50%** of messages while changing **< 2%** of verdicts = the G10 injection is paying rent it does not earn |
| **Q4** | Mechanical health at volume | `parse_ok=False` rate, `no_output` rate, off-schema relation rate, cost and latency per message | Parse failure **> 5%** (the harness's own exclusion ceiling); off-schema materially above the measured 0.08%; triage latency back above ~60s (the act-time-use regression signature) |
| **Q5** | Accuracy — **the dominant open question** | Stratified sample of ~100 from this pool, labeled blind via `label_email_recall.py`, compared to the classifier | Agreement **at or below ~60%** confirms base accuracy as the lever and not memory. Materially above 60% means the earlier estimate was an artifact of the repeat-sender-biased crux set |

### Stated in advance, so it cannot be discovered conveniently later

- **This is not an i.i.d. eval sample.** It is one sequential run in which the
  classifier's own observations accumulate, so message *n* is classified under
  memory written by messages *1..n-1*. Q1/Q2 exist because of that; Q5's accuracy
  number inherits it and is an estimate for *this* regime, not for a cold store.
- **Q5 requires the operator's time** and nothing else here does. If the labeling
  does not happen, Q1–Q4 still stand on their own.
- **The sample is one mailbox over twelve particular days.** Seasonality (end of
  October), a backlog of unread promotional mail, and whatever arrived while the
  account sat idle are all confounds. Distribution drift against the earlier
  139-message corpus is therefore **descriptive, not a regression signal**.
- **Any question added after seeing the data gets recorded as such.** Not
  forbidden — exploratory findings are how the last two real defects were found —
  but labeled, so an exploratory result is never reported as a pre-registered one.

### Afterwards, at no extra fetch cost

The same corpus is the input for **G18's model sweep** (Haiku / Sonnet / Opus, ~$6
total over a corpus this size). That is the cheapest available test of whether the
*model* is the lever on Q5's gap. Running the sweep on a corpus captured for a
different purpose is legitimate precisely because the corpus was frozen before any
of its numbers were seen.

