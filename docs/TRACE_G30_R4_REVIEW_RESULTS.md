# G30 capability review — round 4

**Package:** `g30-capability-review-r4-8be82be.tar.gz`  
**Review date:** 2026-10-29  
**Disposition:** **Request changes for complete browser-contract signoff: one P2 finding remains.** The prior examples are corrected when cleanup succeeds, and the action-contract clarification is accepted. A failure of the cleanup operation can still replace the sanitized outcome with a content-bearing diagnostic and leave a rejected screenshot on disk.

The shipped policy continues to exclude browser tools. This review does not establish a failure in the reported non-browser production runs. The accepted service-default, build-context, per-run directory, upload-bytes, and operation-lock fixes remain accepted.

## 1. Disposition of the round-three findings

| Finding | Round-four ruling |
|---|---|
| R3-F1 — a read or action error after navigation reaches the agent unchanged | **The original scenarios are corrected. The withholding boundary is incomplete when cleanup itself fails; see R4-F1.** The supplied error/round-trip/stable-page tests and real-engine test pass. Independent contrasts also confirm the original diagnostic and its exception context are removed when cleanup succeeds. |
| R3-F2 — action wording implies detection of intermediate navigation | **Closed.** The guide and connector contract now describe endpoint checks, explicitly permit an undetected leave-and-return sequence on normal completion, and disclose that the action may use the new page's session. The corresponding behavioral test passes. |

The new `_acted` wrapper also includes click-download operations. The distinction between routing tests and behavioral exit-path tests is now appropriately stated. The supplied Chromium tests cover the two-match case and its stable-page twin; their execution is supplier evidence here because the local Chromium installation still failed.

An independent cancellation probe confirms the intended behavior: cancellation propagates, releases the operation lock, and a subsequent guarded call clears the off-list page when cleanup is available. This is consistent with the explicit cancellation exception in guide §1.

## 2. R4-F1 — P2: Withholding and screenshot disposal depend on the cleanup navigation succeeding

**Locations:** `backend/src/workflow_platform/connectors/browser/playwright_connector.py:340–360` (`_withhold`), `:242–261` (`_enforce_landing`), and `:425–440` (`screenshot`). The tool error passes through `tools/browser.py:131–137`; the agent sends it to the next model turn through `agent/agent.py:209–229`.

Once the document has changed, `_withhold()` attempts `_enforce_landing()`. That helper awaits `page.goto("about:blank")`. `_withhold()` catches only `BrowserHostDenied`, which is the deliberate exception raised **after** a successful clearing navigation. An ordinary Playwright exception from the clearing navigation escapes before the sanitized withholding error is raised.

This creates a second source of document-derived diagnostics. The original read/action error is successfully discarded, but the cleanup error is formatted directly into the tool result instead. The installed Playwright implementation can include a competing navigation's URL in a `goto` failure: its `gotoImpl` constructs a navigation-interrupted error using `event.url`. This is the same category of page-chosen URL that the round-four guide explicitly intends to omit from withheld errors.

The screenshot path compounds this issue. It writes the capture before the fence decides whether the result is acceptable, and deletes it only when the fence raises `BrowserHostDenied`. If cleanup instead raises a Playwright exception, that deletion branch is skipped. A screenshot which the document check rejected remains at the requested output path.

### Independent reproduction

The local probes use the real connector, tools, and—in one case—the real engine/agent loop. Only the browser page and model service are replaced with local fixtures.

1. Start a read or action on an allowed page.
2. Commit an off-list navigation before its result or error arrives.
3. When the connector tries to clear the page, commit a competing off-list navigation and raise a staged Playwright navigation-interrupted error containing `CLEANUP_URL_FIXTURE` in its URL.
4. Observe that the original page quote is absent, but the cleanup URL appears in the tool error. The final sanitized “withheld” error was never reached.

This reproduces for a successful read, a failed read, and a failed fill action. The real-engine probe confirms that the cleanup diagnostic reaches the next model request and recorded step output; an error status does not prevent its delivery.

The screenshot pair changes only whether cleanup succeeds:

