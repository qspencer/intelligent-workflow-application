# Reviewer's guide — G30 round 3: the three round-2 findings, closed

**Branch:** `main` · **Answers:** your round-2 review of `5866f3d` (request
changes; text kept verbatim in `docs/TRACE_G30_R2_REVIEW_RESULTS.md`) ·
**Change since round 2:** `5866f3d`..this package. Source last changed at
`a41e146`; later commits are documents and tests.

| | |
|---|---|
| What changed, finding by finding | §1 |
| Your next acceptance checks, one by one | §4 |
| Run the claims | `docs/review-artifacts/g30_reproductions.py` |
| Run the controls | `docs/review-artifacts/g30_controls.py` |
| Gates, claims and controls as run | `docs/archives/GATE_OUTPUT_G30_R3.txt` |
| What production did under the layer | `docs/archives/LIVE_EVIDENCE_G30_R3.txt` |
| **The runtime the suite needs** | §7 — now with veracium's own qualification check |

---

## 1. What round 2 found, and what changed

| Finding | Fix | Commit | Claim |
|---|---|---|---|
| **R2-F1** — the ECR build script still built from `backend/` | `-f backend/Dockerfile` with the repo-root context. The places that build the image are now found by scanning the tree, not listed from memory, and the real script runs with `docker` and `terraform` stubbed. | `f502c9b` | A2 |
| **R2-F2** — a queued upload of a missing path could become a directory upload | The source is examined only once the call owns the browser: opened once, refused unless it is one regular file, and read from that same descriptor. The page gets those BYTES, never a path Playwright would classify again. | `a41e146` | E7 |
| **R2-F3** — a read checked the page, waited, and returned an off-list page's text | Every read is fenced: the URL and a count of main-frame navigations are taken with the check, and if either differs when the read returns, the result is withheld. Actions re-check the page when they end; their contract is narrowed (§2). | `a41e146` | E8, E5 |
| Stale comment on the layer variable | Corrected. | `f502c9b` | — |

Every new test was run against the code with its fix removed and failed; each
has a control in the harness that repeats that on every run. Your probes,
inverted:

| Your probe | Ours |
|---|---|
| `test_documented_image_script_uses_the_obsolete_build_context` | `test_the_ecr_script_builds_from_the_root_context` — the real script, stubbed the way yours was; beside it `test_every_image_build_site_is_known`, which fails when a new build site appears unlisted |
| `test_queued_upload_accepts_a_directory_created_after_its_check` | `test_a_queued_upload_cannot_become_a_directory_upload` — your sequence, with the real `write_csv`; its allowed twin `test_a_queued_upload_reads_its_source_when_its_turn_comes` pins WHEN the source is examined |
| `test_top_level_change_during_read_is_returned_without_recheck` | `test_the_reviewers_probe_a_url_change_with_no_event_is_withheld`; and in real Chromium, `test_a_read_waiting_while_the_page_leaves_for_an_off_list_host_is_withheld` |
| `test_operation_lock_keeps_policy_and_releases_on_cancellation` | not adopted — it asserts the repair; the round-2 engine test still uses the bounded hold you called reasonable |

**R2-F3 in a real browser.** We ran the sequence you described before fixing
it: an allowed page with no `#late` whose own script navigates to an allowed
hop, which redirects to an off-list page that has `#late`. With the fence
disabled, Chromium returned the disallowed host's text. With it, the read is
withheld and the page cleared; beside it, a stable allowed page where `#late`
arrives late is read normally. That pair is in E5, and its control is opt-in.

**Why the navigation count, not only the URL.** A URL compared at the end
passes a read taken from an off-list page the browser then LEFT — off-list and
back to the very URL that was checked. The count differs, so the read is
withheld (`test_a_read_through_an_off_list_page_and_back_is_withheld`, with a
control that stops counting). Child-frame navigations do not count; a test
shows a frame navigating mid-read does not withhold it.

**What the fence relies on.** The count must be current when the read's reply
arrives. Playwright's client handles protocol messages in order
(`Connection.dispatch`), and a navigation event reaches this connector's
synchronous handler as it is dispatched (`Frame._on_frame_navigated` →
`Page.emit` → pyee calls a sync handler at once). A reply from the new
document cannot precede the event of the navigation that created it. We read
this in the installed Playwright; the real-Chromium test is what pins it.

