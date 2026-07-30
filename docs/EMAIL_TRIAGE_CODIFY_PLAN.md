# Email Triage, Codified Sender Pre-Filter (G13 slice 1) — Design, v2

Status: **slice 1 BUILT 2026-07-30** — the eligibility engine +
generation CLI (`workflow_platform.codify` pure rule + `tools/
codify_senders.py`: Postgres verdict evidence x veracium disqualifiers,
dry-run default, --apply atomic 0600 writes, --explain traces; 11 rule
tests). **Two delegated decisions executed:** domain-level
disqualification adopted (registrable-domain fence with a freemail
exemption — the TTG sibling case) and the part-2 window closed for
category+mechanics (attention stays monitored; see TWO_AXIS §7 note).
First live dry-run: 111 senders with evidence, **6 eligible**
(indeed/wayfair/overstock/businessinsider/simplyrecipes/nextdoor), and
one finding for the next session: historical 7-bucket-era corrections
put 17 domains behind the fence (incl. nytimes/wsj — era-collision
corrections like breakingnews urgent-vs-newsletter, not sender-
unpredictability evidence). **Open question: era-scope corrections for
disqualification** the way verdict evidence already is — conservative
as-is (over-disqualifies, never under), so deliberately left strict for
the first artifact. Runtime slices (trigger auth_pass, precheck
function + overlay, diamond YAML + attention-only classifier) not yet
built.

## 1. What this is (claims stated honestly)

Senders with unanimous, current-schema, per-message evidence stop paying
for **category** judgment and recall: a deterministic rule supplies the
category; a small **attention-only classifier** still reads every message
(the attention axis is content-dependent and is *never* bypassed). The
rule is scoped, versioned, auditable, immediately disable-able at runtime,
and can never feed its own applications back as promotion evidence.

**Not** "zero-LLM classification" (v1 overclaimed): the codified path
eliminates category-classification and recall tokens and shrinks the
prompt; one small attention call and one minimized apply call remain.
Savings are measured in validation, not asserted.

## 2. Eligibility (the promotion rule, v2)

Evidence unit — **one distinct adjudicated message**, keyed
`(org, account, workflow deployment, message_id, triage_schema_version)`:
replays/retries/forks collapse to one; a human correction *supersedes* the
classifier verdict for its message. The artifact reports
`distinct_messages` and `verdict_events_seen`; only the former drives
promotion.

A sender qualifies when ALL hold:

1. **Current-schema floor**: ≥ 5 distinct schema-2 messages, unanimous
   category, **all with empty attention**, zero corrections. Legacy
   (pre-schema-2) evidence may *support category stability* on top of
   this floor but can never satisfy the attention condition (v1's
   vacuously-true-at-cutover rule was unsafe and is withdrawn).
2. **Diversity + recency**: the qualifying messages span > 1 day (five
   identical messages from one delivery batch prove little), and ≥ 1
   classifier judgment is recent (≤ the rule TTL, §3).
3. **Category allowlist for the first release**: only `newsletter`,
   `promotion`, `notification`. `personal` and `spam` are excluded until
   a compelling case exists.
4. **Entity shape**: key parses as an email address, is a
   `normalize_entity` fixpoint, and is not the mailbox owner
   (store keys like `user|person:…` / `org:…` can never qualify).
5. **Disqualifiers, from any source**: any human correction, any
   confirmed `codified_mismatch`, any sampled/reviewed non-empty
   attention observation for the sender.

## 3. The rule artifact (a policy file, treated like one)

`tools/codify_senders.py` (**`--dry-run` default, `--apply`,
`--explain <sender>`**) queries veracium — always scoped to the same
org/account namespace the workflow writes (`org:<org>:user:<account>`;
identity is never joined by inference) — and writes:

```
backend/.memory/codified/<org_id>/<account_id>/<workflow_id>.json
```

(v1's `<workflow_id>.json` alone was a tenant-isolation bug: a bundled
definition can serve multiple mailboxes.) Written atomically (temp file +
rename), permissions 0600, path components sanitized, schema-validated
before replacement, gitignored. Per-sender entries carry the full audit
surface:

```json
{ "format_version": 1, "triage_schema_version": 2,
  "rubric_hash": "sha256:…", "codifier_version": 1,
  "generated_at": "…",
  "senders": { "weeklybrief@news.weather.com": {
      "category": "notification", "status": "active",
      "distinct_messages": 30, "verdict_events_seen": 34,
      "current_schema_messages": 12, "attention_bearing_messages": 0,
      "corrections": 0, "first_evidence_at": "…",
      "last_evidence_at": "…", "expires_at": "…" } } }
```

