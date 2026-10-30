# G30 capability review — round 6

**Date:** 2026-10-30 UTC  
**Package:** `g30-capability-review-r6-fdf6ed3.tar.gz`  
**Disposition:** Request changes — **one P2 finding**. R5-F1 is closed for the reported retry paths. The new universal refusal claim still fails at the HTTP-client error boundary.

This reviews the workflow application's G30 capability layer, not Veracium core, the harness, or Brain. The round-6 guide was read first. Application source was not edited. All 693 original regular files match the input archive in both the review tree and the restored controls tree.

## 1. Accepted fixes

The two landing refusals now omit the reached URL at their point of construction. The independent round-5 sequence was repeated with the retry's clear both succeeding and failing: the retry refuses without a second locator read, reports the appropriate cleanup status, and keeps the reached URL out of the next model request and stored step output. This closes **R5-F1**.

The earlier first-call handling remains correct in the exercised cases: a read result, read error, or action error followed by failed clearing is withheld without the page text or cleanup URL. Rejected screenshots preserve an existing accepted target and remove their pending capture; successful screenshots publish after the fence; error and cancellation remove pending captures and release the operation lock. Stable checked-document errors remain available.

Complete off-list redirects and the tested off-list downloads now produce bounded refusals without their source URLs. Retaining the agent's own rejected request URL is reasonable. The supplied tests cover that positive control.

## 2. R6-F1 — P2: an incomplete redirect response returns its off-list Location through Playwright's error log

**Primary location:** `backend/src/workflow_platform/connectors/browser/playwright_connector.py:554`, in `_fetch_checked`. The new sanitized redirect refusal at lines 562–568 is reached only after `request.get()` succeeds. `backend/src/workflow_platform/tools/browser.py:567` forwards every other exception's text into the tool result.

**Trigger:** an allowed endpoint sends a 302 with an off-list `Location`, declares a response body longer than it sends, then closes the connection. Playwright buffers the response body before completing `APIRequestContext.get`. It raises `Error: aborted` with a call log containing the response headers, including the complete off-list URL. The connector never gets a response object to inspect or sanitize.

This was reproduced with the **installed Playwright 1.63.0 HTTP client and a real loopback HTTP server**, not an invented exception. The page object is only an adapter supplying the real `context.request`; no Chromium executable is needed for this API client. The exact client boundary used by `fetch_url` runs normally.

| Response from the allowed endpoint | Tool outcome | Off-list URL in next model request / step output | Off-list request or saved download |
|---|---|---|---|
| Complete 302, zero-length body | Bounded `source=redirect` refusal | No / no | Neither |
| Same redirect destination, body truncated after one byte | Raw `APIRequestContext.get: aborted` call log | **Yes / yes** | Neither |
| Complete redirect to an allowed endpoint | Successful download with expected bytes | Not a denial case | Only the allowed download |

The failing tool result includes:

```text
browser_fetch_url failed: APIRequestContext.get: aborted
Call log:
  ...
  - ← 302 Found
    - location: http://localhost:<port>/OFFLIST_LOCATION_FIXTURE
    - content-length: 100
    - connection: close
```

The real workflow engine then delivers that marker to the next model turn and stores it in step output. The model itself is a deterministic fake; no model service was called. The request log contains only `GET /start`, and the downloads directory is empty. This is **diagnostic disclosure, not an off-list dispatch or DOM-read bypass**.

There is a related parsing exit in the same operation. A complete 302 with `Location: https://[SERVER_CHOSEN_HOST_FIXTURE]/download` makes Python's `urljoin()` raise a `ValueError` that quotes `SERVER_CHOSEN_HOST_FIXTURE`. That text also reaches the tool result before the bounded redirect refusal. This secondary case exposes the malformed authority text, not a successfully parsed full URL; the truncated-response case above establishes the full-URL finding independently.

**Why the existing tests miss it:** the redirect fixtures return a completed response, so the new conditional always gets to inspect `Location`. The formatted-name check sees only explicit `BrowserHostDenied` / `BrowserPageNotCleared` constructors. Neither Playwright's transport exception nor the URL parser's exception is constructed there. The check passes on the same tree where both disclosures reproduce.

**Required change:** under a host policy, handle transport and URL-parsing failures at the fetch boundary before arbitrary exception text can reach the tool. Expose a bounded reason and trusted error category; omit raw library call logs, response headers, and server-supplied parse text. The original requested URL may be retained under the agreed request-argument rule. Preserve the sanitized redirect denial and successful allowed-download behavior. Avoid chaining the original unsanitized exception into an exposed diagnostic.

Add the incomplete-response and malformed-Location cases, including a real-engine assertion that neither the next request nor step output carries the marker. Keep the complete-response twin and allowed-download positive control. This does not require Chromium. Audit other raw exception exits before extending a claim to every browser failure; the demonstrated fix should not depend on recognizing one particular error string.

