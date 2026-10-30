# G30 capability review — round 7

**Date:** 2026-10-30 UTC  
**Package:** `g30-capability-review-r7-209955f.tar.gz`  
**Disposition:** **Accept the round-7 changes within this review's scope. No new blocking findings. R6-F1 is closed.**

The common tool error boundary and the connector's fetch boundary address the round-6 finding, and the independently exercised earlier repairs remain intact. This acceptance does not mark the unavailable optional checks as passed or satisfy the separate browser-enablement conditions.

This review concerns the workflow application's G30 capability layer, not Veracium core, the harness, or Brain. The round-7 guide was read first. Application source was not edited. Controls ran in a separate extraction, with their mutations restored.

## 1. Closure of R6-F1

The original round-6 HTTP reproduction was rerun with the installed Playwright **1.63.0** client and a real loopback server. These tests require no Chromium executable. Only the page's context adapter and model responses are test doubles; response acquisition, parsing, the connector, the tool, and the workflow engine use their actual implementations.

| Case | Independently observed result |
|---|---|
| Truncated 302 with an off-list Location | Bounded `BrowserCallFailed`; neither Location nor native call log reaches the tool result |
| Complete 302 with malformed bracketed authority | Bounded `source=redirect` refusal; malformed authority text omitted |
| Complete off-list redirect | Bounded refusal retained |
| Complete redirect to an allowed endpoint | Expected download bytes saved successfully |
| Connector called directly for truncated or malformed response | Bounded exception with neither original message nor exception cause/context attached |
| Real engine for truncated, malformed, and complete off-list responses | Marker absent from the next model request and stored step output |

All refused cases made only the initial allowed request and saved no download. The successful twin requested only the allowed follow-up endpoint. The loopback tests used no credentials, external endpoint, or actual model service.

The implementation now handles the failure before a response object exists: `_fetched_body()` reduces unexpected acquisition/body errors, while `_fetch_checked()` handles malformed redirect resolution without returning the parser's text. `url_host_allowed()` also returns False for the tested unparseable authorities without invoking the host predicate.

The common `_error()` function supplies a second boundary. All eleven browser tool paths were independently exercised with an unanticipated connector exception. Under a host restriction, each returned the class-only status and omitted the fixture text. Without a restriction, each preserved the original diagnostic. This confirms the default across the tools, beyond the submitted navigation example.

Relevant source locations:

- `backend/src/workflow_platform/tools/browser.py:63`: common exception-to-tool-result boundary.
- `backend/src/workflow_platform/connectors/browser/playwright_connector.py:550`: connector fetch error boundary.
- `backend/src/workflow_platform/connectors/browser/playwright_connector.py:589`: malformed redirect handling.
- `backend/src/workflow_platform/connectors/browser/base.py:102`: URL parser failure becomes a refusal result.

## 2. Earlier behavior retained

The independent regression set confirms the prior two-call repair with both retry-clear outcomes: the retry performs no second locator read, reports cleanup correctly, and does not deliver the previously withheld URL to the model or stored output.

The first-call failed-clear cases still withhold the read result, read error, or action error. Rejected screenshots preserve an existing accepted target and remove the pending capture; successful screenshots publish after the fence; error and cancellation remove pending captures and release the operation lock.

The new error marking preserves checked-document diagnostics. Separate read and action probes exercise three cases: unchanged document, a move off-list, and leaving then returning. Only the unchanged case passes the original diagnostic. Both navigation cases withhold it, including the return to the original URL. These are staged page-event tests, not new evidence about Chromium event timing.

The three static counterexamples from round six — concatenation, rebinding `url`, and a prebuilt message — are now rejected by the strengthened source-pattern check. Its description as a tripwire, rather than dataflow analysis, is appropriate.

## 3. Answers to the guide's questions

**The default and its location.** “Application-built or vouched, otherwise class-only” is an appropriate default for this boundary. The tool layer is where arbitrary exceptions become agent-visible results, so it is the right common enforcement point. It uses the current tool context's host policy, not the connector's potentially later policy. Keeping the fetch-specific handling below it also gives direct connector callers a bounded result for the demonstrated failure.

`BrowserBoundedError` is a trust assertion made by application code, not a sanitizer. Its constructors must continue to use controlled fields. The runtime boundary does not repair an application-created bounded error that has accidentally been given arbitrary server text. The current construction sites inspected here respect that obligation. The source-pattern checks are useful maintenance checks, with the limitations now acknowledged.

