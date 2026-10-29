# G30 capability review — round 3

**Package:** `g30-capability-review-r3-3e12786.tar.gz`  
**Review date:** 2026-10-29  
**Disposition:** **Request changes for complete round-three signoff: one P2 implementation finding and one P3 contract clarification.** The build-context and upload repairs are accepted. The read repair handles successful replies correctly, but does not cover error replies.

The current shipped policy continues to exclude browser tools. The remaining implementation finding concerns a browser-enabled policy; it does not establish a failure in the reported production runs. The original service-default correction remains accepted.

## 1. What is closed

| Round-two finding | Ruling | Independently checked |
|---|---|---|
| R2-F1 — outdated ECR build context | **Closed** | The actual script now supplies the repository root and the explicit backend Dockerfile. A recording Docker stub stopped execution at the build command, before login or push. All required COPY sources exist under that context. The revised build-site tests pass. |
| R2-F2 — a queued upload becomes a directory | **Closed for the stated regular-file/bytes contract** | The source is opened after acquiring the operation lock, its descriptor is checked, and the connector receives bytes. The queued-directory refusal and allowed-file twin pass. An additional probe replaces the pathname after the descriptor is inspected and confirms that the original open file's bytes are sent. Size-limit boundary checks pass. |
| R2-F3 — navigation during a read | **Successful-result path corrected; finding class remains open** | The new URL/navigation-count comparison refuses successful results after a transition, including an off-list-and-back transition. Error results bypass that comparison and can carry document text; see R3-F1. |

The operation lock still preserves policy ownership across cancellation in an independent probe. There is no new concern with the service default, per-run output directory construction, or the earlier content-origin clarification.

The upload change is materially stronger than moving the old directory check: Playwright no longer receives a pathname that it can reinterpret as a directory. Reading from the checked descriptor addresses the specific identity problem. This does not promise an atomic snapshot of a file being modified in place, nor remove the already disclosed path/symlink assumptions; the revised contract does not need to make either promise.

## 2. R3-F1 — P2: Error replies bypass the read fence and can deliver the unchecked document's text

**Primary locations:** `backend/src/workflow_platform/connectors/browser/playwright_connector.py:295–308`; `backend/src/workflow_platform/tools/browser.py:131–137,167–173`. Delivery path: `backend/src/workflow_platform/agent/agent.py:209–229`. Related action cleanup: connector lines 397–407 and 477–491.

The fence checks the navigation count and URL only after `await read()` returns normally. If Playwright raises, execution leaves `_fenced` before either the comparison or `_enforce_landing()`. The tool then includes `str(exc)` in `ToolResult.error`. The agent passes that result to the next model turn, even with status `error`.

An error is not necessarily content-free. The installed Playwright implementation constructs strict-selector errors using previews of the matched elements, including their text and attributes. Its client dispatches protocol error replies as exceptions. Both behaviors are documented with exact installed-source excerpts in this archive.

A concrete browser sequence to exercise is the supplied delayed-navigation scenario with **two** matching `.late` elements on the off-list landing page instead of one. The waiting read then raises a strict-match error rather than returning text. Its diagnostic contains previews of the off-list elements. The success-only fence does not inspect that completion path.

### Independent evidence

The local probes use the real connector and tools, with a recording page that commits navigation and raises a staged `PlaywrightError` in the documented diagnostic shape:

1. Start on the allowed page and enter the read.
2. Commit the off-list navigation, including the navigation event consumed by the connector.
3. Raise an error containing `OFF_LIST_DIAGNOSTIC_FIXTURE` in an element preview.
4. Observe the fixture returned in the tool error, without the result-withheld message. The page remains off-list.

This reproduces for both text and table reads. It also reproduces after the page returns to the original allowed URL: the navigation count changed, but the exception path never compares it.

A separate probe runs the same sequence through the **real workflow engine and agent loop**, replacing only the browser page and model service with local fixtures. The diagnostic reaches the next model request and the recorded step output. This establishes that an error status does not prevent delivery of the text.

The contrasts are important:

- The same transition with a successful underlying read is correctly withheld and the off-list page is cleared.
- The same diagnostic on an unchanged allowed document remains available, which is useful ordinary error behavior.
- A fill operation that changes pages and then raises also skips its final landing cleanup. The otherwise identical normal-return operation clears the page and fails as intended.

**Evidence limit:** These are deterministic connector/engine reproductions, not an independently observed Chromium strict-match sequence. The error shape and its document previews are supported by the installed Playwright source. `live_error_probe.py` supplies the local-server Chromium companion and a stable-page twin, but both were skipped here because Chromium could not be installed. The supplier's existing live test exercises a successful locator reply; it does not cover this error completion.

### Required correction

