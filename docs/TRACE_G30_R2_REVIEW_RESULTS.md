# G30 capability review — round 2

**Reviewed:** `g30-capability-review-r2-5866f3d.tar.gz`  
**Date:** 2026-10-29  
**Disposition:** **Request changes for complete round-two signoff. The original service-default defect is corrected. Three P2 issues remain in the submitted package.**

The four original examples are addressed: the ordinary service now loads a system policy, sibling browser calls no longer replace an in-flight call's policy, runs receive separate download directories, and an ordinary denied upload is stopped. The revised tests and controls reproduce those repairs.

The remaining issues are a documented image-build command that still uses the old context, an upload type check that becomes stale while waiting for the new lock, and a top-level page check that does not cover navigation during an awaited read. The two browser findings require browser tools to be enabled; the shipped policy still excludes them. Neither establishes a problem in the documented live production runs.

## 1. Disposition of the original findings

| Round-one finding | Round-two ruling | Evidence and remaining qualification |
|---|---|---|
| R1 — service could run without a layer | **Runtime defect closed; deployment integration incomplete** | The shared loader returns the shipped layer when unset and refuses empty/unreadable values. Actual service tests pass. The image layout and Compose context are corrected, but the documented ECR build script is not; see R2-F1. |
| R2 — sibling replaces an in-flight host policy | **Specific defect closed** | The whole browser call holds the connector lock. The real parallel-engine and serial-twin tests pass; an independent cancellation probe also verifies the waiter cannot replace the active policy and can proceed after cancellation. This does not freeze the browser's own navigation; see R2-F3. |
| R3 — output directory shared across runs | **Closed for the default engine construction path** | The constructor now receives `<root>/<instance_id>`, and the two-run output-preservation test passes. Resume/retry retain the instance identity; a fork creates a new one. |
| R4 — uploads ignore `file_read` | **Original omission closed; directory-refusal class incomplete** | The denied, allowed, and preexisting-directory tests pass. A path that becomes a directory after the check still reaches Playwright as a directory; see R2-F2. |
| Content-origin overclaim | **Withdrawn as requested** | The guide now distinguishes the top-level page from the provenance of its subresources. The narrower top-level invariant still needs R2-F3 addressed. |

The P1 missing-layer fix should stand. There is no reason to roll it back because the browser work has remaining limitations.

## 2. Findings

### R2-F1 — P2: The documented image-build script still supplies the old build context

**Locations:** `infra/scripts/build_and_push.sh:24–25`; `backend/Dockerfile:25–33`; `infra/README.md:40`. Related test: `backend/tests/test_system_capabilities.py::test_the_image_carries_the_layer_where_the_service_looks_for_it`.

The Dockerfile now expects a repository-root context: it copies `backend/pyproject.toml`, `backend/src`, and `config/system_capabilities.yaml`. Compose was updated accordingly. The documented build-and-push script still runs:

```bash
docker build -t "$REPO_URL:$TAG" "$REPO_ROOT/backend"
```

That context cannot supply any of those three source paths. The first copy is sought under `<repo>/backend/backend/pyproject.toml`; the policy is sought under `<repo>/backend/config/`. Neither exists in this package.

**Independent check:** I executed the actual shell script with recording stubs for Terraform and Docker. The Docker stub recorded its arguments and stopped at the build command, before login or push. The captured context was the backend directory. All three required COPY sources were absent beneath it, while all were present beneath the repository root. Docker itself is unavailable here, so this is command execution plus path verification, not an independently completed container build.

**Impact:** The documented ECR image workflow cannot build the changed Dockerfile. It does not silently produce an unrestricted service; it fails before there is a new image. This is distinct from the already disclosed SQLite and unlocked-image limitations: it occurs before dependency installation or store opening.

**Required change:** Update the script and its diagnostic message to use both the repository-root context and the explicit Dockerfile:

```bash
docker build -f "$REPO_ROOT/backend/Dockerfile" -t "$REPO_URL:$TAG" "$REPO_ROOT"
```

