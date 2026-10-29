# G30 capability-layer review

**Date:** 2026-10-29  
**Package:** `g30-capability-review-a7284c0.tar.gz`  
**Disposition:** **Request changes to the G30 contracts and implementation as submitted.**

The deterministic file checks, layer intersection, and three named model-call clamps passed the available claim tests. The additional review found **four issues: one P1 startup-coverage defect and three P2 browser defects**. The browser issues remain dormant under the shipped system policy, which excludes all browser tools; they are not evidence that the currently documented production service accessed an unlisted destination. They do contradict parts of the implementation being submitted for review and must be included in the browser-enablement gate.

The highest-priority issue is that a supported service deployment can still start without a system layer. Separately, browser step policies share mutable connector state, downloads are stored in a common directory across runs, and browser uploads do not check the file-read policy.

## 1. Findings

### G30-R1 — P1: Supported service launch paths still run without the outermost layer

**Locations:** `backend/src/workflow_platform/security/capabilities.py:227–232`; `backend/src/workflow_platform/main.py:148`; `docker-compose.yml:18–32`; `infra/ecs.tf:34–38`; `backend/Dockerfile:20–29`. The coverage test is `backend/tests/test_system_capabilities.py:401–423`.

**Contract affected:** §2's assertion that every workflow run by the service or an operator script has a system layer, and claim A2.

The operator helper falls back to the shipped policy. The service helper does not: `system_capabilities_from_env()` returns `None` when its variable is absent or empty. The default service builder passes that return value directly to `WorkflowEngine`. This is not limited to a deliberately unrestricted unit-test engine:

- The tracked Docker Compose backend sets database, model mode, and region, but no capability-policy variable.
- The tracked ECS task definition likewise does not set it.
- The Docker image copies the backend project and source, but does not include the repository-level `config/system_capabilities.yaml`.
- The README's direct service command also omits the variable.

The new `run-local-be.sh` export protects that launch path. It does not protect these other supported paths. Health reports the absence but deliberately does not treat it as degraded.

**Independent reproduction:** The probe constructed the real service application with the variable unset, replaced only model responses with a fake, retained the real filesystem, and ran an agent step that read a synthetic local file. Health returned HTTP 200 and `{"status":"absent"}` for the system layer; the step returned the file's text. Repeating with the shipped layer configured returned the tool-denial result instead. No product source was changed.

**Why the existing detector misses it:** A2's source check proves that a `system_capabilities=` keyword exists, not that its value is non-null. Its service value is a function expressly capable of returning `None`. The launcher test examines just one shell script. A1 also expressly tests that an unset variable means no layer, which conflicts with the broader guide contract.

**Required change:** Make the normal service startup load a packaged policy or refuse to start without one. If unrestricted development is necessary, make it a distinct explicit mode. Include the policy in container packaging and wire all supported deployment manifests. Exercise actual service construction with the environment absent, empty, valid, and unreadable; do not use keyword-presence as the acceptance condition.

**Scope:** This establishes a source/deployment-coverage failure. The supplied production capture reports a configured layer, and this review did not access production. It does not establish that the currently running service has lost its layer.

**Evidence:** `reviewer_probes.py::test_service_default_can_run_without_the_system_layer`; `evidence/reviewer-probes.log`, record `SERVICE_DEFAULT`.

### G30-R2 — P2: A parallel browser step replaces another step's in-flight host policy

**Locations:** `backend/src/workflow_platform/tools/browser.py:31–47`; `backend/src/workflow_platform/engine/executor.py:971–975,1549–1555`; `backend/src/workflow_platform/connectors/browser/playwright_connector.py:409–424`.

**Contracts affected:** Per-step narrowing and E1's checked redirect hops. This is the same state-sharing problem fixed for deterministic step contexts, recurring at the browser connector.

