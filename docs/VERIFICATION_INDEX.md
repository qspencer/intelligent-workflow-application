# Load-Bearing Verification Index — prose-to-code map for external review

**Selective by design (external review finding 13):** this maps the
**load-bearing** correctness/security claims of the two contract docs, not
every control. Controls NOT mapped here (Bedrock data boundary, raw-trace
default, webhook production-registration, secret-file perms, resource-
exhaustion limits, audit completeness, same-org authz, admin/host trust,
supply-chain) are covered as narrative in `THREAT_MODEL.md` §§7a-9 with
their own dispositions — this index does not re-verify them.

Status: written 2026-08-01. Purpose: let a reviewer verify that the two
contract docs (`docs/EXECUTION_SEMANTICS.md`, `docs/THREAT_MODEL.md`)
match the code, without scavenger-hunting ~198 files. Every `file:line`
and every test name below was confirmed by reading the file at the pinned
commit; unverified claims are flagged, not papered over.

**Pinned review commit: see the handoff manifest** (this doc's own commit
— archive THAT sha, never `HEAD`, so late commits can't desync the index
from the code; external review finding 1):

    git archive --format=tar.gz --output=review-<sha>.tar.gz <sha>
    sha256sum review-<sha>.tar.gz   # record in the handoff note

The sha256 is published in the handoff note, NOT here (a hash cannot live
inside the file it hashes). The archive excludes `.secrets/`, `.env`, and
the untracked local stores; verified 0 secret-pattern entries via
`git archive <sha> | tar -t | grep -iE 'secret|refresh_token|\.env'`.

Status legend (expanded per external review finding 2 — evidence
existence AND quality):
- **VERIFIED-CODE+TEST** — code read AND a test read that asserts the claim.
- **VERIFIED-CODE** — code read; no test pins the specific claim.
- **VERIFIED-ABSENCE** — an intentional NON-feature, confirmed absent in
  code (e.g. compensation, machine-recorded effect outcomes). Not a defect.
- **PARTIAL** — the claim has multiple parts; some pinned, some not.
  Named per row.
- **CONTRADICTED** — the contract asserts X; the code does not do X. A
  defect to fix (none open as of this revision — the CANCELLED case the
  reviewer found is now implemented + pinned).
- **FUTURE/UNBUILT** — designed, tracked (G-item), no code yet.

All paths are relative to repo root `/home/ubuntu/Dev/intelligent-workflow-application`.
Backend source: `backend/src/workflow_platform/`. Tests: `backend/tests/`.

---

## 1. How to run the suite

Default suite (replay mode — no AWS, no Postgres, no Gmail; `BEDROCK_MODE`
defaults to `replay` in `tests/conftest.py:10`):

```
cd backend && uv sync && uv run pytest
```

**Frozen execution record — the default (replay) suite** (external review
finding 4; collection ≠ passing):

| field | value |
|---|---|
| commit | this doc's commit (see manifest) |
| command | `cd backend && uv run pytest -q` |
| passed | **908** |
| skipped | 14 (live/integration, self-skip without creds) |
| failed | 0 |
| python | 3.12.3 |
| uv | 0.12.0 |
| os | Ubuntu 24.04 |
| duration | ~47s |

The gated suites (Postgres / schema / live Bedrock+Gmail+browser) run
separately — their pass records live in the CI run for this commit
(`.github/workflows/ci.yml` + `live-tests.yml`), not reproduced here.

**Code-review round-2 controls (external review 2026-08-01, all
VERIFIED-CODE+TEST):**