**Why actions are not fenced the same way.** A read's result can be withheld;
an action's effect cannot be undone. Binding an action to the checked document
would mean acting on an element handle instead of a locator: that drops the
locator's re-resolution, which the SPA workloads lean on, and still leaves a
window, because a click is mouse input at coordinates. So the contract states
what is done — checked before, checked after, cleared and failed if the page
left — rather than claiming more (§2; the first question in §8).

## 2. The contracts, as they now stand

Changed from round 2 are marked **(changed)**.

- **The system layer bounds every workflow** the service runs, on every
  launch path, and every operator script. Unchanged; the ECR build script now
  produces an image that carries it **(changed)**.
- **File functions and the path matcher** — unchanged.
- **`max_tokens_per_call`** — unchanged: a per-call output ceiling, not a cost
  ceiling.
- **`allowed_hosts` (changed).** It blocks requests the agent initiates before
  dispatch, and checks the TOP-LEVEL page before every read and action.
  - A READ (`read_text`, `read_table`, `wait_for`, `screenshot`) returns only
    what the checked document gave it. If the page navigates while the read
    waits, the result is withheld — a screenshot is deleted — and the page is
    cleared if it is now off-list.
  - An ACTION (`click`, `fill`, `upload_file`, `submit_form`) starts on an
    allowed page, and the page is checked again when it ends. If the page
    navigates off-list while Playwright waits for the element, the action can
    land there; the call then fails and the page is cleared. That stays inside
    the bound below: an allowed page's own scripts can already send what is
    typed into it anywhere.
  - It is neither an egress control nor a content-origin guarantee: redirect
    hops, subresources, preconnect and WebSockets can reach a disallowed host,
    and an allowed page can render a disallowed host's frames and images.
  - A step's policy holds for its whole tool call, beside parallel steps
    sharing the page.
- **Browser outputs** stay in the run's own directory. Unchanged.
- **Browser uploads (changed)** are `file_read`s of the path, and send the
  bytes of one regular file, examined once the call owns the browser. A size
  cap (`MAX_UPLOAD_BYTES`) applies.
- **Ids** — unchanged.

## 3. Claims and evidence

As captured at the shipped tree with both opt-in suites enabled, the claim
runner reports 21 claims, 184 test results, every claim reproduced. The
controls harness defines 52 controls, all fire, and every claim has at least
one aimed at its own tests.

| | Claim | Round 3 |
|---|---|---|
| A1 | The layer loads strictly; unset is the shipped layer, empty or unreadable refuses | |
| A2 | Every engine carries a layer; the image carries it, and every place that builds the image supplies the root context | changed (R2-F1) |
| A3 | Every armed workflow is accounted for, both ways | |
| A4 | Health reports counts, never names | |
| B1 | An unlisted tool is refused through the real engine | |
| B2 | The token clamp covers agent steps, suggestion and distillation | |
| C1–C5 | Matcher; file functions; check-before-access; parallel context; live workflows | |
| D1, D3 | Ids are tokens; a stored released vocabulary refuses on Postgres load | |
| E1 | Agent-initiated requests blocked before dispatch; the page checked before every read and action | wording |
| E2 | URLs the two parsers could read differently are refused | |
| E3 | Outputs stay in the run's own directory | |
| E4 | The browser stays out of the system layer | |
| E5 | Against real Chromium — now including your delayed-navigation sequence and its stable-page control | changed (R2-F3) |
| E6 | A step's host policy holds for its whole call beside a parallel sibling | |
| E7 | Uploads obey `file_read`; the source is one regular file, examined once the call owns the browser, sent as bytes | changed (R2-F2) |
| E8 | A read returns only what the checked document gave it; an action whose page leaves fails and is cleared; every page method is classified | new (R2-F3) |

E8's last test is derived from the source: every connector method that checks
the page must be a fenced read or end with the landing check, so a new method
fails until it is classified.

## 4. Your next acceptance checks

1. **The ECR build command, against the root-context Dockerfile, alongside
   Compose.** Done (A2). We also built the image with exactly the script's
   command and ran it once: the context was the allowlist only, the layer was
   `configured`, and no secret, mail data or memory store was inside. That run
   is not in the capture; the two tests pin what it depends on.