- **Rubric/version binding (external finding 13):** the precheck verifies
  `triage_schema_version` + `rubric_hash` against the deployed workflow;
  any mismatch routes to full classification until the list is
  regenerated — a prompt change must not leave semantically stale rules
  active.
- **TTL + inactivity revalidation (finding 9):** rules expire
  (`expires_at`); the first message after a long sender-inactivity gap
  goes to full classification regardless of the rule.

## 4. Runtime disable overlay (demotion is now actually immediate)

v1's "demotion automatic in effect" was an alert, not demotion — a
corrected sender stayed codified until an operator reran the CLI, an
unbounded gap. v2 adds a runtime-writable overlay beside the rule file:

```json
{ "disabled_senders": { "sender@…": {
    "reason": "sampled_category_mismatch | correction | attention_detected",
    "disabled_at": "…", "run_id": "…" } } }
```

`codified_sender_check` reads rule file + overlay; a disabled sender
routes to full classification. The overlay is written **at runtime by the
record step** (a deterministic function writing a local file via
`world.fs` — no new capability surface) the moment it sees: a sampled
category mismatch; a non-empty attention verdict on a codified run; or a
correction event. Regeneration reconciles: confirms the demotion or
restores the rule, and clears resolved entries. Every disable also emits
a `codified_sender_disabled` audit entry.

## 5. Workflow shape, v2 (attention is never bypassed)

```
trigger → precheck (deterministic, codified_sender_check)
  precheck → classify_full      [condition: route == "full"]
  precheck → classify_attention [condition: route == "codified"]
  classify_full      → record
  classify_attention → record
  record → apply                 [condition: apply_labels != []]
```

- **Precheck output is a structured routing decision** (finding 10), not
  a bare bool: `{listed, authenticated, sampled, route:
  "full"|"codified", rule_category, rule_evidence:{…}, rule_compatible}`.
  `record` validates that **exactly one** classifier step produced
  output; both-or-neither is a hard error, never a silent preference.
