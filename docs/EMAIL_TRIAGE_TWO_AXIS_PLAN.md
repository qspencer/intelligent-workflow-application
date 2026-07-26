# Email Triage, Two-Axis (G11 phase 2) — Design

Status: **proposed** (drafted 2026-07-26; internal design review same
day: adopt-with-conditions, folded. **External review same day: adopt
with design changes, folded** — its core catch: the attention enum
recreated the very collision the split exists to remove; attention is
now multi-valued. One external recommendation declined with reasons, §7b.
Not yet built). Executes
`docs/NEXT_STEPS.md` G11 with its four pinned collision examples; the
rubric-only phase promised in `EMAIL_TRIAGE_ACT_PLAN` §7, designed against
what the acting pipeline actually is now (input-minimized apply step,
enum gates, `wf/*` allowlist, veracium observations).

## 1. Problem, restated from the evidence

The seven-bucket taxonomy mixes two orthogonal axes — what mail **is**
(source) and what it **demands** (attention) — resolved today by a
precedence rule that the evidence has outgrown:

1. **personal × urgent** (dews7 spoofing warning): precedence keeps
   `urgent`, *loses the personal-sender fact* to the summary.
2. **notification × review** (PayPal $5.34): unresolvable by precedence —
   the mail states no demand; importance depends on user context.
3. **notification × review** (ManifestRx order confirmation): same shape.
4. **awaiting-reply is a state, not a category** (the baseball email):
   true at arrival, false after the user replies — needs lifecycle, which
   no category can carry.

Corollary already recorded in G13: the *category* axis is largely
sender-determined and codifiable; the *attention* axis is
content/state-dependent and never codifiable. The split is what makes both
G13 and (later) G12 elicitation well-posed.

## 2. The split

### 2a. Category becomes pure source (5 buckets)

`category ∈ {personal, notification, newsletter, promotion, spam}` — what
the mail IS. `urgent` and `awaiting-reply` **retire as categories**; they
were always attention values wearing category clothes. This is the honest
form of the split: the halfway option (keep 7 buckets, add a flag) leaves
collision #1 unresolved — `category: urgent` still erases *personal*.

### 2b. Attention is its own field — **multi-valued** (external-review correction)

`attention: list` with values from `{urgent, awaiting-reply, review}`;
empty list = no demand (the common case). The first draft's single enum
(`urgent|awaiting-reply|review|none`) recreated the original failure one
level up: the values describe *different properties* (time sensitivity /
response obligation / verification need) and genuinely coexist — the
dews7 warning is urgent AND awaiting a reply; an order discrepancy needs
review AND a reply. A single enum would have forced a second precedence
rule and lost information again.

- `urgent` — time-sensitive: act now (dews7; provider security notices).
- `awaiting-reply` — the sender expects a reply (Samara).
- `review` — verify/decide when convenient; consequential but not
  time-critical (PayPal, ManifestRx). **Narrow operational definition
  (external review, anticipating over-application):** apply only when
  the message reports a consequential transaction, account change,
  security condition, commitment, or order the user should explicitly
  verify — with no immediate deadline and no explicit reply request.
  Negative exemplars in the rubric (these matter more than positives):
  routine delivery notifications, ordinary receipts matching expected
  purchases, account digests, marketing "review our offer", newsletters
  saying "check out".

Admissible combinations: any subset, with one dominance rule —
**`urgent` subsumes `review`** (if both emitted, `review` is dropped;
urgency already implies the user will look). `awaiting-reply` composes
freely with either.

### 2c. Labels

- Source: `wf/<category>` — now five.
- Attention: `wf-attn/<value>` per list element — up to two after
  dominance (`urgent`+`awaiting-reply` max); **no label for an empty
  list** (absence is the common case; labeling it would be noise).
- Existing `wf/urgent` + `wf/awaiting-reply` Gmail labels stay on
  historical messages (true at the time they were applied); they simply
  stop being applied. New-vocabulary labels are pre-created by the
  updated `setup_triage_labels.py` (5 + 3); the tool allowlist becomes
  exactly those eight (§4).

## 3. Lifecycle (the state problem, scoped tightly)