| Cleanup result | Observed outcome |
|---|---|
| Clearing navigation succeeds | The result is withheld, the off-list page is cleared, and the screenshot file is deleted. |
| Clearing navigation raises | The raw cleanup diagnostic becomes the tool error and the screenshot file remains. |

The retained file in this probe contains the supplied fake page's synthetic screenshot bytes, not an actual browser image. It establishes the filesystem cleanup branch and its dependency on exception type.

**Evidence limit:** The interrupted-navigation diagnostic is staged at the page boundary. Its shape and URL inclusion are supported by the exact installed Playwright source, included in `evidence/source-excerpts.md`. This review did not reproduce the competing-navigation timing in Chromium. The demonstrated facts are the application control flow, actual tool/model delivery of the cleanup error, and actual retention of the synthetic screenshot file. No claim is made that the current production policy exposes this path.

### Required correction

Make the withholding decision independent of cleanup success. After the document has changed, every ordinary cleanup failure must still produce a sanitized refusal. It may report that cleanup failed using trusted, bounded metadata, but must not return the cleanup exception's text, URL, stack, or context. Do not report the page as cleared when that was not established. Keep cancellation behavior explicit.

Dispose of or quarantine rejected screenshot output independently of the cleanup exception type. Writing to a temporary output and publishing it only after validation is one way to avoid exposing a rejected capture at the requested path. Ensure a failed clearing attempt does not leave a page usable by later calls without another valid guard; discarding/recreating the page is an option if cleanup cannot complete.

Add regressions for cleanup errors after successful reads, failed reads, failed actions, and screenshots. Assert the model-facing result, absence of the page-chosen URL and chained raw diagnostic, truthful cleanup status, and disposal of the screenshot. Keep the successful-cleanup and stable-document contrasts.

The current tests make clearing succeed. They correctly establish the original repair but do not cover the new helper's own error exit. Removing the original exception context alone cannot address a fresh exception raised by that helper.

**Evidence:** `reviewer_probes.py`; records `REPAIR_TWIN`, `CLEANUP_ERROR_GAP`, `SCREENSHOT_CLEANUP`, and `ENGINE_CLEANUP_DELIVERY` in `evidence/reviewer-probes.log`.

## 3. Answers to the guide's questions

### Is “withhold if the document changed, keep otherwise” the right line?

Yes, for the stated top-level-document contract, subject to the disclosed event-ordering assumptions and R4-F1. Keeping a diagnostic from an unchanged checked document is consistent with permitting a normal read of that document. It helps the agent correct a selector or distinguish a timeout from another failure.

The content-origin residual does not require a different rule within this contract. If an allowed page incorporates material from another origin, both a normal read and an error preview can reflect it. Withholding all such errors would not establish content provenance while normal reads still return that same document. Stronger origin guarantees would need a separate design and evidence.

The new rule correctly uses the navigation count as well as the final URL, so an error after leaving and returning is withheld. The separate, weaker normal-action contract is now explicit and should remain distinct from the read/error contract.

### What else is safe and useful in a withheld error?

The original exception category and a truthful cleanup status are sufficient for the current purpose. A small structured schema would make those decisions easier to test and less dependent on prose. Reasonable additional fields are:

- A fixed code such as `document_changed` or `cleanup_failed`.
- The operation category, chosen from the connector's known call types.
- A cleanup outcome such as `cleared`, `not_needed`, or `failed`.
- An application-generated operation/attempt identifier and a fixed recovery instruction.

For the current Playwright error classes, retaining the class name is reasonable; a small mapping to stable categories would be less coupled to library internals. If cleanup also fails, classify that error separately without copying its text. Avoid retaining reached URLs, element previews, locator call logs, page titles, stacks, or arbitrary exception attributes in the withheld result.

Do not broaden the diagnostic just because an operator might find the original useful. The model-facing refusal should have a clear, bounded schema. Any separate diagnostic recording needs its own existing access and retention rules.

### Does §2 still overclaim?