Each browser tool obtains the run's shared connector and changes its `_host_allowed` callback. Copying `context.connectors` copies the dictionary, not its connector object. Parallel steps therefore share the callback. `_fetch_checked()` awaits each response and then consults `self.check_url()` again, which reads whichever callback the most recent sibling installed.

**Independent reproduction:** Both the direct tool probe and a real parallel-DAG engine run used two steps:

1. The system layer admitted `narrow.example` and `wide.example`; the first step narrowed itself to `narrow.example`.
2. Its allowed initial fetch was suspended while awaiting a response.
3. A sibling with the broader effective policy performed a browser text read. That call changed the shared callback.
4. The first response then supplied a redirect to `wide.example`.

The narrow step followed that redirect and saved `ENGINE_RACE_FIXTURE`, and the engine reported completion. The serial twin, without the sibling policy change, refused the redirect before the second request. The model and transport were fakes with event-controlled scheduling; the engine, tool dispatch, policy resolution, connector logic, and filesystem write were the shipped implementations.

**Impact and bounds:** The step's own restriction is lost. This reproduction does not widen beyond the shared system/workflow bounds. It requires browser tools to be enabled in an alternative policy; the shipped production policy prevents it today.

**Required change:** Keep an immutable effective policy associated with the operation. For a shared page, serialize the complete policy-install/action/landing-check operation, including route callbacks and redirects, or provide isolated connector/page state where different policies can run concurrently. Merely copying the callback at one redirect check will not resolve all of the shared-page interactions. Add a real parallel-engine test, including a serial refusal twin.

**Evidence:** `test_sibling_tool_call_replaces_inflight_host_policy` and `test_host_policy_race_through_parallel_engine_steps`; log records `HOST_POLICY_RACE` and `ENGINE_HOST_POLICY_RACE`.

### G30-R3 — P2: Browser output containment uses a directory shared by every run

**Locations:** `backend/src/workflow_platform/engine/executor.py:206–212,781–788`; `backend/src/workflow_platform/connectors/browser/playwright_connector.py:187–206,393–401`.

**Contract affected:** §2 and E3 state that browser outputs stay in the run's own downloads directory. The connector's containment check assumes the engine supplied such a directory.

The default connector factory receives `self.browser_downloads_dir` unchanged. The default is the process-local `./downloads`; neither the instance ID nor an organization/run-specific child directory is used. The connector is created per run, but its output directory is not.

**Independent reproduction:** Two sequential runs through the same real engine fetched different synthetic bodies under `dest_filename="report.txt"`. Their distinct instance IDs produced exactly the same local output path. At the first run's completion it held `FIRST_RUN`; after the second run it held `SECOND_RUN`. The first run's recorded artifact path now referred to the second run's bytes. The probe injected only a fake Playwright page at the default constructor boundary, preserving the engine's directory argument and the connector's actual writes.

**Why the existing tests pass:** They instantiate a connector with an already-isolated temporary directory and prove containment relative to that supplied directory. They do not test whether engine construction supplies a different directory for each run.

**Required change:** Allocate the directory from an immutable run identity beneath the configured root, and pass it to the connector. Define retry/resume ownership deliberately. Test sequential and concurrent runs using identical server-suggested or agent-selected names; assert distinct paths and preservation of both outputs. Continue testing path containment within each allocated directory.

**Scope:** Dormant with the shipped no-browser policy. This is an artifact-isolation and integrity defect, not an observed production incident.

**Evidence:** `test_browser_outputs_share_one_directory_across_real_engine_runs`; log record `SHARED_DOWNLOADS`.

### G30-R4 — P2: Browser file uploads ignore `file_read`

**Locations:** `backend/src/workflow_platform/tools/browser.py:347–363`; `backend/src/workflow_platform/connectors/browser/playwright_connector.py:427–429`.

`BrowserUploadFileTool` validates that the parameter is a nonempty string and forwards it. The connector checks the page's host and calls Playwright's `set_input_files(file_path)`. Neither consults `context.capabilities.can_read(file_path)`.