**Vouching and useful diagnostics.** The inspected calls to `vouch_checked_page_error()` occur on the unchanged-document exception paths in `_fenced()` and `_acted()`. That preserves the selector diagnostics the agent needs while withholding changed-document errors. No additional class of raw third-party exception was demonstrated to require an exception to the new default.

The documented loss of ordinary navigation details is a reasonable tradeoff for this review. If more recovery information is needed later, add application-owned categories or validated structured fields. For example, known local filename validation failures can have bounded messages rather than exposing arbitrary library text. Do not broadly exempt all `ValueError`, Playwright `Error`, or transport exceptions merely for usability. The existing upload-source refusals already preserve useful application-built explanations.

The marker is an internal assertion on an exception object. Keep marking restricted to the fence and avoid reusing marked exception objects for unrelated operations. No reachable misuse of that mechanism was established in the submitted implementation.

**Section 2 wording — one nonblocking clarification.** The operational rule is now supported by the exercised error paths. The explanatory phrase “no error text reaches the agent from a page or server it did not check” can still be read as an origin claim, despite the explicit content-origin disclaimer immediately afterward. An unchanged allowed page can contain or quote material from other origins, and an application-created bounded error is trusted by construction.

A more exact formulation is:

> Under a host policy, browser tools return arbitrary exception text only when the document fence vouches for it; otherwise they return application-built bounded errors or a class-only failure. This does not establish the origin of content quoted by an admitted document.

Similarly, the guide's “what a tripwire cannot see, the boundary withholds anyway” should be understood as applying to **unvouched, unbounded exceptions**, not to every possible future misuse of a trusted error type. These are wording and maintenance clarifications, not a newly reproduced implementation defect or a reason to reopen R6-F1.

## 4. Independent validation

| Check | Result |
|---|---|
| Input SHA-256 | `7a51402a34789cc24ae739dbd69c971d2f31c9e08829b3f0ab443c02c6456678` |
| Source/test aggregate, 261 Python files | `826c8dabf6bc4b02ab5f260bb19328bf136e8f6dcb7420265100eb5f07f960be` — matches supplied manifest |
| Claim runner | **243 passing test results; 19 runnable claims reproduced** |
| Mutation controls | **72 fired; all 72 restored runs passed** |
| Independent review probes | **56 passed** |
| Full backend suite | **1,678 passed, 35 skipped, 14 failed** |
| Ruff / formatting | Passed; 306 files already formatted |
| Mypy | Passed; 261 source files |
| Dependency audit | No known vulnerabilities found; local `workflow-platform` package skipped because it is not on PyPI |
| Original archive file integrity | All 699 original regular files unchanged in each extraction |

The 56 independent checks comprise 13 earlier repair regressions, nine real HTTP/connector/tool/engine cases, 22 unexpected-error checks covering all eleven tools with and without a restriction, six fence/diagnostic cases, three unparseable-host cases, and three static-check counterexamples. Unlike the round-6 defect probes, these assert the repaired behavior.

### Runtime and evidence limits

The frozen environment is CPython **3.14.8**, SQLite **3.53.1**, Veracium **0.27.1**, and Playwright **1.63.0**. Veracium's `runtime_supported()` returned **False**; no qualification check was disabled. The fourteen full-suite failures remain the unsupported-SQLite failures in learned-memory and real-store health tests. The supplied qualified-runtime capture reports **1,693 passed, 34 skipped**. The difference is those fourteen failures plus the historical-git golden test skipped in this archive-only checkout. The dependency lock is unchanged from round six.

D3's one Postgres test and E5's ten Chromium tests were not run locally. Seven of the 79 controls were not run: one Postgres control and six Chromium controls. No disposable Postgres service or Docker executable was available. The Chromium installation was retried and again failed on an invalid/truncated download. The supplied all-opt-in results — 254 test results and 79 controls — remain supplier evidence. The real HTTP-client tests do not replace Chromium navigation tests.

The supplied production record reports 92 completed triage runs, six completed health-report runs, and no browser tool in the deployed layer. Those records were read, not independently verified against production. No production system, mailbox, AWS operation, or actual model service was accessed.

## 5. Remaining gates

No additional blocking G30 error-handling change is requested on the evidence in this round. Keep browser exclusion and the existing enablement conditions: destination enforcement, action-effect decisions, the navigation-event matrix, and failed-clear recovery. The known container/runtime and unlocked-image-build limitations also remain unchanged.

The review archive includes runnable independent probes, complete check logs, source excerpts, and file-integrity records. Acceptance of these changes is separate from a claim that all environments or future connector implementations have been validated.
