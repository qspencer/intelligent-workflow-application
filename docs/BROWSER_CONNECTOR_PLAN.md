# Browser connector — design + build plan

Forward-looking plan for adding browser automation as a platform
capability. Playwright is the initial backend; the abstraction is
shaped so Selenium / Chrome-DevTools-direct can slot in without
workflow changes.

This is plan, not status. No browser connector exists in the codebase
today; the prototype's `web_automation` action was marked
"Reference, don't lift" in `docs/ARCHITECTURE.md`. This doc exists
so that when a workload pulls for it, the design isn't
re-derived.

## Why a browser connector — and why now might be the right time

`docs/USE_CASES.md` and `docs/INTEGRATIONS.md` don't list a browser
connector. The HTTP-level `WebhookConnector` covers documented APIs.
That's been enough for the four workloads built so far (PR triage,
paper triage, email triage, invoice extraction).

What it *doesn't* cover, surfaced by the RPA Challenge OCR site
(`rpachallengeocr.azurewebsites.net`):

- Pages where the data of interest is rendered by JavaScript (the
  challenge's table is literally `<table><tr></tr></table>` in the
  HTML source — jQuery DataTables populates it client-side after a
  fetch we'd have to reverse-engineer).
- Workflows that have to click a button, wait for a file download, or
  fill a form-based upload — i.e., the action surface is the rendered
  page, not a documented endpoint.
- Vendor portals, SaaS dashboards, legacy enterprise UIs, and
  government / form-only systems where the only way in is the
  rendered UI.

The goal isn't this one challenge — it's the **class of workloads
that the agent can interact with arbitrary webpages at all stages**.
The RPA challenge is the first validation workload, the same way
`examples/email_triage/` was the first validation for the email
connector.

## Architecture

### Layering

Three layers stack on top of the existing `Connector` ABC:

```
Connector (existing ABC — workflow_platform.connectors.base)
  │
  └── BrowserConnector (new ABC — adds browser-specific methods + types)
        │
        ├── PlaywrightConnector       (Phase 1 — Chromium / Firefox / WebKit via Playwright)
        ├── SeleniumConnector         (Phase 2 — fallback for environments where Playwright won't install)
        └── CdpConnector              (Phase 3 — direct Chrome DevTools Protocol, for headless-detection-sensitive sites)
```

For Phase 1 we ship `PlaywrightConnector` only. Its three-engine
support (Chromium / Firefox / WebKit) covers the "different browser"
need under one backend. The ABC layer lets Selenium / CDP slot in
later without workflow YAML changes.

### Lifecycle: per-workflow-run

Distinct from the email connector's process-lifetime sharing. A
`BrowserConnector` instance holds a Playwright `Browser`,
`BrowserContext`, and `Page` for **the duration of one workflow run**.

| Concern | Email connector | Browser connector |
|---|---|---|
| Constructed at | Process start (`main.py` bootstrap) | Workflow-run start (engine lazy-builds when a `browser_*` tool appears in any agentic step's `tools` list) |
| Shared across runs | Yes — one Gmail account per process | No — each run gets its own browser context |
| Cookies / localStorage | N/A | Isolated per workflow run by design (no cross-run state pollution) |
| Cleanup | Process exit | Workflow run completes or fails — context-manager driven, browser always closed |

This per-run scope is what makes multi-tenancy (and concurrent runs)
safe: two workflows hitting the same site don't share auth state,
cache, or open tabs.

### Common types

Pydantic models under `workflow_platform.connectors.browser.models`:

```python
class BrowserSelector(BaseModel):
    """A DOM selector. Auto-detects CSS vs XPath by leading character."""
    selector: str
    type: Literal["css", "xpath"] | None = None  # None = auto-detect

class BrowserClickRequest(BaseModel):
    selector: str
    timeout_ms: int = 5000

class BrowserFillRequest(BaseModel):
    selector: str
    value: str
    clear_first: bool = True

class BrowserTableRow(BaseModel):
    """One row from `browser_read_table`. Keys are column-header text."""
    # implicit — dict[str, str] under the hood, validated for consistent headers

class BrowserDownload(BaseModel):
    source_url: str       # what the click navigated to (informational)
    local_path: str       # where the file landed; subsequent steps read from here
    suggested_filename: str
    bytes: int
```

### BrowserConnector ABC

```python
class BrowserConnector(Connector):
    """Provider-agnostic browser automation.

    Abstract:  navigate, click, fill, read_text, read_table, upload,
               download, screenshot, wait_for, authenticate.
    Concrete:  health_check, trigger_poll (no-op — not a trigger),
               send (no-op — not a destination in the connector-send sense).
    """

    type: ClassVar[str]

    # --- abstract ---

    @abstractmethod
    async def navigate(self, url: str, *, wait_until: str = "load") -> None: ...

    @abstractmethod
    async def click(self, selector: str, *, timeout_ms: int = 5000) -> None: ...

    @abstractmethod
    async def fill(self, selector: str, value: str, *, clear_first: bool = True) -> None: ...

    @abstractmethod
    async def read_text(self, selector: str) -> str: ...

    @abstractmethod
    async def read_table(self, selector: str) -> list[dict[str, str]]: ...

    @abstractmethod
    async def upload_file(self, selector: str, file_path: str) -> None: ...

    @abstractmethod
    async def download_via_click(self, selector: str, *, timeout_ms: int = 30000) -> BrowserDownload: ...

    @abstractmethod
    async def screenshot(self, *, path: str | None = None, full_page: bool = False) -> str: ...

    @abstractmethod
    async def wait_for(self, selector: str, *, state: str = "visible", timeout_ms: int = 5000) -> None: ...

    # --- concrete defaults from Connector base ---

    async def health_check(self) -> bool: ...     # is the page loaded + responsive?
    async def authenticate(self) -> None: ...      # no-op by default; v1 sites that need login do it via navigate+fill+click
```

### Auto-detection: CSS vs XPath

CSS is the default. XPath is detected by leading `/` or `//`. The
agent doesn't need to specify which — `browser_click("#submit")` and
`browser_click("//button[text()='Submit']")` both work.

### Engine integration

A new `connectors: dict[str, Connector]` field on `ToolContext` lets
per-run connectors flow to tools that need them at execution time.
The engine constructs `BrowserConnector` lazily when starting a run
whose definition references any `browser_*` tool, attaches it to the
context, and tears it down in the run's `finally` block.

This is a small `ToolContext` schema change; existing tools that
don't read `connectors` are unaffected.

### Capability allowlist

Per-tool capability strings, matching the tool names (same shape as
email):

| Capability | Grants |
|---|---|
| `browser_navigate` | `navigate` — load a URL |
| `browser_read` | `read_text`, `read_table`, `screenshot`, `wait_for` |
| `browser_write` | `click`, `fill`, `upload_file` |
| `browser_download` | `download_via_click` |

The agent's v1 tool surface defaults to all four. Workflows that
should only *observe* a page (scrape but not click) can restrict to
`browser_navigate + browser_read` via per-step `capabilities.tools`.

### Sync-API wrapping

Playwright provides both sync and **async** APIs natively. We use the
async API directly — no `asyncio.to_thread` wrapping needed. This is
a cleaner story than the email connector (where `googleapiclient` is
sync-only and every call wraps).

## Phase 1: Playwright (read + click + fill + upload + download)

### Deliverables

| Module | Purpose |
|---|---|
| `workflow_platform.connectors.browser.base` | `BrowserConnector` ABC |
| `workflow_platform.connectors.browser.models` | `BrowserDownload`, `BrowserSelector`, etc. |
| `workflow_platform.connectors.browser.playwright_connector` | `PlaywrightConnector` |
| `workflow_platform.tools.browser_navigate` | `BrowserNavigateTool` |
| `workflow_platform.tools.browser_read` | `BrowserReadTextTool`, `BrowserReadTableTool`, `BrowserScreenshotTool`, `BrowserWaitForTool` |
| `workflow_platform.tools.browser_write` | `BrowserClickTool`, `BrowserFillTool`, `BrowserUploadFileTool` |
| `workflow_platform.tools.browser_download` | `BrowserDownloadTool` |
| `workflow_platform.engine.context` | `ToolContext.connectors` field added |
| `workflow_platform.engine.executor` | Lazy `PlaywrightConnector` construction per run |
| `workflow_platform.engine.functions` | New stock function: `filter_rows_by_date` |
| `workflow_platform.engine.functions` | New stock function: `write_csv` |
| `workflow_platform.tools.pdf_extract` | Generalize OCR path to JPG / PNG via a new sibling `ImageOcrTool`, OR add `image_ocr` to the existing tool |
| `examples/rpa_challenge_ocr/` | First validation workflow |
| `backend/pyproject.toml` | Add `playwright>=1.40` |
| `backend/Dockerfile` | Add chromium runtime deps + `playwright install chromium` step |

### Operator setup

Lighter than the email connector — no OAuth gates, no consent flow.
Three steps total:

**Gate 1 — Install Playwright.** `uv sync` after the dep is added,
then `uv run playwright install chromium`. Downloads the Chromium
binary (~150 MB) into the project's `~/.cache/ms-playwright/`. Idempotent.

**Gate 2 — Verify in a one-shot smoke.** A `backend/tools/smoke_browser.py`
analog of `smoke_gmail.py` — five-step diagnostic that opens
chromium, navigates to a known-static URL (e.g. `example.com`), reads
the page title, takes a screenshot, closes cleanly. Each step's
failure maps to a specific issue: missing chromium binary, libnss3
not installed, sandbox config wrong, etc.

**Gate 3 — Optional: configure default viewport / headless / downloads dir.**
Defaults are fine for most workflows; per-workflow override available
via workflow YAML config.

No secrets needed for v1. Sites that require login (Phase 2 work) get
their own setup gate when that lands.

### Cost ballpark

Browser automation itself is free (Playwright + Chromium are open
source). Per-workflow cost is just:

- ~1-2 seconds of browser startup + page load (Bedrock cost: $0)
- Whatever Bedrock-driven agentic steps the workflow runs (e.g. OCR
  field extraction is ~$0.002 per invoice on Haiku 4.5)

For the RPA challenge specifically: assuming ~20 invoices in the
table, full validation run is ~$0.05 + maybe 30-60 seconds wall-clock.

### Example workflow: `examples/rpa_challenge_ocr/`

```yaml
id: rpa-challenge-ocr
name: RPA Challenge OCR
trigger:
  type: manual

steps:
  - id: open_challenge
    type: agentic
    model: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tools: [browser_navigate, browser_click, browser_wait_for]
    capabilities:
      tools: [browser_navigate, browser_click, browser_wait_for]
    goal: |
      Navigate to https://rpachallengeocr.azurewebsites.net/ and click
      the Start button. Wait until the invoice table is populated.

  - id: read_table
    type: agentic
    model: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tools: [browser_read_table]
    capabilities:
      tools: [browser_read_table]
    goal: |
      Read the invoice table at selector `#tableSandbox`. Return the
      rows as JSON; each row has `id`, `due_date`, `invoice_url`.

  - id: filter_overdue
    type: deterministic
    function: filter_rows_by_date
    config:
      rows_from: steps.read_table.output_text
      date_field: due_date
      cutoff: today
      comparison: on_or_before

  - id: extract_invoices
    type: agentic
    model: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tools: [browser_download, image_ocr]
    capabilities:
      tools: [browser_download, image_ocr]
    goal: |
      For each row in `steps.filter_overdue.kept_rows`:
        1. Use browser_download to fetch the invoice image at row.invoice_url
        2. Use image_ocr to extract text from the downloaded JPG
        3. Extract: invoice_number, invoice_date, company_name, total_due

      Return JSON: list of {id, due_date, invoice_number, invoice_date,
      company_name, total_due} in the same order as kept_rows.
    policy:
      max_iterations: 60       # 2 tool calls per row × ~20 rows + buffer
      max_total_tokens: 50000

  - id: build_csv
    type: deterministic
    function: write_csv
    config:
      rows_from: steps.extract_invoices.output_text
      path: /tmp/rpa-challenge-output.csv
      columns: [id, due_date, invoice_number, invoice_date, company_name, total_due]

  - id: submit
    type: agentic
    model: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tools: [browser_upload_file, browser_click, browser_screenshot]
    capabilities:
      tools: [browser_upload_file, browser_click, browser_screenshot]
    goal: |
      Upload `/tmp/rpa-challenge-output.csv` via the page's file input,
      click Submit, then take a screenshot of the result page.

edges:
  - {from: open_challenge, to: read_table}
  - {from: read_table,     to: filter_overdue}
  - {from: filter_overdue, to: extract_invoices}
  - {from: extract_invoices, to: build_csv}
  - {from: build_csv,      to: submit}

policies:
  max_total_tokens: 80000
  budget_action: pause
```

The `extract_invoices` step is the only one that does heavy lifting —
fanning out per-row inside one agentic step. If a workload grows
beyond ~50 rows, we'd revisit by adding a `for_each` step type to the
engine; not needed for the RPA challenge's ~20-row table.

`agent_memory.md` carries the OCR extraction rubric (similar to
`invoice_extraction/agent_memory.md` but the invoices are different
template — sample1.jpg / sample2.jpg show the exact format).

### Tests

| Test file | Coverage |
|---|---|
| `backend/tests/test_browser_models.py` | Pydantic round-trip, selector auto-detection |
| `backend/tests/test_playwright_connector.py` | `PlaywrightConnector` against a `FakePlaywrightSession` (chained-call mock of Playwright's `Page` API) — navigate / read_text / read_table / click / fill / download mechanics |
| `backend/tests/test_browser_tools.py` | Tool dispatch + capability gating through Agent — same shape as `test_email_tools.py` |
| `backend/tests/test_rpa_challenge_workflow.py` | End-to-end against `FakeBedrock` + a fake browser session, plus a recorded HTML fixture for the RPA challenge page |
| `backend/tests/test_browser_live.py` | `@pytest.mark.browser_live` opt-in via `BROWSER_LIVE=1` — hits the real RPA challenge URL; free, no credentials |

The committed fixture for the RPA challenge is the recorded HTML of
the page (`fixtures/rpa_challenge_page.html`) plus a few sample
invoice JPGs from the site (`fixtures/sample_invoice_*.jpg`). The
live test exercises the full pipeline end-to-end including the actual
submission step.

### Build sequence (Phase 1)

Roughly day-by-day, ~5–6 days focused work. Every Playwright call is
async-native — no `to_thread` wrapping needed.

1. **`BrowserConnector` ABC + Pydantic models + Playwright dep.** Add
   `playwright>=1.40` to `pyproject.toml`. `playwright install chromium`
   in dev. Tests: model round-trips, selector auto-detect. *Half day.*

2. **`PlaywrightConnector` skeleton + session lifecycle.** Lazy
   `Browser` / `BrowserContext` / `Page` construction; `__aenter__` /
   `__aexit__` for clean teardown. `navigate` + `health_check` +
   `read_text` + `screenshot`. Tests: `FakePlaywrightSession` chained-
   call mock. *One day.*

3. **Read tools.** `browser_read_text` + `browser_read_table` +
   `browser_wait_for` + `browser_screenshot`. The table reader is the
   trickiest — needs to handle headerless tables, multi-row headers,
   `<th>` vs `<td>` first-row, etc. Tests cover each variant. *One day.*

4. **Write + download/upload tools.** `browser_click`, `browser_fill`,
   `browser_upload_file`, `browser_download_via_click`. Download
   handling is the gotcha — Playwright's download API returns a
   `Download` object asynchronously after a click; need to wire it
   into a per-run downloads directory. Tests cover the
   click-and-await-download pattern. *One day.*

5. **Engine integration.** Add `connectors` to `ToolContext`. Engine
   lazy-builds `PlaywrightConnector` when any agentic step's
   `tools:` list contains a `browser_*` name. Cleanup in run's
   `finally` block (browser always closes even on workflow failure).
   *Half day.*

6. **`examples/rpa_challenge_ocr/` + replay-mode pytest.** Workflow
   YAML, `agent_memory.md` OCR rubric, recorded fixtures, helper
   scripts. New stock function `filter_rows_by_date` + `write_csv` +
   new tool `image_ocr` (or generalize `pdf_extract`). *One day.*

7. **Live test behind `BROWSER_LIVE=1`.** Hits the real RPA challenge
   URL, runs the full pipeline, asserts the submission succeeded.
   Free; no credentials. *Half day.*

8. **CI cadence.** Add `BROWSER_LIVE=1` to the existing
   `.github/workflows/live-tests.yml` weekly job. Requires chromium
   install in the workflow runner. *Quarter day.*

**Total: ~5.5 days focused.** Browser automation has fewer "unknown
unknowns" than OAuth had for email — the failure modes are mostly in
selectors (which we control via the rubric) and chromium runtime
deps (which Docker handles once).

## Phase 2: Selenium fallback

Once Phase 1 ships, Selenium becomes mechanical if needed:
- `SeleniumConnector(BrowserConnector)` against `selenium`
- Workflow YAML swap: `browser_backend: selenium` (default playwright)
- Useful only for environments where Playwright won't install
  (rare — Playwright is more robust)

Effort: ~2 days. Worth doing only if a deployment target can't
install Playwright.

## Phase 3: Login flow handling

For sites that require auth, two patterns:

1. **Cookie injection.** Operator runs the login flow once manually,
   exports cookies via Playwright's `BrowserContext.storage_state` to
   a JSON file under `.secrets/browser/<site>/storage_state.json`
   (gitignored, chmod 0600). Connector loads this on session start.
   Same shape as Gmail's refresh token.

2. **Recorded login script.** A pre-step in the workflow that fills
   the login form. Credentials from `SecretStore`. Pros: works for
   sites that rotate sessions. Cons: brittle, exposes credentials to
   the connector layer.

Phase 3 lands when a workload needs auth. ~2 days for cookie injection,
+1 day for the recorded-login path.

## Phase 4: Anti-bot / headed-mode / browser pooling

Pull-in when forced:

- **Anti-bot evasion**: realistic user-agent rotation, headless-
  detection mitigation (`playwright-stealth`-style patches), human-
  like mouse movements. Cat-and-mouse with site protection.
- **Headed mode for debugging**: workflow YAML config to launch with
  `headless: false` so an operator can watch the browser. Already
  feasible via env var; Phase 4 just formalizes.
- **Browser pooling for concurrent runs**: if many concurrent
  workflows each spin up Chromium, memory adds up. A small pool of
  N=4 contexts shared across runs would dedupe. Worth doing if/when
  we run >10 concurrent browser workflows; not before.

## Out of scope (deferred *within* the browser connector)

Don't build in v1; pull in when a workload demands:

| Feature | When to revisit |
|---|---|
| Multi-page workflows (tabs, popups) | A workload needs to follow a popup or open multiple tabs |
| Drag-and-drop interactions | A workload needs canvas/SVG drag-and-drop (rare) |
| Keyboard shortcuts beyond fill | A workload needs Ctrl+S / Ctrl+C / arrow-key navigation |
| Network interception / mocking | Testing harness wants to mock backend responses |
| iframe traversal | Embedded content (e.g. PDF in iframe) needs interaction |
| Mobile emulation | Need to test mobile-only views |
| Browser extensions | Need to interact with site features that require an installed extension |

## Resolved decisions

These were the open questions; all settled 2026-05-26 before any code
landed:

1. **Eager vs lazy connector construction.** ✅ **Lazy.** Engine
   inspects each workflow's step definitions on run start; if any
   agentic step's `tools:` list contains a `browser_*` name, the
   engine builds a `PlaywrightConnector` for that run and tears it
   down in the run's `finally` block. Workflows that don't touch
   browsers pay zero overhead. Same pattern as
   `maybe_build_gmail_connector`.

2. **Tool surface granularity.** ✅ **Coarse 4-category.**
   `browser_navigate` / `browser_read` / `browser_write` /
   `browser_download` are the capability strings; the 9 tools map
   onto them as shown in the [Capability allowlist](#capability-allowlist)
   section. Coarse is more usable for v1; per-tool gating remains
   available if a workload demands finer-grain restriction.

3. **Image OCR.** ✅ **New `ImageOcrTool`.** Separate from
   `PdfExtractTool`. Pytesseract handles JPG / PNG / WebP natively
   without the PDF intermediate. Cleaner separation of concerns even
   though both tools share the underlying tesseract binary.

4. **Selector validation.** ✅ **Pass-through.** The connector does
   not pre-validate CSS / XPath syntax; Playwright's error surfaces
   through to the agent as a `ToolResult.error` with a clear message.
   Matches the email connector's "let Gmail's HttpError surface"
   pattern.

5. **Screenshot retention.** ✅ **Same per-run dir as downloads,
   prefixed.** Screenshots land at
   `/tmp/browser-downloads/<instance_id>/screenshot-<step_id>-<ts>.png`.
   Same directory keeps the per-run artifacts colocated; the prefix
   makes them grep-findable.

6. **Ship now.** ✅ **Yes.** The RPA Challenge OCR site is the
   validation workload. The 6-day build sequence starts immediately
   after this plan commits.

## Risks

1. **Sites change and selectors break.** Mitigation: encourage robust
   selector strategies in `agent_memory.md` rubrics — prefer text-based
   selectors over positional ones, use ARIA labels where present, fall
   back to XPath only when necessary. Document recovery: a failed
   selector returns a clear error, the agent can call
   `browser_screenshot` + retry. Add to the rubric the pattern:
   "if click fails, screenshot, identify what changed, retry with
   updated selector."

2. **Headless Chromium detection.** Some sites detect headless browsers
   and serve different content. Mitigation: deferred to Phase 4
   (anti-bot evasion). For v1 we accept this — most sites worth
   automating either don't care, or we have legitimate auth that
   bypasses the detection.

3. **Resource leaks.** A browser process left running consumes
   memory and zombies up. Mitigation: context-manager-driven cleanup
   in the engine's run() finally block, plus a periodic "kill
   orphaned chromium processes" cleanup job. Tests assert browser
   close even when the workflow raises.

4. **CI Docker image bloat.** Chromium adds ~250 MB to the image.
   Mitigation: accept it. Worth the cost for the capability gain.
   Don't bother with multi-stage builds optimizing chromium out of
   the base image — too much complexity for too little benefit at
   solo-dev scale.

5. **Test flakiness.** Browser tests are infamously flaky — timing
   issues, network blips, animations. Mitigation: aggressive
   `wait_for` discipline in the rubric, explicit timeouts on every
   action (no infinite-block defaults), and the live test marked
   `@pytest.mark.browser_live` so it doesn't run in default pytest.

6. **Anti-automation legal exposure.** Some sites' ToS prohibit
   automated access. Mitigation: this is the operator's responsibility,
   not the platform's. Document in `docs/ARCHITECTURE.md` security
   section: "the browser connector is a capability, not a license to
   use it — operators are responsible for ToS compliance with sites
   they automate."

## When this graduates

The trigger that opens this plan into a build: **a concrete UI-driven
workload someone actually wants automated.** The RPA challenge is one
such workload (validation-only, low-stakes). Real candidates:

- A vendor portal data export that has no API
- A SaaS dashboard's "download this report as CSV" button
- A government / DMV form-filling workflow
- A customer support tool that only exposes its workflow via the UI
- End-to-end testing of our own deployed dashboard

When that pull arrives:

1. Re-read this doc, pick which sub-features the workload needs.
2. Execute Phase 1's build sequence.
3. Iterate the workflow's `agent_memory.md` rubric the same way
   PR-triage, paper-triage, email-triage, and invoice-extraction
   iterated theirs.
4. Update `docs/USE_CASES.md` to move "Browser automation" from "What
   to skip" into a candidate workload entry.
5. Update this doc with what changed during the build.

## How this doc goes stale

- Phase 1 begins → migrate this doc's design sections into the actual
  code as docstrings; keep this doc for the historical "why" + the
  out-of-scope list.
- A new browser-automation backend becomes the primary target → add
  a Phase N section, don't rewrite the ABC.
- A risk above turns into a real bug → log it in
  `docs/NEXT_STEPS.md` with reference to this doc.