Include every documented image-build entry point in the layout test. Checking only Compose and Dockerfile text missed this caller. Preserve the Dockerfile-specific allowlist when changing the context.

**Evidence:** `reviewer_probes.py::test_documented_image_script_uses_the_obsolete_build_context`; `evidence/reviewer-probes.log`, record `IMAGE_BUILD_CONTEXT`.

### R2-F2 — P2: A queued upload can become a directory after the directory check

**Locations:** `backend/src/workflow_platform/tools/browser.py:374–387`; `backend/src/workflow_platform/connectors/browser/playwright_connector.py:427–429`.

The upload tool checks the read policy and `Path(file_path).is_dir()` **before** awaiting the connector's operation lock. The directory check returns false for a nonexistent path. While the upload is queued, another permitted deterministic function can create that path as a directory. After the lock is acquired, the tool forwards the path without checking its type again.

This is not the disclosed symlink residual. The reproduction uses no symlink, rename, deletion, or replacement of an existing file.

**Independent reproduction:**

1. Hold the connector's operation lock to represent an in-progress browser call.
2. Queue an upload of a nonexistent `upload-target`. Its read policy permits exactly that path, not its descendants.
3. Once the upload has passed its checks and is waiting for the lock, execute the shipped `write_csv` function through the real filesystem to create `upload-target/fixture.csv`, under a policy that permits that write. Its parent directory is created by the stock filesystem implementation.
4. Release the lock.

The tool returns success. At the recording page boundary, the **actual installed Playwright input-file classifier** returns `localDirectory=<...>/upload-target`, with no file list. The upload step's policy reports the child file as unreadable. The twin with the directory already present at call entry is correctly refused and never reaches the page.

The tested boundary is classification and dispatch: no real browser upload or remote transfer was performed. Playwright's installed `resolve_paths_and_directory_for_input_files()` confirms that the later filesystem state is interpreted as a directory, exactly the behavior the new guard intends to prohibit.

**Impact and bounds:** E7's directory refusal is dependent on when the path appears. A queued request can become a directory upload without authorizing its contents individually. This requires browser upload to be enabled in a different reviewed policy; production's current tool list blocks it.

**Required change:** Reject missing and nonregular sources, perform source validation after acquiring the operation, and bind the uploaded bytes to the checked file. Do not pass a path whose type the browser library can reinterpret later as a directory. Merely moving `is_dir()` closer to dispatch reduces the obvious queue window but is not a complete guarantee if another writer can change the source before Playwright resolves it. If pathname-based handling is retained under a documented filesystem-mutation assumption, state that assumption explicitly and test it.

**Evidence:** `test_queued_upload_accepts_a_directory_created_after_its_check`; record `QUEUED_UPLOAD`. The probe uses the real deterministic writer and Playwright's real path classifier, with a recording page seam.

### R2-F3 — P2: The top-level host check is stale if navigation completes during a read

**Locations:** `backend/src/workflow_platform/connectors/browser/playwright_connector.py:258–261,359–367`; `backend/src/workflow_platform/tools/browser.py:129–134`; revised guide §2 / claim E1.

`read_text()` checks the page's URL, awaits the locator's `inner_text()`, and returns the result immediately. It does not recheck the page or bind the read to the checked document. `read_table()` has the same ordering. Holding the connector lock prevents a sibling tool call from changing the policy; it does not stop a page-initiated navigation or a previously initiated navigation from completing.

**Independent state-transition reproduction:** A recording page starts at `allowed.example`. After the real tool/connector completes `_guard()` and begins its awaited locator read, the page is changed to `off-list.example/landing`. The locator then returns `OFF_LIST_PAGE_FIXTURE`. The tool returns that content successfully, and the page is not cleared. The twin with the off-list URL present at call entry is correctly refused and cleared.