The shipped system layer still excludes browser tools. This finding does not establish a production exposure in the reported deployment; it blocks acceptance of the broader G30 browser contract as stated.

## 3. Answers to the guide's questions

**The rule:** keeping newly encountered page/server URLs out of policy denials and unchecked navigation/transport diagnostics is a useful line. Echoing the URL already supplied as the tool argument does not introduce a new URL to the model. That is an information-flow distinction, not a declaration that an agent-supplied URL is trusted. Successful admitted document content and preserved checked-document diagnostics can contain URLs; the policy is not a general URL secrecy guarantee.

**The derivation:** the current test is a useful source-pattern regression check, not a proof of origin or of all error exits. It checks the spelling of interpolated expressions in two constructor names. It does not follow assignments, inspect concatenation or prebuilt messages, or cover exceptions from callees. An independent in-memory AST probe shows that all of these additions would pass its predicate:

```python
raise BrowserHostDenied("refused " + current)

url = current
raise BrowserHostDenied(f"refused {url}")

raise BrowserHostDenied(message)
```

These snippets were inspected as syntax only; they were not executed or written into application source. A stronger design centralizes bounded refusal construction behind typed fields and tests the externally visible tool/engine boundary with hostile inputs and failing dependencies. A static check can then enforce use of that construction path. Calling the present test a “derivation” should not imply dataflow analysis.

**Section 2:** the changed universal “no refusal names a URL a page or server chose” statement is still too broad because of R6-F1. The narrower repaired landing/retry behavior is supported. The existing qualifications about content origin, destination enforcement, action effects, action cancellation, and leaving-and-returning remain necessary. The screenshot claim concerns publication at the requested target; it is not isolation from arbitrary filesystem observers of the pending directory. No additional blocking defect was established in those unchanged contracts this round.

Keeping failed-clear page replacement as a browser-enablement condition is appropriate. The existing operation-start guard still prevents the independently tested retry read. Page retirement/recreation remains a recovery improvement; no automatic replay of a mutating action should accompany it.

## 4. Independent validation

| Check | Result |
|---|---|
| Input SHA-256 | `35445371a52a529e8bd53979a6b65084a5d36a9d464821ff3c5a0c8f4d00b59a` |
| Source/test aggregate, 260 Python files | `0e87388ae6172721791ba3a434fc900e8629182feea04dfefb696c6e89416ed8` — matches supplied manifest |
| Claim runner | **228 passing test results; 19 runnable claims reproduced** |
| Mutation controls, separate extraction | **65 fired; all 65 restored runs passed** |
| Independent review probes | **20 passed**: 13 regression checks, 6 real HTTP/tool/engine cases, 1 AST-check limitation probe |
| Full backend suite | **1,663 passed, 35 skipped, 14 failed** |
| Ruff / formatting | Passed; 305 files already formatted |
| Mypy | Passed; 260 source files |
| Dependency audit | No known vulnerabilities found; local `workflow-platform` package skipped because it is not on PyPI |
| Original archive file integrity | All 693 original regular files unchanged in each extraction |

The gap probes deliberately assert the observed disclosure. Their passing status means the finding reproduced, not that the behavior is acceptable. Logs, runnable tests, source excerpts, and identity records accompany this report.

### Runtime and evidence limits

The frozen environment is CPython **3.14.8**, SQLite **3.53.1**, Veracium **0.27.1**, and Playwright **1.63.0**. Veracium's `runtime_supported()` returned **False**; no qualification check was disabled. The fourteen full-suite failures remain the unsupported-SQLite failures in learned-memory and real-store health tests. The supplied qualified-runtime capture reports **1,678 passed, 34 skipped**. The difference is those fourteen failures plus the historical-git golden test skipped in this archive-only checkout. The dependency lock is unchanged from round five.

D3's one Postgres test and E5's ten Chromium tests were not run locally. Seven of the 72 controls were not run: one Postgres control and six Chromium controls. No disposable Postgres service or Docker executable was available. The Chromium installation was retried and failed with an invalid/truncated download. The supplied all-opt-in results — 239 test results and 72 controls — remain supplier evidence. The independent real HTTP-client tests above do not substitute for the Chromium navigation suite.

The supplied production record reports 88 completed triage runs, five completed health-report runs, and no browser tool in the deployed layer. Those records were read, not independently verified against production. No production system, mailbox, AWS operation, or actual model service was accessed.

## 5. Next acceptance checks

1. Withhold raw fetch transport and parsing diagnostics, including failures before a response object is returned.
2. Demonstrate URL/marker absence at both tool and engine boundaries for incomplete redirects and malformed Locations, with the successful and bounded-denial twins retained.
3. Preserve the accepted two-call retry repair, first-call cleanup handling, screenshot behavior, and earlier capability checks.
4. Describe the static test at its actual strength; reconcile the universal refusal wording with the observable error boundary.
5. Keep browser exclusion and the documented enablement conditions, and re-capture the qualified-runtime and optional checks.