| Claim | Implementation | Test |
|---|---|---|
| F1 ANY branch exception cancels siblings AND the origin step persists FAILED | `executor.py` loop body guarded; `_run_step_once` catches unexpected `Exception` → step FAILED + `unexpected:true` audit → re-raise (not CancelledError) | `test_parallel_execution.py::test_unexpected_exception_cancels_mutating_sibling` (asserts step a FAILED w/ error, sibling b CANCELLED, no mutation) |
| F2 tool pins FAIL CLOSED on unresolved path | `executor.py` pin resolution → `StepFailure` + audit `tool_pin_unresolved` if a path resolves None | `test_tool_param_pinning.py::test_unresolved_pin_fails_step_closed` |
| F3 tool secrets (incl. model-ECHOED output_text), RAW trigger payloads, recalled correspondent history, and ERROR text projected across every read surface | `trace_projection.py::redact_tool_data` (re-exported by `api/redaction.py`) — **default-deny via a registered field VALIDATOR registry** (§1.4 CONTRACT 1): a value survives only if its field is registered AND the value passes its rule; `output_text` and all free-form fields are withheld **whether or not the step used a tool** (raw by taint, §1.1); `trigger_payload`/`trigger` project to routing ids only (sender address stripped); `recall` withheld; `redact_error` covers error text. Applied to both audit endpoints, instance detail (dump+steps+context), explain, escalations, dry-run, run-batch, WS delivery; the LIST endpoint's `_instance_summary` omits context + trigger_payload entirely | `test_audit_redaction.py::test_below_grant_cannot_recover_tool_secrets_anywhere` (model echoes both secrets into output_text; a raw trigger body is set; Viewer+User recover no tool/output/trigger secret from all 5 HTTP surfaces incl. the list; routing `message_id` survives; grant-holder raw) **+** `test_audit_redaction.py::test_redact_projects_trigger_payload_and_recall` (trigger + recall unit) **+** `test_org_isolation.py::test_ws_redacts_tool_secrets_without_grant` (WS) **+** `test_trace_review_fixes.py` (one adversarial test per reproduced bypass) **+** `test_trace_surface_inventory.py` (explain / escalations, P2) | **VERIFIED-CODE+TEST** for the read surfaces. Authorization is now a distinct, audited **raw-trace grant** (TG1), not a role, and every release emits the attempt/decided audit pair (TG2); storage separation + per-org encryption are built (TG3a-d). ⚠️ **Contract A / B1 are NOT claimed as delivered** — two external code reviews found them undelivered; the four remediation primitives (P1/P2/P3a/P4) are built and a third review is pending. See `docs/TRACE_GOVERNANCE_PLAN.md` + `NEXT_STEPS.md` G-Trace-Review-2 |
| F5 WS org resolution fails closed | `api/ws.py::_subscriber_org` raises `_OrgUnresolved` on missing row → upgrade rejected | `test_org_isolation.py::test_ws_rejects_non_admin_with_unresolvable_org` |

Reproducibility (external review note): the suite needs the locked deps
(veracium==0.4.3 et al) — a clean env can't run it offline yet. Next
handoff will ship a hash-pinned wheelhouse or a container/OCI digest of
the py3.12.3 / uv0.12.0 env. Affects independent reproduction, not archive
integrity.
CI runs `uv run pytest -m "not integration"` (`.github/workflows/ci.yml:67`).

Opt-in gated suites (markers declared in `backend/pyproject.toml:59-64`;
each self-skips unless its env var / URL is set):

| Suite | How to run | Needs | Gate location |
|---|---|---|---|
| Postgres integration | `TEST_DATABASE_URL=… uv run pytest -m integration` | a Postgres DB | `test_postgres_repositories.py:32-36` (`skipif TEST_DATABASE_URL is None`) |
| Schemathesis contract fuzz (GET only) | `SCHEMA_TESTS=1 uv run pytest -m schema` | none (in-process) | `test_schema_conformance.py:28-32` |
| Live Bedrock | `BEDROCK_LIVE=1 uv run pytest -m live` | real AWS creds (costs cents) | `pyproject.toml:61` marker; weekly `live-tests.yml:44` |
| Live Gmail | `GMAIL_LIVE=1 uv run pytest -m gmail_live` | Gmail creds under `.secrets/` | `pyproject.toml:62`; `live-tests.yml:45` |
| Live browser | `BROWSER_LIVE=1 uv run pytest -m browser_live` | `playwright install chromium` | `pyproject.toml:63`; `live-tests.yml` |

CI also runs ruff lint + `ruff format --check` + `mypy src tests` +
Alembic up/down/up (`ci.yml:58-72`) and the schema job as a separate
parallel gate (`ci.yml:97-102`). Live suites run weekly, never in PR CI
(`live-tests.yml`).

---

## 2. Reading list (priority order)

Ten files that carry the load-bearing claims. Each line says what to verify.

1. `backend/src/workflow_platform/engine/executor.py` — the whole
   EXECUTION_SEMANTICS contract: state machine, parallel dispatch loop
   (`_dispatch_loop` L615), edge-condition fail-closed (`_is_edge_active`
   L798-826), skip propagation (`_schedule_or_skip` L681-715), retry
   wrapper (`_run_step_with_retry` L830), budget post-step
   (`_check_budget` L725), pause/kill between steps (`_maybe_pause` L776),
   fork loads current definition (`fork` L239), tool-param pinning +
   `tool_param_override_blocked` audit (L1040-1095), recall verbatim
   injection (L1024-1033).
2. `backend/src/workflow_platform/agent/agent.py` — tool dispatch,
   capability gate (`context.capabilities.tool_allowed`, ~L234-236),
   pinned-param application + `pin_overrides` recording (~L199-213).
3. `backend/src/workflow_platform/security/capabilities.py` —
   `resolve_capabilities` (L92) layer intersection, `tool_allowed` (L50),
   `max_tokens_per_call` takes `min` (L66).
4. `backend/src/workflow_platform/tools/email.py` — `EmailLabelApplyTool`
   (L103), `wf/*` allowlist refusal (L156-159): the mutating Gmail tool boundary.
