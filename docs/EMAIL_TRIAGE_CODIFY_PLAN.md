# Email Triage, Codified Sender Pre-Filter (G13 slice 1) — Design, v2

Status: **BUILT + CUT OVER 2026-07-30** (slice 1: eligibility engine +
CLI; runtime slices same day). Live shape: the v2 diamond on
`email-triage-apply` — deterministic `codified_sender_check` (rule
artifact + disable overlay read per run via world.fs; schema/TTL/
inactivity gates; HMAC-keyed 1-in-5 sampling; fail-open to judgment
everywhere) routes authenticated listed senders past the category
classifier while a small attention-only step still reads EVERY message;
`record_email_triage` composes per route with exactly-one-source
enforcement, decision_source/model_confidence-null semantics, and
IMMEDIATE overlay demotion on sampled mismatch or attention-detected.
The auth gate ships with a hardening beyond the plan: EmailMessage
carries Authentication-Results as an ORDERED list because the collapsed
header dict is last-wins — which would have surfaced attacker-APPENDED
AR forgeries; only the first mx.google.com entry is believed.
First artifact: **7 senders** (30d TTL, sample 1-in-5). §9 validation
window OPEN — **and codification is NOT yet "validated": the attention-
only classifier has no frozen precision results, which codify depends on
(TWO_AXIS finding 6)**. Deviations, honest (corrected per the 2026-07-31
review): runtime **rubric-hash NOT enforced** (schema version is; §3);
**`skip_if` deferred** — the decision_source query contract is the built
guard (§8); corrections era-scoping open (fence stays strict); the
overlay read-modify-write is now lock-serialized (§4/finding 17).
Engine-surface follow-ups — runtime rubric-hash, `skip_if`, per-sender
sampling floors, paired attention shadow eval, a resolved policy
fingerprint, tool-param pinning, apply postconditions — are tracked as
**G23**.

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
   — **a candidate threshold for THIS single-operator deployment, not an
   established universal promotion rule** (external review finding 14: 5
   unanimous over >1 day is weak evidence of durable future stability; the
   impact here is one reversible label in one mailbox; a general platform
   default should require evidence scaled by sender volume + observed
   category variability, not a fixed count) —
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

- **Rubric/version binding — DESIGN TARGET, partially implemented**
  (external review round 2, finding 12 — resolving the contradiction with
  the status line): **current runtime behavior checks
  `triage_schema_version` only**; `rubric_hash` is recorded in the
  artifact but **NOT enforced at runtime** (deterministic functions can't
  read the engine's memory hash yet). Stated plainly: a prompt/rubric
  change may leave semantically stale rules active until the operator
  regenerates — a real current limitation, not a minor note. Enforcing
  the hash at runtime is a G23 item.
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
  ≈ 3/n — but that bound is **per sender, not aggregate** (external
  review finding 15: 28 samples on sender A + 2 on B can reach "30 across
  the list" while B is essentially untested). Rate reduction or continued
  promotion for a sender requires that SENDER's own minimum sample count
  (or a hierarchical rule keeping low-volume senders at the higher rate);
  aggregate counts are operational reporting only, never sender-level
  confidence.
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

**`ObservationSpec.skip_if` is DEFERRED, not implemented** (external
review round 2, finding 13 — resolving the contradiction with the status
line): what is actually built is the **eligibility query contract** —
codified runs are excluded from positive evidence because promotion
counts only `decision_source == "classifier"` verdicts, so no
self-confirming write reaches the ledger. The `skip_if` engine surface
(load-time validation + runtime fail-closed skip + high-severity audit)
is the DESIGNED belt-and-suspenders, tracked G23; its fail-closed
semantics (a missed observation is recoverable; thousands of
self-confirming writes corrupt the ledger permanently) are the argument
for building it, not a description of current behavior.

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
  schema-version incompatibility (rubric-hash enforcement is DEFERRED —
  G23, so no rubric-hash test today); inactivity revalidation; sampled
  carries
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
- Engine (DEFERRED — G23, the `skip_if` surface is not built; the
  decision_source query contract is the live anti-feedback guard and is
  covered by the eligibility-CLI tests above): when `skip_if` lands,
  load-time validation rejects bad expressions and runtime eval failure
  skips + high-severity audit (fail-closed).

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