**Browser reachability and evidence limit:** This is a deterministic connector-level reproduction, not a live Chromium reproduction. A realistic sequence is an allowed page with a missing `#late` element, followed by a delayed navigation to an allowed first hop that redirects to an off-list page containing `#late`. The package already discloses that navigation redirects can reach such a page. The installed Playwright implementation of `innerText` uses a selector polling/retry operation; it is not inherently pinned to the document the application checked. Source evidence for that behavior is included. The exact live sequence still needs execution when Chromium is available.

**Why the revised residual does not cover this:** The guide discusses navigation **between** browser calls and says the next call checks the resulting URL. This case changes the top-level page **during** an active call, after its check but before the content is delivered. It is also different from intentionally permitting foreign images/scripts within an allowed top-level page. The top-level page itself is now off-list.

**Required change:** For reads, bind the operation/result to the document or navigation generation that was checked, and withhold the result if that identity changes or the page is no longer allowed. A final URL check closes this simple example but does not prove that an intermediate off-list document was never read. Actions that change the page require their own navigation-aware handling; checking after an effect cannot undo it. Keep browser exclusion until destination enforcement and these action/read semantics are established.

Add a real-browser regression using the delayed allowed-hop/off-list-landing sequence, alongside a stable allowed-page positive control. If continuous validity is not intended, narrow the public contract to the precise checks actually performed rather than claiming the agent acts only while the top-level page is allowed.

**Scope:** The shipped policy keeps this dormant. The demonstrated failure is the connector's validation ordering; live browser timing remains explicitly unverified here.

**Evidence:** `test_top_level_change_during_read_is_returned_without_recheck`; record `TOP_LEVEL_DURING_READ`; Playwright source excerpts in `evidence/source-excerpts.md`.

## 3. Answers to the round-two design questions

### Is one connector lock across a whole tool call the right ownership?

Yes, for serializing calls that intentionally share one page and session. The lock starts before policy installation and spans the awaited connector action. All eleven tool-to-connector action calls inspected are within that operation. The revised real-engine test confirms the original policy-overwrite defect is fixed. An independent event-controlled probe also confirms that a waiting operation cannot install its policy early and that cancellation releases the lock.

The guide correctly explains why the old probe's “wait until the sibling finishes” handshake cannot be used unchanged: serialization makes that handshake circular. The revised bounded response hold is reasonable, and its control fails when the lock is removed. For a more deterministic future test, explicitly observe the sibling reaching the lock and then release the first response, without requiring the sibling operation to finish first.

Two boundaries remain:

- Serialization of individual tool calls does not make a whole multi-call step sequence exclusive. Steps still share cookies, page state, and navigation history. That is an intentional session-sharing choice, not an additional finding in this review.
- The lock protects application callers, not autonomous page state or unrelated filesystem writers. R2-F2 and R2-F3 demonstrate why those state lifetimes need their own handling.

Retaining the last policy between calls is coherent with the stated restricted scope while browsers remain excluded. A connector per step would provide stronger page-state separation, but would change the session-sharing behavior. It is not necessary merely to repair the original callback overwrite.

### Is “unset means the shipped layer” an acceptable service default?

Yes. It is closed with respect to missing configuration because the fallback is a concrete strictly loaded policy and inability to read it raises. The empty/whitespace variable is separately refused. This preserves documented direct service commands without restoring unrestricted operation.

The runtime behavior is now backed by real application construction and a real-file denial test. The source construction test also checks the value is the shared loader, improving on round one's keyword-presence check. R2-F1 is a deployment integration correction, not a reason to replace this loader design.

### Does §2 still exceed what is shown?

E1 exceeds the observed ordering when the top-level page changes during a call. E7 exceeds the directory check's lifetime when an upload waits for the lock. The image layout is plausible and its Compose path is fixed, but the broader deployment-completeness claim must include the build script.

The output-token scope and the withdrawal of a content-origin guarantee are now stated appropriately. Browser exclusion, lexical-path/symlink limitations, operator-controlled trigger I/O, author-set aggregate budgets, and the remaining scaffold approval gap should remain explicit.

