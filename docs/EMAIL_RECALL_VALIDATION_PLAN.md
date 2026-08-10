# Email-triage per-entity recall — validation experiment (design)

**Status:** design, for review. **Author:** platform session, 2026-08-10.
**Companion:** `docs/SEMANTICS.md` "Adopted: veracium"; `examples/email_triage_live/`.

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