**Independent reproduction:** With the browser upload tool allowed, an allowed page host, and `file_read=[]`, `can_read()` returned false for an existing synthetic fixture. The actual tool/connector still invoked the page's `set_input_files` boundary with that exact path and returned success. The page was a recording fake: this proves dispatch past the missing check, not an actual remote transfer.

**Impact and bounds:** Once enabled, the upload tool provides a local-file input path outside the read policy. An allowed destination does not authorize access to every local source file. The documented exception for browser-generated output scratch files does not cover reading an upload source. This is an additional browser-enablement prerequisite, not a failure of the seven deterministic functions covered by C2/C3. The shipped tool allowlist prevents it today.

**Required change:** Apply the resolved read policy before Playwright sees the path, using the same path-validation rules as other file inputs. Define whether directory uploads are supported; if they are, apply the policy to each file actually enumerated. Add denied and allowed twins at the tool-dispatch boundary.

**Evidence:** `test_browser_upload_does_not_consult_file_read`; log record `UPLOAD_FILE_READ`.

## 2. Answers to the guide's review questions

### Is the system layer the outermost bound on everything that runs a workflow?

Not as stated. R1 demonstrates a normal service construction without it, and the Compose/ECS paths omit the configuration. The operator scripts inspected use the new mandatory-loading helper. With the shipped layer explicitly installed, the available claim tests confirm the intersection and deterministic file checks; I did not find an additional failing case in those paths.

The source-derived checks are useful regression detectors but are not completeness proofs. A2's keyword test accepts a null value. C3 traces the current deterministic functions' filesystem calls; it does not cover browser upload input or engine allocation of browser directories. These populations need to be named separately.

### Is the token-cap scope appropriate?

The implementation matches the explicitly stated division: agent steps take the minimum of system/workflow/step limits; question suggestion and memory distillation take the system cap. The ordinary, restrictive, and uncapped twins passed. The learned-memory callback keeps its per-observation cap within the service's observation lock.

That is a defensible product choice if `max_tokens_per_call` on a workflow or step is described as an agent-execution setting. It should not be presented as bounding every model call attributable to that workflow. If that broader meaning is desired, post-run suggestion and observation should inherit the workflow's cap too. This is a scope decision, not another defect under this guide's express contract. The cap remains an output-token ceiling, not a total-cost ceiling. The fixed scaffold call is separately outside the guide's three workflow-driven call categories.

### Are the stated residuals sufficiently bounded?

- **Browser exclusion:** Keep it. R2–R4 add prerequisites beyond destination enforcement. Passing the future destination gate alone would not resolve them.
- **Content wording:** The statement that nothing a disallowed host returns reaches the agent is broader than this design establishes. Non-navigation subresources deliberately pass at `playwright_connector.py:218–234`. An allowed page can render a foreign image or incorporate a foreign script's text while its own URL stays allowed; the landing check establishes the top-level URL, not the provenance of everything rendered. This is a source/design inference, not a new live-browser reproduction. Narrow the claim or add provenance-aware enforcement and appropriate tests. Do not equate a top-level host check with a complete content-origin guarantee.
- **Symlinks:** The matcher deliberately authorizes lexical paths, not resolved targets. I found no stock function here that creates symlinks, consistent with the disclosure. That does not independently verify the supplied statement about the live trees. Keep this assumption explicit and revisit it whenever another writer can modify those trees.
- **Triggers:** The inspected startup registration path consumes operator-loaded definitions; API persistence alone does not register their triggers. The operator-config exception remains relevant because trigger I/O is outside these step checks.
- **Spend and scaffold approval:** The disclosed author-controlled budgets and approval limitations remain. A capability layer limits configured operations; it does not validate every other drafted field or impose an aggregate platform spend ceiling.

## 3. G29 closure continuity