There is also a small stale comment at `security/capabilities.py:164–165` describing an unset variable as no layer. It should be updated to match the new loader. This is documentation cleanup, not another finding.

## 4. Verification

The round-two guide was read before source inspection. The previous F1 guide is byte-identical to the copy read in the prior round; its historical scope and counts were not substituted for the current G30 guide.

| Check | Independently observed result |
|---|---|
| Input archive SHA-256 | `b9c03512ae6e7f8a91ce2186cefe2b60c4af374682b79a5124e8d16c3db991be` |
| Source/tests aggregate SHA-256 | `3a8bb5025427b4493bba7c433d75f1826adbd810043748346234b80c7509860a`, matching the supplied manifest |
| Claim runner | **150 passed** across all 18 runnable claims; D3 and E5 not run |
| Control runner | **40 fired and passed after restoration**; 2 optional controls not run |
| Reviewer probes | **4 passed**: one repair/cancellation check and three observed-gap probes with contrasts |
| Full backend suite, `DATABASE_URL` unset | **1,586 passed, 30 skipped, 14 failed** |
| Ruff check | Passed |
| Ruff format | 260 files already formatted |
| Mypy | No issues in 260 source files |
| Source integrity | All **673 original regular files** unchanged in the review tree and in the separate control tree |

The reviewer probes deliberately assert the current observed gaps; their passing status is evidence of reproduction, not approval of those behaviors. No application source was edited.

### Runtime and optional-service limitations

The frozen lock installed successfully from the available cache using CPython **3.14.8**, Veracium **0.27.1**, and Playwright **1.63.0**. This Python has built-in SQLite **3.53.1**. The available system Python has SQLite 3.45.1 but is Python 3.12, which cannot run this Python-3.14 project unchanged. A compatible Python 3.14 linked to the qualified SQLite was not available.

All fourteen regression failures again originate from Veracium's `unsupported-sqlite` refusal or assertions downstream of that refusal, in learned-memory tests and the real-store health test. The supplied result of **1,601 passed, 29 skipped** differs by those fourteen failures plus the historical-git golden test skipped in this archive-only checkout. This agrees with the guide's explanation; the qualification gate was not disabled or altered.

For future environment preflight, printing `sqlite3.sqlite_version` is useful but not sufficient by itself: Veracium's `runtime_supported()` compares its recorded build identity and constructor evidence. Run the actual qualification check as well as printing the version.

Chromium installation was retried and again failed because the downloaded archive was invalid/truncated. Consequently E5 and its control were not independently executed. No disposable Postgres service was available, so D3 and its control were not run. The supplier's R2 capture contains both optional suites; that is supplied evidence, not an independent run here. The G29 withdrawal source is unchanged by this round, and no new G29 concern was identified.

Docker was not available. The image-layout test and actual script invocation with recording stubs were executed; a real container build was not. The guide explicitly acknowledges that its successful image smoke run is absent from the capture. I have not treated that assertion as independently verified.

The production activity counts in `LIVE_EVIDENCE_G30_R2.txt` were read as supplied evidence only. This review did not contact production, a mailbox, AWS, or any other external application.

## 5. Next acceptance checks

1. Correct and test the documented ECR build command against the root-context Dockerfile, alongside Compose.
2. Preserve the upload source's checked identity/type across queueing and dispatch; add the nonexistent-path-to-directory transition and allowed-file control.
3. Establish and test the lifetime of the top-level-page check through awaited reads and actions. Run the delayed-navigation regression with real Chromium before making the stronger E1 assertion.
4. Retain the shipped exclusion of all browser tools. The original policy-overwrite repair is sound for its intended synchronization purpose, but does not establish all browser guarantees.
5. Capture the optional browser/Postgres checks and a full qualified-runtime suite again with the next package. Keep the input manifest and detector controls.

This completes the round-two review. The archive includes all independent logs, runnable probes, line-numbered source evidence, input identity, and integrity checks.
