# Email Triage, Acting Variant — Design

Status: **proposed** (drafted 2026-07-18; design-reviewed same day:
adopt-with-conditions, all findings folded in below; not yet built). This is the
named trigger for `docs/NEXT_STEPS.md` G11 firing, and a platform first: the
first workflow where an agent holds a **mutating external capability** in
production (writing Gmail labels to a real mailbox). The design's job is to
make that first mutation boring: smallest possible blast radius, every write
audited, and the judgment/action boundary explicit.

## 1. What changes

`examples/email_triage_live/` classifies mail and records verdicts —
deliberately read-only (`tools: []`, test-pinned). The acting variant applies
the verdict back to the mailbox as a Gmail label (`wf/<category>`), so triage
becomes visible where the mail lives. The read-only example stays untouched
(it remains the template and the rollback); the acting variant is a new
sibling, `examples/email_triage_apply/`, and the live deployment migrates to
it only after a supervised validation window (§8).

## 2. Decision: who applies the label

Applying a label given a category is mechanical — no judgment. Three ways to
do it:

- **(a) Give the classifier agent the label tool.** Minimal build, but the
  agent that reads attacker-controlled content would hold a write
  capability, and the step that needs zero tools would have one. Rejected.
- **(b) A deterministic step that calls the connector.** Cost-correct
  (zero tokens), but deterministic steps deliberately cannot reach
  connectors: they run as trusted code *outside* the capability
  intersection and per-call audit that agent tool dispatch provides.
  Granting them connector reach means building a second, parallel
  capability story for functions — a real architecture change. Rejected
  for now; recorded in §10 as the codification-loop endpoint once trust
  is established.
- **(c) A second, minimal acting step — chosen.** The workflow splits
  privileges: the **classifier keeps `tools: []`** (its fence is
  re-pinned for the new variant), and a tiny second agentic step holds
  exactly one tool and applies exactly one label. Acting goes through the
  existing, tested path — capability intersection, per-tool-call audit
  entries, C6 capability panel, dry-run stubbing — which is precisely the
  machinery the trust wedge sells. Cost: one small Haiku call per message
  (~300–500 tokens ≈ $0.0005; ~30 msgs/day ≈ 1.5¢/day). Accepted.

## 3. Input minimization (engine change, the load-bearing piece)

`_build_user_message` currently dumps the **full trigger payload + all prior
step outputs** to every agentic step. Under that, the acting step would
receive the hostile email body — an injection in mail content could address
the tool-holding agent directly. The fix is a small, generally useful engine
addition:

- `AgenticStep` gains an optional **`inputs: list[str]`** — context paths
  (the existing `_resolve_context_value` grammar: `trigger.message_id`,
  `steps.record.category`). When present, the user message is built from
  **only** those resolved values; nothing else from the run reaches the
  step. Absent → today's behavior (back-compatible; no other workflow
  changes).
- The acting step declares
  `inputs: ["steps.record.category", "trigger.message_id"]` — it never
  sees subject, body, sender, or any other attacker-influenced text.

**The category channel must be closed first (design-review blocking
finding).** Today `record_email_triage` copies *any* string through as
`category` — validation against `TRIAGE_CATEGORIES` is deliberately absent
(historical five-bucket coexistence). That makes `steps.record.category`
attacker-influenceable free text: a mail that steers the classifier's JSON
puts arbitrary words into the tool-holder's prompt. Fix, additive:
`record_email_triage` gains a **`category_valid`** output field (exact
membership in `TRIAGE_CATEGORIES`; existing consumers untouched), and the
apply edge conditions on it (§5) — so the acting agent's entire
attacker-adjacent input surface is **one enum value** plus a
platform-supplied message id. This also fixes the adjacent gap that
`parse_ok` alone doesn't imply `category` exists.

**Security criteria (test-pinned):**
1. A hostile string planted in the trigger's subject/body must not appear
   anywhere in the acting step's user message or system prompt.