Validate the document/navigation state on exceptional as well as normal read completion. If it changed, discard the original diagnostic as well as any successful result, clear an off-list current page, and return a diagnostic that does not include material from the unchecked document. Merely wrapping the original exception in a message saying the read was withheld would still deliver that material.

Preserve useful ordinary errors when the checked document is unchanged. Keep cancellation semantics intact while performing appropriate cleanup. For actions, run the promised landing cleanup on error exits too; their effects still cannot be undone.

Add tests for an off-list landing with multiple matches, the same error after an off-list-and-back transition, a stable allowed-page error, and action failure after navigation. Exercise the model-facing result as well as the connector exception. The source-shape test requiring a final `_enforce_landing()` statement does not establish that every exit reaches it.

This is a remaining part of R2-F3, not a reason to undo the new navigation counter or the accepted upload repair.

## 3. R3-F2 — P3: The action wording promises detection of transitions that the final URL check cannot detect

**Locations:** `docs/G30_CAPABILITY_REVIEW_GUIDE_R3.md`, §2 action bullet and §5 first residual; `backend/src/workflow_platform/connectors/browser/base.py:106–113`; connector action methods ending in `_enforce_landing()`.

“Checked before, checked after” accurately describes the normal-return implementation. The stronger accompanying statement that an action which navigates off-list while waiting then fails and clears the page is not true for an off-list-and-back transition.

An independent fill probe emits both navigation events, returns to the exact initial allowed URL, and completes normally. The connector has counted two navigations, but the action returns success because it checks only the final URL. This is a connector-level state-transition probe; it does not claim an independently reproduced live action on the intermediate document.

**Correction:** If the intended contract remains endpoint checks, say: “On normal completion, if the final check finds an off-list page, the call fails and the page is cleared. Intermediate navigation, including leaving and returning to an allowed URL, is not detected by this check.” Adjust this wording again when R3-F1 adds cleanup on exceptional completion.

Alternatively, compare navigation generations for actions too and conservatively fail calls that changed documents, while continuing to disclose that an already performed effect cannot be reversed. That would be a stronger detection contract, with possible false failures for SPA navigation; it would still not be prevention.

This is a small contract correction, separate from the P2 error-delivery problem.

## 4. Answers to the design questions

### Is the narrower action contract the right one?

It is a reasonable description of the current partial browser protection once R3-F1 and R3-F2 are addressed. It is not sufficient to treat `allowed_hosts` as a guarantee about where an action's effect occurs.

For future enablement, distinguish navigation/request filtering from permission to act on a document. If host restrictions are intended to bound effects, require actions to remain associated with the checked document and refuse or stop before dispatch when that cannot be established. An element handle reduces locator retargeting, but—as the guide correctly notes—does not by itself prove that mouse input or the resulting effect stayed in the intended document. The per-connector lock does not stop the page's own navigation.

The observation that an allowed page can forward typed data does not fully justify actions on another page. A click or form submission can use the other page's existing authenticated state without transferring any newly typed data. This is an effect-location issue as well as an information-flow issue.

The current shipped exclusion remains the appropriate gate while destination enforcement and action semantics are incomplete. There is no need to block the accepted non-browser service/file changes on adopting a new action implementation now.

### Is the read fence's ordering argument sound?

The implementation is plausible for successful replies, and the counter is a meaningful improvement over URL equality alone. The installed client processes navigation events synchronously before resuming work awaiting later replies. The server's committed-navigation path updates frame identity and emits its navigation event. I found no demonstrated successful-reply ordering failure in this review.

However, FIFO handling in the Python client establishes the order in which messages already sent are consumed. It is not, by itself, proof of the browser/server ordering for every navigation type. The guide appropriately calls the broader assumption a residual. Keep the statement bounded to the tested behavior, and extend the real-browser matrix before broader enablement:

| Navigation kind | Current evidence and useful next check |
|---|---|
| Script navigation through an allowed hop to an off-list redirect landing | The supplier's live test directly covers this combined sequence for a successful read; its control removes the fence. Add the strict-match error variant from R3-F1. |
| Ordinary script navigation and same-document history changes | Successful-result fake tests establish counter handling. Add live script navigation plus `pushState`/hash cases, retaining the disclosed conservative refusal of same-document changes. |
| History back/forward, including restoration to the same URL | The fake off-list-and-back test verifies the algorithm if events arrive. Exercise real history/BFCache restoration and same-URL document replacement to verify those events and their ordering. |
| Failed navigation or committed error page | The client does not emit the public `framenavigated` event for a navigation event carrying an `error`; its URL is still updated. An aborted provisional navigation is not automatically a replacement document. Test abort-before-commit and a committed Chromium error page separately, including locator errors. R3-F1 applies regardless of whether the event ordering is perfect. |