2. **The upload source's identity and type preserved across queueing and
   dispatch, with the transition and an allowed control.** Done (E7). The
   bytes handed over come from the descriptor whose type was checked.
3. **The lifetime of the top-level check through awaited reads and actions;
   the delayed-navigation regression in real Chromium.** Done for reads (E8,
   E5). For actions, the contract is narrowed to what is done (§1, §2).
4. **Browser exclusion retained.** Yes; the test still refuses any browser
   tool in the layer.
5. **Optional suites, a full qualified-runtime suite, the manifest and the
   controls captured.** All in `GATE_OUTPUT_G30_R3.txt` and
   `CODE_MANIFEST_G30_R3.txt`. The capture header now prints veracium's own
   verdict, `runtime_supported()`, as you suggested.

## 5. Residuals

Carried and still true: browser destinations (no browser tool may enter the
layer); symlinks inside an allowed tree; triggers' own I/O; spend has no
platform ceiling; scaffold drafts are approved at run time, not field by
field; the last step's policy stays on the connector between calls; the image
is not deployable as-is (its SQLite is not veracium's qualified build, and it
is not built from `uv.lock`).

New this round:

- **Actions whose page navigates mid-action** are detected and cleared, not
  prevented (§1).
- **The fence's ordering assumption** is pinned by one real-Chromium sequence,
  not proven for every kind of navigation.
- **Same-document navigations** (a hash change, `pushState`) during a read
  also withhold it. That is conservative — the host cannot change — and the
  agent reads again.
- **Uploads check a path, then open it.** A symlink swapped in between is the
  carried symlink residual; the type check is on what was opened.

## 6. Live state

From `LIVE_EVIDENCE_G30_R3.txt`, since phase 1 was deployed:

- 67 real `email-triage-apply` runs, all completed; the governed tool was
  called 67 times under the layer and succeeded every time;
- the largest agent-step output was 143 tokens, against the cap;
- memory distillation: 10910 observations recorded, the largest 2196 output
  tokens;
- 4 real `scheduled-health-report` runs, all completed, writing the moved log;
- every real DMARC file on disk is admitted, including all 48 the viewer
  holds. A real delivery still waits on a report email.

The round-3 code is deployed (the service restarted at 20:21 UTC on
2026-10-29), and production still has no browser tool.

## 7. How to run — the runtime matters

**veracium refuses to open its store on any runtime it has not qualified.**
Its qualification is not the SQLite version alone: `runtime_supported()`
compares the build identity and constructor evidence it recorded. uv's own
managed Python bundles SQLite `3.53.1`, which it refuses — the cause of
all 14 failures in your full-suite run, in learned-memory tests and the
real-store health test, as in round 1. No G30 claim depends on them.

The qualified runtime is a Python `3.14` linked to the SYSTEM SQLite of
`ubuntu-24.04` (`3.45.1`): the build `actions/setup-python` installs there,
which CI uses with `UV_PYTHON_PREFERENCE: only-system`, and which this box
runs. Check with veracium's own predicate, not only the version:

```
python -c "import sqlite3; from veracium.store.schema_version import runtime_supported; print(sqlite3.sqlite_version, runtime_supported())"
# must print: 3.45.1 True
```

Then, from `backend/`:

```
UV_PYTHON=<that python> uv sync --frozen
uv run python ../docs/review-artifacts/g30_reproductions.py
uv run python ../docs/review-artifacts/g30_controls.py     # edits sources in place, restores each
```

Opt-in: `BROWSER_LIVE=1` with Chromium installed (`uv run playwright install
chromium`) — E5 now carries the delayed-navigation sequence you asked to see,
so it is the one most worth retrying; and `TEST_DATABASE_URL` naming a
Postgres database whose tables you can lose, for D3.

## 8. What we most want from you

1. **Actions.** Is "checked before, checked after, cleared and failed if the
   page left" the right contract for actions, or should enablement require
   binding actions to the checked document despite the locator trade-off?
2. **The fence's assumption.** Is the ordering argument in §1 sound for the
   navigation kinds you would test — a redirect landing, a script navigation,
   a history navigation, a navigation that fails to an error page?
3. **Anything in §2** that still claims more than the tests show.