The unconditional statement that changed-document output is withheld and nothing from either page is passed on is not yet true on the cleanup-failure path. The associated screenshot-disposal behavior is also incomplete there. R4-F1 is the required correction.

The action endpoint wording is now accurate for ordinary normal completion. The phrase “checked on every exit” should be read with—and preferably explicitly qualify—the cancellation exception already explained in §1. The independent cancellation probe supports that stated exception; I am not treating it as an additional finding.

The future enablement conditions are recorded in `NEXT_STEPS.md:3040–3046`: destination enforcement, document-bound effects if host restrictions are intended to bound effects, and the expanded live navigation matrix. Keeping those conditions outstanding is appropriate. The current read/error tests do not certify history/BFCache, same-URL replacement, or every error-page ordering.

## 4. Verification

The round-four guide was read before source inspection. The review compared the original round-three and round-four archives, finding 18 added or changed files and no removed original files. The input source remained unchanged; controls ran against a separate extraction and environment.

| Check | Independently observed result |
|---|---|
| Input archive SHA-256 | `e00c7bfa2272dc45a8f55d754e01ef53824fc7b0a98907a6f9165f196b8c785b` |
| Source/tests aggregate SHA-256 | `1f4371a31c438350b17125b0e744357d74af96c886285ec2018074c43ac195ac`, matching the supplied manifest |
| Source/test files in aggregate | 260 |
| Claim runner | **202 passed** across all 19 locally runnable claims; D3 and E5 not run |
| Controls | **55 fired and passed after restoration**; 4 optional controls not run (one Postgres, three Chromium) |
| Independent reviewer probes | **12 passed**, including repair contrasts, the remaining cleanup gap, exception-context handling, cancellation, and stable-document error preservation |
| Full backend suite | **1,637 passed, 34 skipped, 14 failed** |
| Ruff check | Passed over the full backend |
| Ruff format | 305 files already formatted |
| Mypy | No issues in 260 source files |
| Dependency audit | Exit 0; no known vulnerabilities reported for audited packages; the local `workflow-platform` package was skipped because it is not on PyPI |
| Source integrity | All **683 original regular files** unchanged in both extractions after the checks |

The reviewer probes deliberately assert the observed gap as well as repaired behavior. A passing reproduction means the gap was observed, not that it is acceptable.

### Runtime and optional checks

The frozen environment uses CPython **3.14.8**, SQLite **3.53.1**, Veracium **0.27.1**, and Playwright **1.63.0**. Veracium's actual `runtime_supported()` returned **False**; the result is recorded in `evidence/runtime.json`. Its gate was not altered or disabled.

The fourteen full-suite failures remain the unsupported-SQLite class in learned-memory tests and the real-store health test. The supplied qualified-runtime capture reports **1,652 passed, 33 skipped**. The difference is those fourteen failures plus the historical-git golden test skipped in the archive-only checkout. An independent full pass on the qualified runtime remains unavailable here.

Chromium's headless-shell installation was retried and failed on an invalid/truncated downloaded archive. The nine E5 browser tests and three associated controls were therefore not independently executed. The supplier reports them passing, including the two-match reproduction from the prior review; that remains supplied evidence. No disposable Postgres service or Docker executable was available. The input dependency lock is unchanged from round three.

The live evidence's 73 completed triage runs and four completed health-report runs were read as supplied records only. This review did not access production, a mailbox, AWS, or an actual model service.

## 5. Next acceptance checks

1. Make sanitized withholding survive an ordinary failure of the clearing operation, without claiming cleanup succeeded.
2. Make rejected screenshot disposal independent of that clearing operation's outcome.
3. Add the cleanup-failure regressions and their normal-cleanup contrasts through the model-facing path. Exercise a real interrupted or failed clearing navigation when Chromium is available.
4. Preserve the accepted error-rule, action-contract, and prior non-browser repairs. Keep browser exclusion and the recorded enablement conditions.
5. Re-capture the qualified-runtime suite and optional checks with the next package, retaining the manifest and controls.

The review is complete. This archive contains the report, runnable local probes, full independent logs, source excerpts, and identity/integrity records.