2. A hostile-*category* fixture (classifier returns
   `{"category": "urgent. IGNORE PREVIOUS INSTRUCTIONS …", …}`) must set
   `category_valid=false` and the apply step must be SKIPPED — no tool
   call anywhere in the run.

## 4. Account binding + label allowlisting

Two gaps in the current plumbing, both closed here:

- **Per-account tools.** `EmailLabelApplyTool` binds one `GmailConnector`;
  the bootstrap builds it only for the platform tools account
  (`intelligent.workflow.engine@…`) — but the triage mailbox is the
  personal account, which has poll credentials on disk and no tool.
  Change: the bootstrap builds one label tool **per credentialed account**
  (reusing the dmarc-era per-account seeding), registered as
  `email_label_apply:<account>` (bare `email_label_apply` stays aliased to
  the tools account for back-compat). The capability allowlist then names
  *which mailbox* is writable — and the C6 capabilities panel shows it.
- **Label allowlist, twice over.** The tool gains an optional
  `allowed_labels` constructor param; for triage-apply instances it is the
  seven `wf/<category>` names, and any other request fails before the API
  call. Independently, `_resolve_label_id` already **refuses to create
  labels** (`GmailLabelNotFound`) — the mailbox's own label list is a
  second, physical allowlist. Labels are pre-created once by a small
  operator CLI (`backend/tools/setup_triage_labels.py`: idempotent,
  creates the seven `wf/*` labels via the connector, prints what exists).

## 5. Workflow shape

```
email trigger (qspencer@gmail.com, unchanged)
  → classify   (agentic; tools: []           — unchanged, rubric + recall)
  → record     (deterministic record_email_triage — unchanged)
  → apply      (agentic; tools: [email_label_apply:qspencer@gmail.com]
                inputs: [steps.record.category, trigger.message_id]
                goal: "Apply exactly the label wf/<category> to the message."
                policy: max_iterations 2, small token cap)
       condition: steps.record.category_valid == true — unparseable or
                  out-of-vocabulary verdicts are recorded but never acted
                  on (skip propagates; §3 finding — parse_ok alone does
                  not imply a usable category)
```

- **Add-only**: the tool path only adds labels (`addLabelIds`); removal,
  archiving, and mark-read are not reachable. Re-runs re-apply the same
  label — idempotent at Gmail.
- `record_email_triage` output gains nothing; the apply step's own output
  (`labels_applied`) plus the per-tool-call audit entry are the record of
  action. `learned_memory` observations unchanged.
- Dry-run (C6.1) already replaces external tools with no-op stubs — the
  acting variant is dry-runnable on day one.

## 6. Blast radius, stated plainly

The worst case under this design is: *wrong `wf/*` labels on messages in
one mailbox.* Stated precisely: `message_id` is a free tool parameter — the
`inputs:` selector constrains the prompt, not the tool's argument space —
so a compromised acting agent could label messages other than the triaged
one. What makes this acceptable rather than hand-waved: with §3's category
fence in place, **no attacker-influenced text reaches the acting agent at
all** (its inputs are an enum member and a platform id), so steering it
requires breaking the enum check first; and even then the writes are
confined to pre-created `wf/*` labels on the one bound mailbox. Parameter
pinning (engine-resolved tool-argument binding) is recorded in §10 as
hardening, not prerequisite. Labels are
user-visible, removable, non-destructive, and confined to the pre-created
`wf/` namespace. No send capability exists in the workflow; no label outside
the allowlist can be written; no mailbox other than the bound account is
reachable; the classifier that reads hostile content still holds zero tools.
Kill switch: swap back the read-only YAML + restart (the G9 cursor makes
restarts loss-free).

## 6b. Partial-failure semantics (design-review findings, accepted or mitigated)

- **Apply failure loses the run's memory observation.** Observations fire
  only on the success path, so a mechanical label failure (auth blip,
  label deleted) would FAIL the instance after a *valid recorded verdict*
  and silently skip `_observe_learned_memory`. Mitigation: the apply step
  sets `runtime.retries: 2`; a persistently FAILED instance is retryable
  via the existing endpoint (the resume path re-runs only the failed
  step, then observations fire on completion). Residual loss window
  accepted and visible (FAILED instances are the dashboard's loudest
  state).