Only `awaiting-reply` gets lifecycle management in this phase — it is the
one value falsified by a specific, detectable event (the user's reply).
`urgent`/`review` also decay, but their staleness is low-harm (Gmail's
read state already signals handled mail); their decay is a recorded
deferral, not built.

- **Check-then-label (arrival/backfill guard).** The Gmail poll trigger
  annotates each delivered message with `already_replied: bool`. This is
  **new connector surface, named** (design-review finding — the connector
  has no thread fetch today): `GmailConnector.thread_has_newer_sent(
  thread_id, than_internal_date)` on `threads.get` with
  **`format=metadata`** — labelIds + internalDate, plus the
  `In-Reply-To`/`References` metadata headers (headers only, never
  bodies) for precise reply linkage where present. The format
  restriction is load-bearing: a default `threads.get` would pull full
  bodies of the whole thread into the trigger. **Tri-state result
  (external-review correction — the boolean was fail-open for a
  precision-critical value):** `reply_status ∈ {replied, not_replied,
  unknown}`; lookup failure ⇒ `unknown`, which **suppresses
  `awaiting-reply`** (category unaffected, delivery never blocked) and
  is counted/logged so the retirement sweep can reconsider — an API
  blip must not manufacture a response obligation. Honesty note: a
  newer SENT message in the thread is a *heuristic*, not proof the
  specific message was answered (multi-question threads, Gmail's
  grouping); the `References` linkage upgrades it where headers exist,
  and the residual false-retirement risk is documented on the method.
  Quota/latency at current volume negligible. For fresh mail the answer
  is almost always false (cheap insurance); for **backfilled** mail —
  restarts, the G9 catch-up path, exactly where the baseball email came
  from — it is the fix. Sits in the trigger because that's the component
  that already holds Gmail access; deterministic steps still can't reach
  connectors, and the apply step stays minimized.
- **Retirement (the sweep).** `tools/retire_attention_labels.py`
  (operator CLI): list messages carrying `wf-attn/awaiting-reply`,
  thread-check each (grouped by thread — one fetch per thread, not per
  message), **remove** the label where a reply now exists. **Dry-run by
  default** with an audit report; `--apply` executes. Each retirement
  optionally records a veracium *resolution* observation (system-
  authored: "user replied to <sender>'s message of <date>") so memory
  holds the demand's lifecycle as dated events — arrival verdicts are
  never rewritten (append-only; the observation history already carries
  `date_from`), and later G12 aggregation can distinguish "sender often
  awaits replies" from "a pile of stale unresolved flags".
  Requires a new `GmailConnector.remove_labels` — which, like
  `create_label`, is **CLI-reachable only**: `EmailLabelApplyTool` keeps
  its add-only path untouched (the fence from ACT_PLAN §5 holds; label
  removal never becomes an agent capability). Scheduled execution of the
  sweep is deferred (it's the §2b function-capability story again);
  ad-hoc CLI runs are proportionate at current volume.

## 4. Mechanics, end to end

- **Classifier output** (rubric change): `{"category": <5>,
  "attention": [<values>], "category_confidence": …, "summary": …}` —
  one call, same cost class. Confidence is scoped to the category axis
  (a single ambient `confidence` over two independently-interpreted
  axes answers nothing — external review); the extractor accepts the
  legacy `confidence` key as an alias; no self-reported attention
  confidence (deterministic validation + human review stay
  authoritative).
- **`triage_schema_version: 2`** stamped on record outputs and in the
  veracium verdict observation (external review): the era
  discriminator for analytics and the G13 filter — value-compatibility
  across rubric *wording* changes (which churn `memory_hash`) without
  conflating vocabularies. `memory_hash` remains the exact-repro key.
- **`record_email_triage`** (additive): extracts `attention`; computes
  `attention_valid = attention ∈ ATTENTION_LEVELS and not (attention ==
  "awaiting-reply" and trigger.already_replied)`; `category_valid` now
  checks the 5-bucket vocabulary. New computed output **`apply_labels: list[str]`** with the axes
  **genuinely independent** (external review caught the first draft
  contradicting itself — "independently gated" followed by
  invalid-category ⇒ empty list): the category element appears iff
  `category_valid`; each attention element appears iff the whole
  attention list is vocabulary-clean (one hostile element poisons the
  attention *axis* only — intra-axis conservatism, inter-axis
  independence; a steered category never blocks a valid attention flag
  and vice versa). Adversarial note: a valid-but-wrong attention value
  from steered content is a classifier-steering risk that exists
  regardless of gating structure; the enum gates protect the
  *tool-holder's prompt channel*, and the eight-label allowlist stays
  enforced at the tool boundary, not only in workflow config.
- **Apply step gets *more* minimized, not less:** `inputs` becomes
  `[steps.record.apply_labels, trigger.message_id]` — the tool-holder no
  longer even sees a category, just a pre-computed, enum-derived label
  list to pass through in **one** tool call (both labels in one
  `apply_labels` invocation — the tool's list parameter already supports
  it, so cost stays at the two-turn floor). Edge condition becomes
  **`steps.record.apply_labels != []`** (external-review correction:
  conditioning on `category_valid` alone would discard a valid
  attention flag whenever the category was malformed — asymmetric,
  contradicting independence).
- **Constants:** `TRIAGE_CATEGORIES` → the five source buckets;
  `ATTENTION_LEVELS` new. The per-account tool allowlist in `main.py`
  (currently derived from `TRIAGE_CATEGORIES` alone) becomes the
  explicit eight: `wf/*` × 5 + `wf-attn/*` × 3. The functions-module comment about historical
  taxonomies coexisting (already true for 5-bucket-era rows) extends to
  this era; stored rows are never rewritten, analytics tolerate mixed
  vocabularies keyed by `memory_hash`.
- **Veracium observations:** the verdict template gains attention: "…
  classified as {category} (attention: {attention}) …", same
  `system`+`derived_from: third_party` provenance. Recall context
  therefore accumulates per-sender attention history — which is G12's
  raw material later.
- **Both example rubrics migrate** (`email_triage_apply` live;
  `email_triage_live` as the rollback artifact stays vocabulary-consistent
  with it), with the four collision examples written in as rubric
  examples.

## 5. Continuity: corpus, evidence, judge

- **Ground-truth corpus (154 labels)** stays 7-bucket — it validated the
  old vocabulary and is not rewritten. A small fresh two-axis label pass
  (~30 messages via the review CLI, which gains an attention prompt) is
  the phase-2 calibration set; the old corpus's source-bucket labels
  (139 of 154) remain directly comparable for category parity.
- **G13 unanimity evidence needs an era filter, defined now**
  (design-review finding: veracium's V4 counters are cumulative — with
  no filter, old `urgent`/`awaiting-reply` edges would *permanently
  poison* those senders' unanimity, not merely delay it). The G13
  qualifying query counts only verdict edges whose category value is in
  the **current 5-bucket vocabulary** (equivalently: era-scoped via the
  rubric `memory_hash` recorded as `context_ref` on outcome events).
  Under that filter the 15 old-vocabulary messages are excluded rather
  than disqualifying; the big codification candidates (Barron's,
  weather.com — all source-bucket history) carry over untouched.
- **Judge/eval tooling** (`judge_email_triage`, `review_triage`,
  `reclassify_triage`) get vocabulary updates in the same change — but
  judge *calibration* against the new axis waits for the fresh label
  pass (§7).
- **`tools/label_from_ground_truth.py` is frozen at the 7-bucket era**
  (design-review finding: it validates the corpus against
  `TRIAGE_CATEGORIES` and would silently drop the 15 urgent/
  awaiting-reply labels under the new constant). It gains its own
  era-pinned vocabulary list + a docstring note; it already served its
  one-shot purpose and is not re-run routinely.

## 6. What this deliberately does not do

- No urgent/review decay lifecycle (deferral, trigger: staleness
  observed to mislead in practice).
- No scheduled retirement sweep (CLI only; scheduling = the
  function-capability story).
- No G12 elicitation — but the attention axis is its prerequisite, and
  per-sender attention history in recall is its designed-for substrate.
- No UI changes beyond what falls out of category badges already being
  vocabulary-driven (`categoryClass` gains the attention chip rendering
  in a later IA cut; the merged home's "needs attention" strip remains
  IA_PLAN's noted future landing).
- No change to the read-only fence, capability grants, or the apply
  step's tool surface.

## 7. Validation (window part 2)

Supervised on the live mailbox after cutover of the updated rubric +
labels. Arithmetic honesty (design-review finding): at the ~9/day INBOX
residue, ≥50 live messages ≈ **5–6 uptime days**, and rare values
(`urgent`) may see zero live instances in-window — the offline pass splits into a
**30-message development set** (rubric tuning) and a **separate ~30
held-out acceptance set labeled after the rubric freezes** — the set
that tunes the rubric cannot also be the evidence it works (external
review); the live window is deployment monitoring, not the only test. Expect `review`
over-application and seed the rubric with review-vs-none *negative*
exemplars up front (the paper-triage `case_study` precedent, 54%→8%
over three rubric rounds, says over-application of the judgment-shaped
bucket is the default failure mode):

1. **Category parity** against the 5-bucket vocabulary (spot-checks +
   the fresh 30-message two-axis label pass): target ≥ the phase-1 bar
   (no regression from the axis split).
2. **Attention precision over recall, with numeric gates** (external
   review — "defensible" is an operator principle, not a release gate):
   zero false `urgent` in the acceptance set; `review` and
   `awaiting-reply` precision ≥ 80% on acceptance (reported as raw
   numerator/denominator — class counts are small); zero
   `awaiting-reply` where `reply_status == replied`; `unknown`
   reply-status cases logged separately, never counted as ordinary
   negatives; per-label counts reported so an aggregate can't hide a
   failed rare class. Missed flags remain rubric-tuning input, not
   blockers: false urgency erodes trust faster than missed urgency.
3. **Zero out-of-allowlist writes** (now 8 labels), zero apply-cost
   regression (still one tool call).
4. **`already_replied` correctness**: backfilled messages with existing
   replies must not receive `wf-attn/awaiting-reply` (the baseball
   regression test, live).

Rollback (corrected by design review — the naive version was false):
reverting the rubric ALONE is broken once `TRIAGE_CATEGORIES` is
5-bucket — a reverted rubric emitting `urgent` would hit
`category_valid=False` and silently skip apply for two of seven
buckets. **Rollback = revert the code constant + rubric together** (one
revert commit; they ship in one commit precisely so one `git revert`
restores coherence). Labels applied meanwhile remain true-at-time.

**§7b — external recommendation declined, with reasons:** a runtime
`EMAIL_TRIAGE_SCHEMA_VERSION` flag (both vocabularies shipped, flag
selects) was recommended for operational rollback. Declined as
disproportionate for a single-deployment platform: it doubles the
tested surface (two rubrics, two constants, flag-conditional seeding)
to save one `git revert` + restart measured in minutes, and the things
a code revert genuinely can't undo — applied labels, schema-2 rows,
observations — are already tolerated as mixed-era data made analyzable
by `triage_schema_version` stamping (adopted above). Revisit if this
ever runs as a multi-tenant service where restarts are expensive.

## 8. Test plan (unit, fake connector)

- `record_email_triage`: 5-bucket `category_valid`; `attention_valid`
  incl. the `already_replied` gate; `apply_labels` composition for all
  (valid, invalid, none) combinations; hostile attention string →
  `attention_valid=False`, label dropped, category label unaffected.
- Trigger: `already_replied` annotation from a faked thread response
  (newer sent message present/absent; thread fetch failure → `False` +
  logged, never blocks delivery).
- Apply path: one tool call carrying both labels; attention-invalid run
  carries category label only; hostile-category fixture still skips
  entirely (existing pin, re-asserted under new vocabulary).
- `remove_labels`: CLI-only reachability (no tool exposes it — asserted
  the same way create_label's fence is), removal call shape.
- Allowlist: the 8-label set enforced at the tool boundary; no "none"
  label exists anywhere.
- Combinations (external review): urgent+awaiting-reply co-applied;
  review+awaiting-reply co-applied; urgent subsumes review (dedupe);
  valid attention with invalid category applies the attention label
  only, and the reverse; `reply_status=unknown` suppresses
  awaiting-reply only; a newer unrelated SENT message (References
  mismatch) does not mark replied where headers allow the distinction;
  sweep dry-run default, thread grouping, idempotent re-run; schema-2
  and legacy rows aggregate side by side.
