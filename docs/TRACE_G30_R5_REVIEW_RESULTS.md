# G30 capability review — round 5

**Package:** `g30-capability-review-r5-93cc364.tar.gz`  
**Review date:** 2026-10-29  
**Disposition:** **Request changes for complete browser-contract signoff: one P2 diagnostic finding remains.** The original cleanup-failure call is now sanitized and the screenshot publication/disposal repair is accepted. The next guarded call can return the URL that the first call withheld.

The shipped policy still excludes browser tools. This finding concerns a browser-enabled policy, not the reported non-browser production runs. The accepted service-default, build-context, upload, operation-lock, and per-run directory fixes remain accepted.

## 1. What is fixed

R4-F1's original single-call examples now behave as requested. A successful read, failed read, or failed action followed by a clearing failure returns the bounded `cleanup=failed` status without the original diagnostic or interrupting URL. The error correctly avoids claiming that clearing succeeded. The supplied tests and independent contrasts pass.

The screenshot repair also holds in the checked cases:

- A rejected capture never replaces an existing accepted target, whether clearing succeeds or fails.
- The pending file is removed after rejection, a capture error, and cancellation in the local probes.
- A successful capture is absent from the requested path while capture runs and is published after the fence succeeds.
- The operation lock is released after the tested capture error and cancellation.

These are actual filesystem checks with a synthetic page writing fixture bytes. They are not live-browser image captures. They support the stated requested-path publication contract; they do not establish isolation from arbitrary filesystem observers of the pending directory.

The new real-Chromium test is appropriately described as a closed-page clearing failure, not a reproduction of interrupted-navigation timing. It is useful evidence for an actual Playwright failure at that boundary. Its execution remains supplied evidence here because the local Chromium installation failed again.

## 2. R5-F1 — P2: The retry guard returns the URL withheld by the preceding failed-clear call

**Locations:** `backend/src/workflow_platform/connectors/browser/playwright_connector.py:265–279` and `:282–286`. Error delivery: `tools/browser.py:131–137` and `agent/agent.py:209–229`. Relevant tests: `test_g30_capability_enforcement.py:531–548,569–628`.

`_withhold()` now discards the landing helper's message and emits only a bounded status. However, `_enforce_landing()` still embeds `current!r` in both its `BrowserPageNotCleared` message and its ordinary cleared-page refusal. A call entering through `_guard()` exposes those messages directly through the tool error.

This matters specifically for the documented recovery procedure. After clearing fails, the page stays at the interrupting URL. On the next call, that URL becomes `current` and is copied into the diagnostic. The first result omitted it correctly; the retry returns it. It returns whether the second clearing attempt fails again or succeeds.

### Independent two-call reproduction

The probe uses the submitted page fixture and real connector/tool implementation:

| Step | Observed result |
|---|---|
| Begin a read on the allowed page; commit an off-list navigation; make clearing fail at a URL containing `RECOVERY_URL_FIXTURE` | The tool returns `cleanup=failed`. The URL fixture and original page quote are absent. |
| Call the same read tool again while clearing still fails | The entry guard refuses the operation, but its error includes the complete URL containing `RECOVERY_URL_FIXTURE`. |
| Contrast: make the second clearing attempt succeed | The entry guard clears the page and still includes that URL in its refusal. |

No second locator read occurs. This is a diagnostic-delivery defect, not evidence that the second call bypasses the host check to read the DOM.

A separate probe runs two tool calls through the **real workflow engine and agent loop**. Only the page and model service are replaced with local fixtures. The second model request sees the first sanitized refusal; the third model request sees the retry diagnostic containing the URL. The recorded step output also contains it. The original read-error quote remains withheld throughout.

**Evidence limit:** The page transitions and clearing error are staged, as in the supplied unit tests. The complete application recovery and model-delivery paths are executed. No competing-navigation timing or new live-browser result is claimed.

### Why the new tests miss it

The call-entry test starts with a different, unmarked `OTHER` URL. Its first failure message contains that old URL, while the fixture changes the page to the marked cleanup URL. The test checks that the new marker is absent from the first exception, which passes. It then clears successfully on the next call but checks only the cleared outcome, not whether that second error contains the marker.

The real-engine test likewise stops after one tool call. It establishes first-call sanitization but not the recovery sequence the error invites the model to perform.

### Required correction

Sanitize the landing refusal itself for **every caller**, including call entry, normal action completion, and navigation completion. Neither the failed-clear nor successful-clear refusal should contain the reached URL when honoring the stated rule that page-chosen URLs are not passed on. Replacing Playwright's error text while retaining the same URL in an application-generated message is insufficient.

A shared safe diagnostic representation can distinguish `page_not_allowed` at entry from `document_changed` during a call, while carrying the operation category and truthful cleanup status. The current `_withhold` suffix can remain. Remove the raw URL from the underlying exceptions as well, so future callers do not need to remember an additional redaction step.

Add the two-call engine regression with both second-clear outcomes. Also start a guard directly on a marked off-list URL and assert that the marker is absent from every model-facing refusal. Keep the assertions that no locator operation runs on guard failure and that ordinary allowed-page diagnostics remain useful.

**Evidence:** `reviewer_probes.py`; records `R4_REPAIR`, `RETRY_GUARD_URL`, and `ENGINE_RETRY_DELIVERY` in `evidence/reviewer-probes.log`. Numbered source is in `evidence/source-excerpts.md`.