Child-frame navigation does not need to invalidate this specifically top-level contract. The absence of a content-origin guarantee is now explicit and should remain so.

### Does §2 still exceed the evidence?

Yes, in two specific places: the read statement omits content-bearing error replies, and the action statement implies detection of any off-list transition rather than the final-page observation actually made. R3-F1 and R3-F2 identify those corrections.

The revised upload statement is supported. The build command is repaired, but a command/path check is not a deployable-image certification. The guide still discloses the image's unqualified SQLite and unlocked dependency installation; retain those qualifications. I did not independently build an image or verify the supplied production activity.

## 5. Verification and limits

The round-three guide was read first. Review focused on the delta from the previously reviewed round-two package, with the current claim and control runners executed in full where their prerequisites were available. Historical guides and prior results were not treated as current evidence.

| Check | Independent result |
|---|---|
| Input archive SHA-256 | `2fd99266129f985cd97c8f023e4d2a21a43e9020913d7935403e88945ebabd00` |
| Source/tests aggregate SHA-256 | `569c00d2379b5341a9cda11aa050267e1a15780e618eeda3734f92b21986310e`, matching the manifest |
| Source/test files in aggregate | 260 |
| Claim runner | **176 passed**, across all 19 locally runnable claims; D3 and E5 not run |
| Controls | **49 fired and passed after restoration**; 3 optional controls not run (one Postgres, two Chromium) |
| Independent reviewer probes | **14 passed**; **2 optional Chromium companion cases skipped** |
| Full backend suite | **1,611 passed, 32 skipped, 14 failed** |
| Ruff check over the full backend | Passed |
| Ruff format over the full backend | 305 files already formatted |
| Mypy | No issues in 260 source files |
| Dependency audit | Completed: no known vulnerabilities reported for audited packages; the local `workflow-platform` package was skipped because it is not on PyPI |
| Source integrity | All **678 original regular files** unchanged in both the review extraction and the separate control extraction |

Some reviewer probes intentionally assert the observed gaps. Their passing status means reproduction succeeded, not that the behavior is acceptable.

### Runtime qualification

The final frozen environment uses CPython **3.14.8**, SQLite **3.53.1**, Veracium **0.27.1**, and Playwright **1.63.0**. Veracium's actual `runtime_supported()` predicate returned **False**, as recorded in `evidence/runtime.json`. No qualification gate was disabled.

The fourteen full-suite failures are the same unsupported-SQLite class as in the earlier review, in learned-memory tests and the real-store health test. The supplied qualified-runtime capture reports **1,626 passed, 31 skipped**. The difference is those fourteen failures plus the historical-git golden test skipped in this archive-only checkout. A full independent pass on the qualified runtime is therefore still unavailable here.

The first environment attempt selected Python 3.15, and an early shared-environment attempt had unavailable modules. Those attempts are retained separately and are not counted as product failures. The completed runs used explicit Python 3.14 environments, with controls in a separate source tree and environment. The final results above supersede the setup attempts.

### Optional and external checks

Both the full Chromium installation and a separate headless-only attempt failed on invalid/truncated downloaded archives. Consequently neither the supplier's seven E5 browser tests nor the two new optional companion cases was independently executed. The supplied capture reports E5 passing; that remains supplier evidence. No disposable Postgres service or Docker executable was available.

The initial offline dependency-audit attempt lacked a cached `pip-audit`. The subsequent online attempt completed with exit 0 and reported no known vulnerabilities for the packages it could audit. It explicitly skipped the local `workflow-platform` package because it was not found on PyPI. Both attempts are retained in `evidence/pip-audit*.log`. This is a dependency advisory check, not a correctness certification. The input lockfile is unchanged from round two.

The supplied live record reports 67 completed triage runs and four completed health-report runs under the layer. Those figures were read as supplied evidence only. No production system, mailbox, AWS operation, or actual model service was accessed. The script probes stopped at local recording stubs.

## 6. Acceptance checks for the next package

1. Extend document validation to read errors, preventing unchecked document previews from entering the model-facing result. Include a real-engine assertion and a real-Chromium multiple-match case.
2. Complete the promised action cleanup on error exits, and clarify or strengthen the off-list-and-back action contract.
3. Retain the accepted build-context, descriptor-to-bytes upload, operation-lock, default-layer, and per-run directory fixes.
4. Keep browser tools excluded until the separate destination and action enablement conditions are satisfied.
5. Re-capture the qualified-runtime suite and optional checks, preserving the manifest and controls. The existing successful-result read tests should remain alongside the new error-path tests.

The review is complete. The archive contains the report, runnable independent probes, the unexecuted Chromium companion, source excerpts, test logs, and input/integrity records.
