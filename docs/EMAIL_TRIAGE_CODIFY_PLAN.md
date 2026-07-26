# Email Triage, Codified Sender Pre-Filter (G13 slice 1) — Design

Status: **proposed** (drafted 2026-07-26; design-reviewed same day:
adopt-with-conditions, all findings folded in below — including adopting
the reviewer's authentication gate as a requirement; not yet built). Executes
`docs/NEXT_STEPS.md` G13 for the email-triage workload — the first concrete
slice of the codification loop (`docs/LEARNING.md` execution learning; the
design-time/runtime "dial" from the Pega analysis,
`docs/product/COMPETITIVE_LANDSCAPE.md`). **Depends on the two-axis plan
shipping first** (`EMAIL_TRIAGE_TWO_AXIS_PLAN`): eligibility is defined
against the 5-bucket category axis and its era filter.

## 1. What this is

Senders whose outcome evidence is unanimous stop paying for LLM judgment:
a deterministic pre-filter classifies them directly (zero tokens), the
classifier handles everyone else, and **demotion is automatic in effect**
— evidence of drift pulls a sender back to runtime judgment. The criterion
(G13, sharpened 2026-07-25): codify only where the **sender determines the
category**; unanimity over many messages is the statistical test for that.
The attention axis is never codified (content/state-dependent by nature).

Honest cost note: the operator's Gmail filters already drain most
sender-determined mail before INBOX, so absolute savings are modest — but
the current qualifiers (Barron's 46, weather.com 2×30, …) are INBOX
residents accounting for a large minority of historical volume. The real
product here is the mechanism: judgment spent once, promoted to a rule,
reversible on evidence — measured, not asserted (§8).

## 2. Eligibility (the promotion rule)

A sender qualifies when, over **classifier-sourced, current-era** verdict
evidence (both qualifiers are load-bearing — §5, §6):

1. **≥ 5 confirmed** outcome events, **zero corrected** — the existing
   G13 threshold, already met by 8 senders (2026-07-24 check).
2. **Category-unanimous**: every counted verdict names the same 5-bucket
   category. (Era filter per the two-axis plan: only verdicts whose
   category ∈ current vocabulary count; old `urgent`/`awaiting-reply`
   edges are excluded, not disqualifying.)
3. **Attention-clean**: zero verdicts with a non-empty attention list
   in current-era evidence (attention is multi-valued per the two-axis
   plan's external review). A sender that *ever* demanded attention is not
   boring enough to codify. (Vacuously true at cutover — the new era
   starts empty; condition binds as two-axis evidence accrues. The
   residual risk that an old-era sender occasionally deserved `review`
   is accepted and covered by sampling, §4.)
4. **Entity-shape filter (design-review finding — the evidence store
   contains non-sender entities):** the key must parse as an email
   address, be a fixpoint of `normalize_entity`, and not be the mailbox
   owner. Keys like `user|person:…` or `org:…` from the store can never
   qualify; §2.4 is a query predicate, not an output description.
5. **Era filter: `triage_schema_version` where stamped, value-based for
   legacy rows** (the two-axis plan's external review introduced the
   schema-version stamp — the clean discriminator; pre-stamp history
   filters by value-compatibility, which keeps the 8 current
   qualifiers' stable-bucket evidence).

## 3. The codified list: materialized, auditable, operator-generated

**`tools/codify_senders.py`** queries the veracium store with the §2 rule
and writes **`backend/.memory/codified/<workflow_id>.json`**:

```json
{ "generated_at": "…", "era": "5-bucket", "thresholds": {"confirmed": 5},
  "senders": { "weeklybrief@news.weather.com":
      { "category": "notification", "evidence": {"confirmed": 30} } } }
```

- **Materialized file, not a live store query**: deterministic functions
  stay free of veracium coupling (and of per-message store reads); the
  list is inspectable, diffable, and its provenance travels with it.
- **Gitignored** (`.memory/` already is): sender addresses are personal
  mail metadata — never committed, same rule as the ground-truth corpus.
- **Regeneration is the demotion mechanism**: any `corrected` outcome on
  a listed sender (from review labels, a fork-with-changed-verdict, or a
  future G12 answer) removes it on the next run. The CLI prints
  promoted/retained/demoted with evidence counts. Cadence: operator-run
  — after review sessions, or when a mismatch audit (§4) fires.
  Auto-regeneration at boot is a named deferral.

## 4. Workflow shape (diamond; engine already supports it)

```
trigger → precheck (deterministic, codified_sender_check)
  precheck → classify     [condition: NOT steps.precheck.codified]
  precheck → record       [condition: steps.precheck.codified]
  classify → record
  record   → apply        [condition: category_valid — unchanged]
```

**Authentication gate (design-review HIGH, adopted as a requirement):**
the codified path is a roster of trusted-looking, high-spoof-value
senders, and bypassing the classifier means a forged `From: Barron's`
would get a credible `wf/newsletter` label with zero content scrutiny 4
runs in 5 — sampling is a drift detector, not a per-message defense.
Therefore the trigger annotates each message with
`auth_pass: bool` — parsed from Gmail's `Authentication-Results` header
(DKIM or DMARC pass aligned to the From domain; parse failure or absent
header ⇒ `False`) — and **`codified_sender_check` requires `auth_pass`**:
unauthenticated mail from a listed sender goes to the classifier like
everyone else (fail-open to judgment). Same trigger-annotation precedent
as the two-axis `already_replied`; the codified path thus never has
*less* scrutiny than the mail's own authenticity supports.

- `codified_sender_check` (new stock function): loads the list **via
  `world.fs`, path anchored to `WORKFLOW_PLATFORM_MEMORY_DIR`** (the
  CWD-relative bug class has recurred in this project — never a bare
  relative path), **read per-run** (rollback-without-restart is a §8
  requirement, and one small-file read per message is nothing),
  normalizes `trigger.from_address.address`, checks `auth_pass`, and
  emits
  `codified: bool` plus — when codified — `verdict_text`: a synthetic
  classifier-shaped JSON (`category`, `attention: []`,
  `category_confidence: 1.0`, `summary: "codified: 30/30 unanimous"`,
  `triage_schema_version: 2`). Missing/unreadable list
  → `codified: false` for everyone (fail-open to judgment, never to a
  wrong rule).
- **Sampling (drift detection beyond corrections):** even a listed
  sender goes to the classifier when `sha256(message_id) % N == 0`
  (config `sample_one_in`, default 5 initially ≈ 20%, loosen later).
  Hash-based, not RNG — deterministic per message, replay-safe in tests.
  A sampled run whose classifier verdict **disagrees** with the codified
  category records a `codified_mismatch` audit entry — the operator
  signal to regenerate (demote). Skip semantics verified: `record` runs
  when either incoming edge is active; both inactive never occurs
  (precheck always activates exactly one).
- `record_email_triage` gains a fallback source (config
  `codified_from: steps.precheck.verdict_text`, used when `triage_from`'s
  step was skipped) and a passthrough output `source:
  "classifier" | "codified"`. Everything downstream — `apply_labels`
  composition, the minimized apply step, the enum gates — is unchanged
  and shared by both paths.

## 5. The anti-feedback rule (load-bearing)

Codified runs must not manufacture their own evidence. Two guards, both
test-pinned:

1. **No verdict observation on codified runs — which requires NEW ENGINE
   SURFACE, named here** (design-review finding: `ObservationSpec` has no
   conditional field and `_observe_learned_memory` renders every declared
   observation whose template resolves — the verdict template reads
   `steps.record.*`, populated on codified runs too). `ObservationSpec`
   gains **`skip_if: <expression>`**, evaluated with the same
   simpleeval sandbox as edge conditions against the run context; the
   verdict observation declares `skip_if: steps.record.source ==
   "codified"`. Additive, back-compatible (absent = never skip), and
   generally useful beyond this consumer. The mail-received
   (third_party) observation still accrues; sampled runs are real
   classifier verdicts and observe normally (they are exactly how
   unanimity keeps accruing — or breaks).
2. **The eligibility query counts classifier-sourced verdicts only**
   (belt-and-suspenders with guard 1, and robust to any future consumer
   that writes verdict-shaped events).

Recall injection and act-time `unreviewed` uses are absent on the
codified path **structurally, not by new logic** (verified in review:
recall runs only inside non-minimized agentic steps — classify is
SKIPPED and apply is `inputs:`-minimized, so neither records uses) —
outcome counters stay a measure of *judgment*, not of rule
application. Rule applications are visible instead in step outputs and
audit (`source: codified`, run counts queryable per sender).

## 6. Interaction with G13 evidence going forward

The two-axis plan's era filter is implemented **in the eligibility query
of `codify_senders.py`** (category value ∈ current vocabulary), not in
veracium — the store stays an honest ledger; interpretation lives in the
consumer. Combined with §5, the evidence stream stays clean: only real,
current-era classifier judgments ever promote or retain a sender.

## 7. Cost & behavior expectations

- Codified path: **zero LLM tokens, zero recall** — trigger + three
  deterministic steps + one two-turn apply call (~$0.0025 → the apply
  call is now the entire cost; a later slice could route codified
  verdicts through a deterministic label function if the §2b
  function-capability story ever lands, taking it to zero).
- Classifier path: unchanged.
- Latency: codified runs complete in ~1s vs ~8s.

## 8. Validation

1. **Shadow-accuracy via sampling**: run with `sample_one_in: 5` for the
   first ~2 weeks; success = zero `codified_mismatch` audits, or every
   mismatch explained and answered by regeneration.
2. **Zero human corrections** on codified labels in the window (the
   operator's Gmail spot-checks — same standard as the acting window).
3. **Measured savings**: fraction of runs taking the codified path +
   tokens avoided, from step outputs (`source` counts) — replaces the §1
   hand-wave with numbers.
4. Rollback: delete/empty the codified list file (fail-open sends
   everyone back to the classifier); no restart needed — the list is
   read per-run (pinned; the per-boot hedge is dropped).
5. **Spoofing check**: at least one crafted unauthenticated message
   from a listed sender in the test window confirms the auth gate
   routes it to the classifier (unit-tested regardless; §9).

## 9. Test plan (unit)

- `codified_sender_check`: hit / miss / sampled-hit (hash boundary
  cases) / missing file / malformed file / **`auth_pass=False` on a
  listed sender** → all fail-open to the classifier; normalization
  applied to the trigger address.
- Trigger `auth_pass` parsing: DKIM/DMARC pass, fail, absent header,
  malformed header, misaligned domain → only aligned pass yields True.
- `skip_if` on ObservationSpec: engine unit tests (skip on true, observe
  on false/absent, malformed expression → observe + audit, never crash).
- DAG: codified run skips `classify` (SKIPPED, zero Bedrock calls);
  non-codified run skips nothing; `record` completes on either path.
- `record_email_triage`: fallback source; `source` passthrough;
  `apply_labels` identical for equivalent verdicts from either path.
- Anti-feedback: codified run emits no verdict observation and no
  act-time uses; sampled run emits both; `codified_mismatch` audit on
  disagreement.
- `codify_senders.py` against a seeded fake store: promotion at
  threshold, exclusion below it, demotion on corrected, era filter
  excludes old-vocabulary verdicts, attention≠none disqualifies,
  non-email entity keys (`user|person:…`, `org:…`) and the mailbox owner
  never qualify.

## 10. Deferred, with triggers

| Deferred | Trigger |
|---|---|
| Auto-regeneration (boot-time or scheduled) | The operator forgetting to regenerate after a review session actually causing a stale rule. |
| Deterministic label application for codified runs (drop the apply LLM call) | The §2b function-capability story landing for any reason. |
| Gmail filter-export import as seed candidates | Operator interest; assisted flow (XML parse → candidate list → operator confirms categories) — never auto-promoted. |
| Generalizing codification beyond email triage | A second workflow with per-entity unanimity evidence. |
| Sampling-rate auto-tuning | Enough mismatch/agreement data to make a rate decision non-arbitrary. |