The feature remains withdrawn in the shipped definition validator: non-null `released_fields` raises at `workflow/definition.py:312–319`. The G29 tests included in the full run passed for the available model/YAML/API import, scaffold, clone, and persistence-site coverage. Postgres `get` and `list_all` both reconstruct through `WorkflowDefinition.model_validate` (`persistence/postgres.py:100,109`).

The actual Postgres stored-row test D3 was not run because no disposable Postgres service was available. The source and supplied evidence support the intended remediation, but I am not substituting source inspection for independent execution of D3. G29 stays **closed by withdrawal**, not reopened or approved.

## 4. Verification and limits

`docs/TRACE_F1_REVIEW_GUIDE.md` was read in full first, followed by the current `docs/G30_CAPABILITY_REVIEW_GUIDE.md`. The latter supplies this round's scope; historical F1 counts and old dependency versions were not treated as the current environment specification.

| Check | Independent result |
|---|---|
| Input archive SHA-256 | `6493d57c42f2666f1c367cdc39c458c7c94b35078df7752c3c66f13b59c76875` |
| Source/tests aggregate | `cd32bf7e0a6029a6d1121679f17434032b8c95498c050c2b5ba5979e5f7e8415`, matching the supplied 260-file manifest |
| Prescribed claim runner | **135 passed**, covering all 16 runnable claims; D3 and E5 explicitly not run |
| Prescribed control runner | **33 controls fired and passed after restoration**; 2 optional controls not run |
| Reviewer probes | **5 passed**, asserting the four observed defects; includes serial/parallel twins and a real parallel engine reproduction |
| Full backend suite, `DATABASE_URL` unset | **1,569 passed, 30 skipped, 14 failed** |
| Ruff check | Passed |
| Ruff format check | 260 files already formatted |
| Mypy | No issues in 260 source files |
| Source integrity after tests | All **667 original regular files** unchanged in both the review tree and the separate control tree |

The full-suite failures are in learned-memory operations and the real-store health test: the installed locked environment's SQLite **3.53.1** is rejected by Veracium as an unqualified runtime identity. The failed observation/recall assertions follow that refusal. This review did not disable qualification checks or rewrite dependency behavior to obtain a passing suite. The supplied gate result is 1,584 passed and 29 skipped; the 14 runtime-related failures and the additional skipped historical-git golden test account for the difference.

The working environment was CPython **3.14.8**, the package's frozen lock, and **Veracium 0.27.1**. Dependencies were installed from the available cache. The initial automatically selected 3.15 environment did not install successfully; the final tests used the explicit supported 3.14 interpreter in an environment outside the source tree.

Chromium was unavailable. Installation was attempted and failed with a non-zip/truncated download; see `evidence/browser-install.log`. Thus the six optional claim results comprising D3 and E5, and their two controls, were not independently executed. Browser findings use the shipped connector logic with recording or synchronized transport/page doubles, with that limitation stated in each finding. No real mailbox, external model call, or production service was used.

The live counts and deployment assertions in `LIVE_EVIDENCE_G30.txt` are **supplier-provided evidence**. This review read the capture but did not rerun its production queries or certify the live state.

## 5. Acceptance conditions for the next package

1. Close R1 with runtime startup tests and complete deployment packaging/configuration, or explicitly withdraw unsupported service launch paths and ensure they cannot silently execute unrestricted workflows.
2. Close R2 with policy ownership that remains correct through all awaits and route callbacks, demonstrated through the real parallel engine.
3. Close R3 with instance-bound output directories and two-run overwrite/isolation tests.
4. Close R4 with upload-source authorization before browser dispatch, including an allowed control.
5. Retain browser exclusion until those repairs, pre-dispatch destination enforcement, and accurately bounded content claims are all addressed.
6. Run D3/E5 and their controls in the next evidence capture, and rerun the full suite on the qualified SQLite runtime. Preserve the existing claim-to-test and test-to-control mapping.

The archive includes the runnable reviewer probes, unabridged test logs, source excerpts with line numbers, input identity, and post-run integrity checks. The review does not modify the supplied application.