## 3. Answers to the guide's questions

### Is leaving an uncleared page in place sufficient?

For the limited guarantee that subsequent calls do not read or act on an off-list document, rechecking before each operation on that document is sufficient in principle. The tests confirm refusal before another locator read. R5-F1 must still be fixed: refusing the DOM operation does not by itself sanitize the refusal.

I would accept that bounded behavior without requiring page replacement to close this review. For an enabled production browser, discarding or marking the page unusable after a failed clear is preferable recovery design. A clearing failure may mean the page is closed or broken; repeated checks on that same object may never recover. Leaving a live page in place also leaves its own activity running, consistent with the disclosed absence of an egress guarantee.

A practical design is to make the page unusable for document operations and create a replacement on an explicit subsequent navigation. Do not automatically replay the failed mutating action: its effect may already have happened. This is a recovery recommendation, not an additional blocking finding or a substitute for the existing browser-enablement conditions.

### Is the bounded status the right shape?

Yes. The reason, call category, original error category, and three cleanup outcomes give the model enough information to choose a recovery step without the page text or URL. The three cleanup outcomes are meaningful: no cleanup needed, clearing completed, or the attempt failed to establish clearing.

Keep those values controlled by the application. The current Playwright class names are reasonable error categories; a small mapping to stable categories would reduce coupling to library internals. If other code will parse the status, prefer structured fields alongside the readable error rather than parsing a bracketed suffix. That is an API improvement, not required to fix R5-F1.

The status must apply consistently to entry-guard errors as well as changed-document errors. A correct first-call suffix cannot protect the recovery sequence if the next error includes a raw URL.

### Does §2 still exceed what is shown?

The screenshot requested-path guarantee is supported by the supplied tests and the independent contrasts. The original single-call cleanup guarantee is also supported.

The broader claim that the failure is sanitized at every caller, with no URL passed on, remains incomplete across the next-call recovery sequence. R5-F1 identifies the exact remaining formatter paths. The action endpoint limitation, cancellation exception, lack of an effect-location guarantee, and outstanding navigation matrix are now stated clearly.

The stated browser exclusion and future destination/action requirements should remain. None of this review is a certification to enable browser tools under the shipped system layer.

## 4. Verification

The round-five guide was read first. Original-archive comparison found **20 added or changed files**, with no removed original files. The source stayed unchanged; mutation controls ran in a separate extraction and environment.

| Check | Independent result |
|---|---|
| Input archive SHA-256 | `ed8506f31c90755286b81ff5ec0132581400ff49104f1ec2cda7b3210abb8ff7` |
| Source/tests aggregate SHA-256 | `9437d6410491c7f3e947efe4e443bcfceccb06c2eadc5455b2e354152ebc00a6`, matching the supplied manifest |
| Source/test files in aggregate | 260 |
| Claim runner | **220 passed** across all 19 locally runnable claims; D3 and E5 not run |
| Controls | **60 fired and passed after restoration**; 5 optional controls not run (one Postgres, four Chromium) |
| Independent reviewer probes | **12 passed**, covering first-call repair, recovery diagnostics, real-engine delivery, screenshot publication/disposal, cancellation, and stable-page errors |
| Full backend suite | **1,655 passed, 35 skipped, 14 failed** |
| Ruff check | Passed over the full backend |
| Ruff format | 305 files already formatted |
| Mypy | No issues in 260 source files |
| Dependency audit | Exit 0; no known vulnerabilities reported for audited packages; the local `workflow-platform` package was skipped because it is not on PyPI |
| Source integrity | All **688 original regular files** unchanged in both extractions |

The reviewer tests that assert the remaining gap are reproductions. Their passing status is not acceptance of that behavior.

### Runtime and evidence limits

The frozen environment uses CPython **3.14.8**, SQLite **3.53.1**, Veracium **0.27.1**, and Playwright **1.63.0**. Veracium's own `runtime_supported()` returned **False**, recorded in `evidence/runtime.json`. No qualification check was disabled.

The fourteen suite failures remain the unsupported-SQLite class in learned-memory tests and the real-store health test. The supplied qualified-runtime capture reports **1,670 passed, 34 skipped**. The difference is those fourteen failures plus the historical-git golden test skipped in this archive-only checkout. The dependency lock is unchanged from round four.

The headless Chromium installation was retried and failed on an invalid/truncated downloaded archive. The ten E5 tests and four browser controls were not independently run. The supplied capture reports them passing, including the real closed-page cleanup failure; that remains supplier evidence. No disposable Postgres service or Docker executable was available.

The supplied live record reports 78 completed triage runs and four completed health-report runs. Those records were read, not independently verified against production. No production system, mailbox, AWS operation, or actual model service was accessed.

## 5. Next acceptance checks

1. Remove reached URLs from both successful-clear and failed-clear landing refusals, consistently across their callers.
2. Add two-call engine tests showing that the next-call guard does not return the URL withheld by the first call, with clearing both succeeding and failing on retry.
3. Preserve the accepted first-call cleanup handling, truthful status, screenshot publication/disposal, and earlier fixes.
4. Keep browser exclusion and the documented enablement conditions. Re-capture the qualified-runtime and optional checks with the next package.

The review is complete. This archive includes the report, runnable probes, independent logs, source excerpts, and source identity/integrity records.