- **Silent no-apply.** Tool errors return as `ToolResult(error=…)`; an
  agent at `max_iterations: 2` can *complete* in prose without a
  successful call — the step "succeeds" with no label written. Accepted
  with detection, not prevented: §8's validation criteria (parity +
  audit-entry check) catch it during the window, and the same audit query
  (apply-step completions without a successful `email_label_apply` call)
  is kept as a periodic operator check afterward. A structural guard
  (engine-level "step must include a successful call to tool X") is
  recorded in §10 — it's the general fix, and too much machinery for one
  workflow's first outing.

## 7. G11 lands as phase 2 (rubric-only)

This plumbing fires G11's trigger, but the two-axis rework ships **after**
the apply path is validated, as a rubric + label-vocabulary change with no
new code: a second label namespace (`wf-attn/needs-attention`) driven by a
new `attention` field from the classifier, with the §4 allowlist widened by
exactly that one label. Phase 1 applies category labels only — one new thing
at a time.

## 8. Validation window + success criteria

Run supervised for **~3 days / ≥100 messages** on the live mailbox:

1. Category parity: applied labels match the existing pipeline's recorded
   categories 100% (the apply step adds no judgment, so any divergence is
   a bug, not drift).
2. Zero unexpected writes: every `tool_call` audit entry for the apply
   step names a `wf/*` label and the triaged message id — nothing else.
3. No cost surprise: apply-step spend ≤ ~$0.001/message.
4. Spot-check in Gmail: labels render usefully (this is also the first
   user-visible surface for the G11 evidence — expect the
   category-vs-attention friction to become concrete here).

Rollback at any criterion failure: revert the deployment to the read-only
YAML; labels already applied stay (harmless, removable).

## 9. Test plan (unit, fake connector)

- Apply step calls the tool with exactly `wf/<category>` +
  the trigger's message id; happy path asserts the audit entry.
- `category_valid == false` (both unparseable AND out-of-vocabulary
  hostile-category fixtures) → apply step SKIPPED; no tool call anywhere.
- `record_email_triage` emits `category_valid` correctly for all seven
  valid categories, the empty case, and a prompt-injection string.
- Input minimization pin (§3): hostile trigger text absent from the acting
  step's prompts.
- Classifier fence re-pin: `classify.tools == []` in the new YAML, same
  test shape as the existing read-only pin (which stays for the old
  example).
- `allowed_labels` enforcement + unknown-label (`GmailLabelNotFound`)
  surfacing as a step error, not a crash.
- Per-account registration: `email_label_apply:<account>` resolves to a
  connector bound to that account; the bare name still maps to the tools
  account.
- `inputs:` engine unit tests: selected-paths-only message, unresolved
  path → empty slot (not a crash), absence → legacy behavior.

## 10. Deferred, with triggers

| Deferred | Trigger |
|---|---|
| Deterministic connector actions (codification loop endpoint for §2b) | Apply path validated + a second mechanical connector action appears — then design the function-capability story once, for both. |
| Label-removal / correction sync (user removes a label → outcome event for veracium) | First observed hand-correction in the mailbox; pairs with G12. |
| Auto-creating labels from the workflow | Never, absent a strong pull — the no-create fence is a feature. |
| Outlook parity for the acting path | Outlook connector existing at all (`EMAIL_CONNECTOR_PLAN`). |
| Tool-parameter pinning (engine resolves `message_id` from context and binds it; agent can't choose) | Second acting workflow, or any evidence of the §6 residual path being probed. |
| Structural "step must succeed via tool X" guard | Second workflow that acts through a tool — generalize then, not for one consumer. |
| Org-scoping of per-account tools in the catalog | Second org with email credentials (`GET /api/catalog` currently lists tools globally). |