- **`classify_attention`** (codified route): a small agentic step —
  attention-only prompt (the definitions + negative exemplars from the
  two-axis plan), `tools: []`, **no recall injection** (cost; per-sender
  attention history remains G12's substrate later), smaller token cap,
  same model initially (a cheaper model is a §11 option). Output:
  `{"attention": […], "decision_note": "…"}`.
- **Routing**: `route = "full"` when not listed, not authenticated (§6),
  rule incompatible/expired/disabled, or **sampled**. Sampled runs carry
  `rule_category` so `record` can compare the full classifier's category
  against the rule and write the `codified_mismatch` audit + overlay
  entry on disagreement.
- **`record_email_triage`** composes the final verdict from either
  source: codified route = rule category + classifier attention; full
  route = classifier both. Output carries **`decision_source:
  "classifier" | "codified_sender_rule"`**, `model_confidence` (null on
  the codified route — finding 11: a rule is not a confident model;
  v1's `category_confidence: 1.0` is withdrawn), `rule_evidence`
  metadata, and `decision_note` (v1's fake `summary` is withdrawn).
  `apply_labels` composition downstream is unchanged.

## 6. Authentication gate (v2: a trusted-header policy, not a parse)

The codified path still requires authentication, now specified (external
finding 8):

- Use **only** `Authentication-Results` headers whose `authserv-id` is
  Google's receiving infrastructure (`mx.google.com`); attacker-supplied
  AR headers deeper in the header block are ignored (topmost trusted
  instance wins).
- `dmarc=pass` suffices. Absent DMARC, an aligned `dkim=pass` (strict
  alignment; no relaxed-subdomain acceptance in the first release) is
  accepted. IDN domains are compared punycode-normalized.
- Forwarded/mailing-list mail typically fails alignment → routes to full
  classification. Correct: fail toward judgment.
- Stated limit: authentication proves domain/signing control, not that
  this message matches the sender's historical category — that is what
  universal attention judgment + sampling + the overlay are for.

## 7. Sampling, v2 (keyed, counted, honest)

- **Keyed hash**: `HMAC_SHA256(platform_secret, account ||
  gmail_message_id) % sample_one_in` — the id is Gmail's internal opaque
  id (not the sender-supplied RFC Message-ID), and the HMAC removes any
  residual attacker influence over sampling selection. Deterministic per
  message, replay-safe; the secret comes from the SecretStore.
- **Count-based validation** (not calendar-based): with zero mismatches
  in *n* samples, the ~95% upper bound on the true mismatch rate is
  ≈ 3/n — so rate reduction (5 → 10) requires a minimum sampled count
  (≥ 30 across the list, reported per sender), never "two quiet weeks".
  The validation report includes: codified-run count, sampled count (per
  sender), mismatches, attention-bearing sampled messages, corrections.

## 8. Anti-feedback (v2: query contract primary, skip_if fail-closed)

The eligibility query contract (external finding 6) is the primary
mechanism:

- **Positive evidence**: classifier-sourced (`decision_source ==
  "classifier"`), current-schema, distinct-message verdicts only.
- **Never positive**: rule applications — the codified route's category
  is rule-sourced by construction. The codified route's *attention*
  verdicts are real classifier judgments and DO count — toward the
  attention-cleanliness condition and the disable overlay (a strict
  improvement over v1: attention violations now surface on every
  message, not 1-in-5).
- **Disqualifying**: corrections and confirmed mismatches from any
  source; a correction to a codified result produces a
  rule-application-correction event even though no classifier category
  verdict exists for that run.

`ObservationSpec.skip_if` remains as named engine surface, now with
**fail-closed semantics for learned-memory writes** (external finding 12,
replacing v1's observe-on-error): expressions are validated at
workflow-load time (an invalid definition is rejected before execution);
runtime evaluation failure **skips the observation** and emits a
high-severity audit event. The asymmetry is the argument: one missed
observation is recoverable; thousands of self-confirming writes corrupt
the evidence ledger permanently. Documented explicitly: this fail
direction is specific to memory writes and may differ for other future
`skip_if` consumers.

## 9. Validation

1. Shadow accuracy: `sample_one_in: 5` until ≥ 30 sampled messages with
   zero unexplained mismatches (per-sender counts reported, the 3/n
   bound stated in the report).
2. Zero human corrections on codified-route labels; every overlay
   disable investigated.
3. Attention parity on the codified route: the attention-only
   classifier's decisions spot-checked to the same standard as the
   two-axis acceptance set.
4. Measured savings: tokens and latency per route from step outputs
   (`decision_source` counts) — replacing §1's hand-wave with numbers.
5. Rollback: empty/delete the rule file **or** disable per sender via
   the overlay; both are read per-run — no restart. Crafted
   unauthenticated and spoofed-sender fixtures confirm routing in tests
   and once live.

## 10. Test plan (v2)

- Precheck: structured route output; disabled-overlay hit; expired rule;
  rubric-hash mismatch; inactivity revalidation; sampled carries
  `rule_category`; exactly-one-classifier-output enforcement
  (both/neither = hard error); HMAC sampling boundaries; auth-policy
  cases (trusted vs attacker AR header, dmarc pass, aligned/unaligned
  dkim, absent, malformed, IDN); missing/malformed rule file → full
  classification for everyone.
- Record: verdict composition per route; `decision_source` /
  `model_confidence: null` / `rule_evidence` passthrough; overlay write
  on mismatch / attention-detected / correction; audit entries.
- Eligibility CLI (seeded fake store): distinct-message collapsing
  (replay/retry/fork dedupe); correction supersedes; current-schema
  floor with legacy top-up; diversity (single-batch fails) + recency;
  category allowlist; disqualifiers; entity shape; tenant scoping
  (evidence from another account's namespace never leaks in);
  `--dry-run` / `--apply` / `--explain`; atomic write + schema
  validation + 0600.
- Engine: `skip_if` load-time validation rejects bad expressions;
  runtime eval failure skips + high-severity audit (fail-closed pinned).

## 11. Deferred, with triggers (v2 additions marked)

| Deferred | Trigger |
|---|---|
| Auto-regeneration (boot/scheduled) | A stale rule surviving past its overlay disable in practice. |
| Cheaper model for `classify_attention` *(v2)* | Attention parity holding in validation; then measure. |
| Deterministic label application for codified runs | The §2b function-capability story landing. |
| Gmail filter-export import as seed candidates | Operator interest; assisted, operator-confirmed, never auto-promoted. |
| `personal`/`spam` codification *(v2)* | A compelling case; both stay classifier-only until then. |
| Relaxed DKIM alignment / forwarded-mail handling *(v2)* | A legitimate codified sender consistently failing strict alignment. |
| Sampling-rate auto-tuning | ≥ the §7 minimum sampled counts, so the decision is non-arbitrary. |
| Generalizing beyond email triage | A second workflow with per-entity unanimity evidence. |