5. `backend/src/workflow_platform/connectors/email/gmail.py` —
   `_resolve_label_id` (L308-318) raises `GmailLabelNotFound`, never creates (no-create fence).
6. `backend/src/workflow_platform/connectors/email/auth_results.py` —
   `authentication_pass` (L41): ordered Authentication-Results parse,
   first trusted `mx.google.com` entry decides (defeats appended headers).
7. `backend/src/workflow_platform/engine/functions.py` —
   `record_email_triage` (L411): enum gates `category_valid` (L464) /
   `attention_valid` (L469), `apply_labels` composed only from validated enums (L487-491).
8. `backend/src/workflow_platform/auth/local.py` + `auth/passwords.py` +
   `auth/middleware.py` — Argon2id (passwords.py:15), hashed sessions
   (local.py:42,113-115), CSRF Origin check (middleware.py:121-124 + ws.py:99-102).
9. `backend/src/workflow_platform/auth/scope.py` (`OrgScope` L19,
   `resolve_org_scope` L29) + `api/ws.py` (`event_deliverable` L33) —
   tenant boundary resolution + WS org filter.
10. `backend/src/workflow_platform/persistence/postgres.py` — per-repo
    `async with self._sf() as s, s.begin()` (each method its own
    transaction): backs the "persistence is NOT atomic" claim.

Key test files: `test_org_isolation.py`, `test_auth_local.py`,
`test_email_triage_apply.py`, `test_auth_results.py`,
`test_learned_memory.py`, `test_conditional_edges.py`,
`test_tool_param_pinning.py`, `test_capability_enforcement.py`.
All confirmed present.

---

## 3. EXECUTION_SEMANTICS claim map

| § | Claim | Code (verified) | Test (verified) | Status |
|---|---|---|---|---|
| §1 | Instance states PENDING→RUNNING→{COMPLETED\|FAILED\|KILLED}, ↕PAUSED | `persistence/models.py:26-33` (`WorkflowInstanceState`) | many; e.g. `test_pause_resume.py::test_pause_then_resume_completes_remaining_steps` | VERIFIED-IN-CODE |
| §1 | Step states: FAILED vs SKIPPED vs (sibling) CANCELLED, persisted distinctly | `models.py` `StepExecutionState.CANCELLED` (added 2026-08-01); `executor.py::_cancel_pending` marks in-flight rows CANCELLED on sibling-failure/pause/kill | `test_parallel_execution.py::test_failure_in_one_branch_cancels_pending_siblings` (now asserts sibling row is CANCELLED, failing step FAILED, completed_at set) | **VERIFIED-CODE+TEST** (the prior revision's claim was false — the state did not exist; now implemented + pinned, per the 2026-08-01 review) |
| §1 | Retry of FAILED instance: FAILED→PAUSED then `resume()`; failed step re-runs (already_done = COMPLETED+SKIPPED only) | `api/workflows.py:1050-1052` (sets PAUSED, calls `engine.resume`); `executor.py:229-234` (`already_done` filters COMPLETED+SKIPPED) | retry endpoint test pins the 200/"retry_started" surface; resume mechanics in `test_pause_resume.py` | **PARTIAL** — (a) FAILED→PAUSED→resume: CODE+TEST; (b) the failed step actually re-runs while COMPLETED/SKIPPED don't: CODE only (no dedicated test asserts the re-execution) |
| §1 | KILLED terminal/non-resumable | `executor.py:74-78` (`_KillRequested`), `_maybe_pause` L785-787 | `test_lifecycle_endpoints.py::test_kill_running_instance` (L106), `::test_kill_terminal_instance_rejected` (L117) | VERIFIED-IN-CODE |
| §1 | recovery-reasoning categories NOT machine-recorded | (doc self-declares "Not provided") — no such field exists in `models.py` | n/a | DOC-ONLY *by the doc's own admission* (G21); not a defect |
| §2 | email trigger: at-least-once, persisted cursor + last-500 seen-id ring | `triggers/gmail_poll.py:166-167` (`deque(maxlen=500)`), `_persist_cursor` L277, `_mark_seen` L291; `TriggerCursorState` `models.py:88-99` | `test_trigger_cursor.py::test_restart_fires_missed_mail_exactly_once` (L101: m-2 fires once, boundary m-1 not re-fired) + `::test_poll_persists_cursor_and_seen_ids` | VERIFIED-IN-CODE |
| §2 | webhook: one POST → one instance, in-request, no queue, no persist-before-ack | `api/workflows.py:1254-1304` (`fire_webhook` → `registry.fire` synchronously L1299) | `test_webhook_trigger.py::test_webhook_endpoint_fires_registered_trigger` (L57) | VERIFIED-IN-CODE (no test pins the "crash-after-accept loses instance" negative — inherent, untestable cheaply) |
| §2 | webhook HMAC when `secret_name` set; fail-closed if secret missing | `api/workflows.py:1265-1290` (401 bad sig, 503 if secret unloadable) | `test_webhook_hmac.py::test_missing_signature_401`, `::test_missing_secret_fails_closed_503`, `::test_signature_checked_before_json_parse` | VERIFIED-IN-CODE |
| §2 | schedule: missed ticks NOT replayed; manual/API: one request→one instance | `triggers/schedule.py`; `api/workflows.py` run endpoint | `test_schedule_trigger.py`; `test_lifecycle_endpoints.py::test_run_workflow_creates_instance` (L161) | VERIFIED-IN-CODE |
| §3 | Independent branches run concurrently (asyncio.wait FIRST_COMPLETED) | `executor.py:646-649` | `test_parallel_execution.py::test_two_independent_steps_run_concurrently` (L29), `::test_diamond_topology_runs_branches_in_parallel` | VERIFIED-IN-CODE |
| §3 | Edge condition that ERRORS fails the instance (fail closed) | `executor.py:_is_edge_active` L811-826 (raises RuntimeError unless opt-out) | `test_conditional_edges.py::test_condition_eval_error_fails_instance_by_default` (L166: asserts FAILED + "failed to evaluate") | VERIFIED-IN-CODE |
| §3 | `on_error: inactive` opt-out marks target skipped (NOT safety-checked yet) | `executor.py:812-817` (returns False) | `test_conditional_edges.py::test_condition_eval_error_opt_out_marks_target_skipped` (L196: asserts COMPLETED, target skipped) | VERIFIED-IN-CODE (the "validate_definition should reject unsafe opt-out" part is G21, unbuilt — DOC-ONLY future) |
| §3 | All-inactive incoming edges → SKIPPED; skip propagates downstream | `executor.py:_schedule_or_skip` L697-715 | `test_conditional_edges.py::test_skip_propagates_through_chain` (L86), `::test_target_runs_if_at_least_one_incoming_edge_active` (L127) | VERIFIED-IN-CODE |
| §3 | Step failure after retries cancels in-flight siblings; effects not undone | `executor.py:655-657` (`_cancel_pending` then raise) | `test_parallel_execution.py::test_failure_in_one_branch_cancels_pending_siblings` (L69) | VERIFIED-IN-CODE |
| §3 | **Persistence NOT atomic across records** (separate transactions) | `persistence/postgres.py` — each repo method opens its own `async with self._sf() as s, s.begin()`: instance update L141, step create L219 / update L224, audit append L288 — no shared txn wrapping step+instance+audit | none | **VERIFIED-CODE** (code confirms separate transactions; no test exercises the crash-window — accepted, hard to pin, G21) |
| §4 | `runtime.retries` default 0; retry re-runs the whole step | `workflow/definition.py:35` (`retries: int = 0`); `executor.py:838` (`attempts = runtime.retries + 1`) | `test_retries_and_timeouts.py::test_step_retries_succeed_on_second_attempt` (L34), `::test_step_retries_exhausted_fails_workflow` (L73) | VERIFIED-IN-CODE |
| §4 | Retries NOT engine-gated on error class / `Tool.effect` (authoring rule only) | `executor.py:830-860` (retries any `StepFailure`, no effect check) | The bundled-example rule ("retries>0 only on idempotent") is asserted for `apply` (retries=2) via workflow shape tests, not a general engine gate | VERIFIED-IN-CODE (absence-of-gate is the honest claim; static check is G21, unbuilt — DOC-ONLY future) |
| §5 | Token budget checked AFTER each step; `notify`/`pause`/`escalate` | `executor.py:_check_budget` L725-774 (called at L661 after step success) | `test_cost_metering.py::test_budget_pause_action_pauses_after_breach` (L160: PAUSED, `budget_exceeded`+`workflow_paused` audited, step b never ran), `::test_budget_notify_action_continues_with_audit`, `::test_budget_escalate_emits_special_action` | VERIFIED-IN-CODE |
| §5 | Per-step + per-workflow timeouts | `executor.py:_dispatch_step` L938-946 (step), `_drive_inner` L437-444 (workflow) | `test_retries_and_timeouts.py::test_step_timeout_is_treated_as_failure` (L106), `::test_workflow_timeout_kills_in_flight_steps` (L141) | VERIFIED-IN-CODE |
| §5 | Pause/kill polled BETWEEN steps; running step completes; in-flight call not interrupted | `executor.py:_maybe_pause` L776-787 (re-reads instance state each loop iteration) | `test_pause_resume.py::test_pause_then_resume_completes_remaining_steps` (L19); kill in `test_lifecycle_endpoints.py::test_kill_running_instance` | VERIFIED-IN-CODE |
| §6 | Compensation/rollback NOT provided; recovery is retry/fork/manual | (no rollback code exists; retry+fork endpoints do) | n/a (absence) | VERIFIED-IN-CODE (honest absence) |
| §7 | Crash recovery: re-drive with `already_done` from persisted COMPLETED/SKIPPED; RUNNING step re-runs | `executor.py:resume` L229-234, `_build_dag_state` L607-612, `_dispatch_loop` seeds `scheduled` L628 | `test_pause_resume.py` (resume path); no dedicated process-crash test (restart-triggered recovery is a deployment property) | VERIFIED-IN-CODE (crash-restart itself not simulated in a test) |
| §7 | Recovery restart-triggered, NOT lease-arbitrated (single-process) | (no lease/heartbeat code) | n/a | VERIFIED-IN-CODE (honest limitation; leases are G21) |
| §8 | In-flight instance NOT version-bound: retry/resume/fork load CURRENT definition by id | `api/workflows.py:1042` (retry `definitions.get`), `1078` (fork `definitions.get`); `executor.fork` L239 drives current def + current memory | `test_engine_fork.py::test_fork_reruns_with_current_agent_memory` (L228: fork picks up post-source memory edits) | VERIFIED-IN-CODE |
| §8 | Fork = migration variant WITHOUT compatibility validation; no stored def-hash | `executor.fork` L239-322 (no hash compare); no def-hash field on `WorkflowInstance` (`models.py:43-56`) | `test_engine_fork.py::test_fork_at_middle_preserves_upstream_reruns_downstream` (L139), `::test_fork_records_workflow_forked_audit_entry` (L167) | VERIFIED-IN-CODE (the reviewer-proposed def-hash guard is G21, unbuilt) |
| §8 | Delete requires `force=true` when run history exists; non-terminal runs 409 regardless | `api/workflows.py:667-675` (409 unless force), `652-661` (409 on live runs) | `test_delete_workflow_endpoint.py::test_delete_refused_while_instances_live` (L98), `::test_delete_no_history_needs_no_force` (L149), `::test_delete_allowed_once_instances_terminal` (L121) | VERIFIED-IN-CODE |

---

## 4. THREAT_MODEL claim map

### Injection ladder (§4 rungs 1–6) + write fences

| Rung | Claim | Code (verified) | Test (verified) | Status |
|---|---|---|---|---|
| 1 | Content readers hold NO tools (`tools: []`) | `examples/email_triage_apply/workflow.yaml` classifier + attention both `tools: []`; `examples/email_triage_live/workflow.yaml:47-49` | `test_email_triage_apply.py::test_classifier_fence_and_apply_shape` (L128: `triage.tools == []`, caps `[]`, `classify_attention.tools == []`); `test_email_triage_workflow.py::test_live_validation_workflow_is_read_only` (L92) | VERIFIED-IN-CODE |
| 1 | *(disposition — finding 7)* the pre-split `examples/email_triage/workflow.yaml` content-reading step holds `email_send`+`email_label_apply` | **now trigger `manual`** (orchestrator no-op → NOT auto-registered/polled = not a live surface) + deprecation banner | `test_email_triage_workflow.py::test_workflow_yaml_loads_and_has_expected_shape` (now asserts `trigger.type == "manual"`) | **VERIFIED-CODE+TEST** — neutralized; deployed fenced variants are `_apply` (prod) / `_live` |
| 2 | Enum vocab gates: `category_valid` / `attention_valid` against enums | `engine/functions.py` `record_email_triage` L411, `TRIAGE_CATEGORIES` L384-390, `ATTENTION_LEVELS` L393, `category_valid` L464, `attention_valid` L469-483 | `test_email_triage_apply.py::test_record_email_triage_category_valid_field` (L172), `::test_two_axis_apply_labels_composition` (L198) | VERIFIED-IN-CODE |
| 2 | Tool-holder gets enum-derived `apply_labels` list, never free text | `functions.py:487-491` (`wf/{category}` gated on `category_valid`, `wf-attn/{value}` on `attention_valid`) | `test_email_triage_apply.py::test_two_axis_apply_labels_composition` (L198: invalid category → `["wf-attn/review"]`, etc.) | VERIFIED-IN-CODE |
| 3 | Input minimization: `inputs:` strips trigger content, rubric memory, recall from apply step | `executor.py:1002` (`minimized = step.inputs is not None` → no memory, no recall), `_build_user_message` L1352 (only named paths) | `test_email_triage_apply.py::test_hostile_trigger_text_never_reaches_apply_step` (L268: hostile subject/body, "Prior agent memory", "Learned memory about this correspondent" all absent from apply prompt) | VERIFIED-IN-CODE |
| 3 | Hostile content / rubric / recall absent from minimized apply step | `executor.py` `inputs:` handling + `_build_user_message` | `test_hostile_trigger_text_never_reaches_apply_step` (asserts hostile subject/body/rubric/recall markers ABSENT) | **VERIFIED-IN-CODE** (the earlier "token counts asserted" clause was an overstatement — corrected in THREAT_MODEL §4 to "content-absence, not a token-count assertion" on 2026-08-01) |
| 4 | 8-label `wf/*` allowlist at tool boundary | `tools/email.py` `EmailLabelApplyTool`, `allowed_labels`, refusal; the 8 built in `main.py:138-140` | outside-namespace refusal: `::test_allowed_labels_refuses_outside_namespace`; **exact set**: `::test_label_allowlist_is_exactly_the_intended_eight` (added 2026-08-01 — pins the literal 8, no INBOX/TRASH/wildcard) | **VERIFIED-CODE+TEST** |
| 4 | Gmail no-create fence: unknown label raises, never creates | `connectors/email/gmail.py:_resolve_label_id` L308-318 (raises `GmailLabelNotFound` L317; `create_label` L295 unreachable from the tool) | `test_gmail_connector.py::test_apply_labels_unknown_label_raises_gmail_label_not_found` (L395) | VERIFIED-IN-CODE |
| 4 | Tool-param pinning: engine forces `message_id`+`labels`; override → `tool_param_override_blocked` audit | AGENT: `agent/agent.py:199-213` (overwrites `tool_input[key]`, records `pin_overrides`, `pinned`); ENGINE audit: `executor.py:1087-1095` | `test_tool_param_pinning.py::test_pinned_param_overrides_model_value` (L51), `::test_pin_injected_when_model_omits_it`, `::test_no_override_flagged_when_model_agrees`; end-to-end audit: `test_email_triage_apply.py::test_pinning_defeats_a_steered_apply_agent` (L504: `tool_param_override_blocked` with `params=={"message_id","labels"}`) | VERIFIED-IN-CODE |
| 5 | Agents have NO memory-write tool; engine writes provenance-tagged observations | `memory/learned.py:176` `observe` is a service method, called only from `executor.py:1225`+; no `Tool` subclass writes memory (enumerated) | `test_learned_memory.py::test_engine_observes_after_completed_run` (L188: actor_type "engine"), `::test_workflow_without_spec_makes_no_memory_calls` | VERIFIED-IN-CODE |
| 5 | Third-party claims quarantined; never-assert fence injected VERBATIM | `executor.py:1024-1033` (injects `recalled.context` verbatim); `memory/learned.py:279-304` returns veracium's fenced block unchanged; provenance `observe(author=…)` L187-197 | `test_learned_memory.py::test_engine_injects_recall_verbatim_with_fence` (L495: fence text "UNVERIFIED THIRD-PARTY CLAIMS (never assert as fact)" present in system prompt), `::test_recall_context_returns_fenced_block` (L460), `::test_observe_writes_episode_and_meters_usage` (quarantined==1) | VERIFIED-IN-CODE |
| 5 | Recall entity normalized (attacker display names never key anything) | `memory/learned.py:normalize_entity` (lowercase, strip `+tag`); recall/observe key off `trigger.from_address.address`, not the display name | `test_learned_memory.py::test_normalize_entity` pins address canonicalization | **PARTIAL** — address normalization is CODE+TEST; the end-to-end "hostile `From: display <x@y.com>` keys ONLY by `x@y.com`" call-path is CODE-verified but not pinned by a dedicated test. Named gap. |
| 6 | Codify bypass requires DKIM/DMARC-aligned auth; ordered AR parse defeats appended headers | `connectors/email/auth_results.py:authentication_pass` L41-60 (first trusted `mx.google.com` entry decides, returns without falling through) | `test_auth_results.py::test_first_trusted_entry_decides_attacker_appended_ignored` (Gmail fail topmost + attacker pass below → False) — **8 tests confirmed** in file | VERIFIED-IN-CODE |
| 6 | Codified runtime: auth gate + drift-disable overlay + fail-open | `engine/functions.py:codified_sender_check` L1244 (auth L1266, fail-open on missing rules L1278, combined gate L1311); overlay writer `_disable_codified_sender` L569 | `test_codified_runtime.py::test_unauthenticated_listed_sender_gets_judgment` (auth gate), `::test_schema_mismatch_expiry_inactivity_and_overlay_disable`, `::test_unlisted_and_missing_file_fail_open` | VERIFIED-IN-CODE |

**§4a two claims kept separate:** authority containment (hard invariant)
is the ladder above — test-pinned. Decision robustness (which of the 8
labels a hostile mail steers) is explicitly *empirical, not pinned*
(§9c) — correctly not claimed as a test guarantee.

### Tenant isolation (§5)

| Claim | Code (verified) | Test (verified) | Status |
|---|---|---|---|
| `OrgScope` resolution; missing user row fails closed (403) | `auth/scope.py:19` (`OrgScope`), `:29` `resolve_org_scope` (Administrator→None unscoped; non-admin→org_id; missing→403) | exercised across `test_org_isolation.py` | VERIFIED-IN-CODE |
| Cross-org read/mutation = 404, never 403 (no existence oracle) | `api/workflows.py:179,183` (`_visible_workflow`/`_visible_instance` raise 404) | `test_org_isolation.py::test_cross_org_reads_are_invisible`, `::test_cross_org_mutations_404` | VERIFIED-IN-CODE |
| Instances inherit definition's org_id | `executor.py:175` (`org_of(definition.id) or "default"`) | `test_engine.py::test_instance_inherits_definition_org` (acme-owned definition → acme instance, added 2026-08-01) | **VERIFIED-IN-CODE** |
| WS events carry org_id from emit; filter per subscriber; fail-closed | `api/ws.py:33` `event_deliverable`, `:74` `_subscriber_org` | `test_org_isolation.py::test_ws_never_delivers_foreign_org_events`, `::test_event_deliverable_primitive_negative_and_positive` | VERIFIED-IN-CODE |
| Administrator cross-org access audited as `org_bypass` | `api/workflows.py:187` `_bypass` + `_note_bypass` | `test_org_isolation.py::test_admin_cross_org_mutation_audits_bypass` | VERIFIED-IN-CODE |
| Learned memory namespaced `org:<org>:user:<key>` | `memory/learned.py:42-48` `memory_namespace` | `test_learned_memory.py::test_engine_observes_after_completed_run` (L189 uses `memory_namespace("default",…)`) | VERIFIED-IN-CODE |
| Escalation guards: last-Administrator + per-org last-org-admin; Org Admins can't touch Administrators; roles exclusive per mode | `api/users.py:96,237` (last-admin 409), `107,250` (per-org 409), `118,183` (forbid Administrator grant); `middleware.py:139` (no local JIT) | `test_users_api.py` exists + CI-passes; its exact test fns were **not re-read for this index** | **VERIFIED-CODE** (guard code read; pinning tests named but not individually re-confirmed here) |
| Seven isolation invariants | — | `test_org_isolation.py` (1,2,4,5,6,6b,7 — WS now forbidden-first + direct `event_deliverable` primitive test); criterion 3 in `test_users_api.py` (not re-read, row above) | **PARTIAL** — 1,2,4,5,6,6b,7 CODE+TEST here; 3 CODE-only. All against IN-MEMORY repos; the Postgres §4b joins are NOT exercised (G22) |
| Intra-org isolation is coarse (role-graded, not per-owner) | (by design — no per-owner filter) | n/a (honest scope statement) | VERIFIED-IN-CODE (named boundary, §5) |

### Auth surface (§6)

| Claim | Code (verified) | Test (verified) | Status |
|---|---|---|---|
| `dev` headers inert outside dev mode | `auth/middleware.py:104-124` (local/oidc branches never read `X-Dev-*`) | `test_auth_local.py::test_dev_headers_ignored_in_local_mode` (L220) | VERIFIED-IN-CODE |
| Argon2id + rehash-on-verify | `auth/passwords.py:15,18,31`; rehash on login `auth/local.py:109-111` | `test_auth_local.py::test_password_roundtrip_and_rehash_flag` (L73: `$argon2id$` prefix) | VERIFIED-IN-CODE |
| Opaque sessions stored HASHED (sha256); revoke = row delete | `auth/local.py:42` `hash_token`, `113-115` (token in cookie only, hash in DB), logout `153` | `test_auth_local.py::test_tokens_stored_hashed_and_absent_from_audit` (L173), `::test_logout_revokes_on_next_request`, `::test_deactivation_revokes_immediately` | VERIFIED-IN-CODE |
| HttpOnly SameSite=Lax cookie | `api/auth.py:57-67` (`httponly=True`, `samesite="lax"`) | `test_login_sets_cookie_and_authenticates` — now asserts `httponly` + `samesite=lax` in Set-Cookie (added 2026-08-01) | **VERIFIED-IN-CODE** |
| CSRF Origin check on cookie'd non-GET AND WS upgrade | `middleware.py:121-124` (403); `ws.py:99-102` (close) | `test_auth_local.py::test_cross_origin_post_rejected` (L204), `::test_ws_cookie_auth_and_origin_check` (L229) | VERIFIED-IN-CODE |
| Enumeration-resistant login + dummy-verify timing | `auth/local.py:97-101`, `passwords.py:34` `dummy_verify` | `test_auth_local.py::test_login_failures_are_indistinguishable` (L111: dummy called exactly once, identical bodies) | VERIFIED-IN-CODE |
| Rate limits per IP + per email | `api/auth.py:43` (loops `ip:` + `email:`); `local.py:50` `LoginRateLimiter` | `test_auth_local.py::test_login_rate_limited` (L270), `::test_login_endpoint_returns_429` (L279) | VERIFIED-IN-CODE |
| oidc: JWKS-validated JWT, IdP sole authority | `auth/oidc.py:56` `validate` (iss/aud/exp/RS256) | `test_auth.py::test_oidc_validator_accepts_valid_token` (+ expired/wrong-issuer/wrong-audience variants) | VERIFIED-IN-CODE |
| "Fifteen pinned security tests in test_auth_local.py" | — | **15 test functions confirmed** by reading the whole file | VERIFIED (count accurate) |

### Capabilities (§7)

| Claim | Code (verified) | Test (verified) | Status |
|---|---|---|---|
| Intersection system ∩ workflow ∩ step ∩ runtime; narrowest wins | `security/capabilities.py:92` `resolve_capabilities`, `:50` `tool_allowed`, `:66` `max_tokens` takes min | `test_capabilities.py::test_layered_tool_intersection_most_restrictive_wins` (L28), `::test_runtime_cannot_widen_system` (L48), `::test_max_tokens_takes_min_across_layers` (L89) | VERIFIED-IN-CODE |
| Enforced at Agent dispatch AND inside tools (file tools check path scope internally) | Agent: `agent/agent.py:234`; file tools: `tools/filesystem.py:36` (`can_read`)/`:73` (`can_write`), `pdf_extract.py:48`, `image_ocr.py:72` | `test_capability_enforcement.py::test_agent_denies_tool_outside_allowlist` (L24), `::test_file_read_rejects_path_outside_acl` (L114), `::test_file_write_rejects_path_outside_acl` (L137) | VERIFIED-IN-CODE |

---

## 5. Known gaps the review will hit (preempted)

These are honestly not covered. Cross-referenced to the G-numbers in
`docs/NEXT_STEPS.md` and the contract docs.

1. **Non-atomic persistence has no test** (EXECUTION_SEMANTICS §3). The
   separate-transaction structure is code-visible in
   `persistence/postgres.py`, but no test exercises a crash between the
   step-commit, instance-update, and audit-append. Accepted small window
   at single-operator scale; shared-txn step-commit is **G21**.

2. **"Token counts asserted" (THREAT_MODEL §4 rung 3) is DOC-ONLY.** The
   apply-step leak test proves hostile content / rubric / recall never
   reach the minimized step, but asserts no token count. The prose
   overstates by one clause; the security-load-bearing part (content
   absence) is fully pinned.

3. **Fake-Gmail coverage is not real-connector conformance.** All Gmail
   tests run against `FakeGmailService` (`tests/_email_fakes.py`).
   There are no per-connector fake-vs-real contract tests — a divergence
   between the fake and the live `googleapiclient` behavior would pass
   CI. Live behavior is only checked weekly (`live-tests.yml`,
   `test_gmail_live.py`, opt-in).

4. **In-memory-repo isolation tests don't exercise the Postgres §4b SQL
   joins (G22).** `test_org_isolation.py` runs against
   `InMemoryRepositories`; the audit/steps/cost org-scoping *joins* that
   Postgres performs are not driven by the isolation suite. A generated
   route-to-scope inventory proving every surface participates is the
   named next step (**G22**, THREAT_MODEL §5).

5. **Instance→org inheritance branch — NOW TESTED** (closed 2026-08-01): `executor.py:175`
   copies `org_of` forward but the isolation tests pre-seed `org_id`.

6. **Cookie HttpOnly/SameSite flag values — NOW TESTED** (closed 2026-08-01).

7. **Schemathesis fuzzes GET only** (THREAT_MODEL §8 row; `TESTING.md`
   roadmap). Mutating verbs (POST/PATCH/DELETE) are not fuzzed.

8. **Deferred G21 items, designed-not-built** (EXECUTION_SEMANTICS
   §4/§8, THREAT_MODEL §8): static retry/effect-gating validation in
   `validate_definition`; soft-delete/archive with retained immutable
   versions; worker leases / multi-process recovery arbitration;
   concurrency keys; fork def-hash version-binding guard; persisted inbound
   queue + idempotency key. Each is prose-only today — do not expect code.

9. **Two-axis + codify ARE built and cut over** (correcting a stale claim
   the earlier revision got backwards — external review finding 3):
   `EMAIL_TRIAGE_TWO_AXIS_PLAN` is **BUILT + CUT OVER 2026-07-26**,
   `EMAIL_TRIAGE_CODIFY_PLAN` **BUILT + CUT OVER 2026-07-30**. What remains
   **FUTURE/UNBUILT within codify (G23)** is narrow: runtime **rubric-hash
   enforcement** (schema version IS checked) and the **`skip_if`** engine
   surface (the decision_source query contract is the live anti-feedback
   guard). The codify runtime path — precheck routing, auth gate, disable
   overlay, sampling — is built and pinned (`test_codified_runtime.py`).

10. **Recovery-reasoning categories, compensation, exactly-once, mid-step
    budget, schedule catch-up, approval expiry** — all explicitly "Not
    provided" (EXECUTION_SEMANTICS §9). Absence is the contract, not a bug.

---

*Every citation above was read at commit `9a9f822`. Line numbers drift on
edit; re-anchor by symbol name if the file has changed.*
