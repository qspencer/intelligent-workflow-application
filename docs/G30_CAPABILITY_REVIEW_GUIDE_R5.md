# Reviewer's guide — G30 round 5: when clearing the page fails

**Branch:** `main` · **Answers:** your round-4 review of `8be82be` (request
changes; text kept verbatim in `docs/TRACE_G30_R4_REVIEW_RESULTS.md`) ·
**Change since round 4:** `8be82be`..this package. Source last changed at
`75052a1`; later commits are documents and tests.

| | |
|---|---|
| What changed | §1 |
| Your next acceptance checks, one by one | §4 |
| Run the claims | `docs/review-artifacts/g30_reproductions.py` |
| Run the controls | `docs/review-artifacts/g30_controls.py` |
| Gates, claims and controls as run | `docs/archives/GATE_OUTPUT_G30_R5.txt` |
| What production did under the layer | `docs/archives/LIVE_EVIDENCE_G30_R5.txt` |
| The runtime the suite needs | §7 |

---

## 1. What round 4 found, and what changed

| Finding | Fix | Commit | Claim |
|---|---|---|---|
| **R4-F1 (P2)** — withholding depended on the clearing navigation succeeding; a failed clear's error, which can name the interrupting URL, replaced the withheld one and reached the model; a rejected screenshot stayed on disk | Fixed where the page is cleared, so every caller is covered. A failed clear raises `BrowserPageNotCleared` carrying only the error's class. Withholding reports what clearing did as one fixed value. Screenshots are captured to a pending name and moved into place only after the check. | `75052a1` | E8, E5 |

**Fixed at the source, not only where you found it.** The clearing step is
`_enforce_landing`. Besides the withholding path you reproduced, it runs at the
start of every call and after `navigate` — the same shape, also unlisted. A
failed clear there now raises the same sanitized error, says clearing failed,
and reads nothing from the page; the next call checks the page again and
clears it once clearing works
(`test_a_page_found_off_list_that_cannot_be_cleared_says_so`).

**The withheld status, as you suggested.** A withheld call's error ends in a
bounded status:

```
[document_changed; call=read; error=Error; cleanup=failed]
```

`call` is one of the connector's two call kinds, `error` is the original's
class name or `none`, and `cleanup` is `cleared`, `not_needed` or `failed`.
Nothing else from either page is carried: not the result, not the original or
the clearing error's text, not a URL, and no exception chained as context.
After a failed clear the page is left as it is and not reported cleared.

**Screenshots.** The capture is taken to a pending name beside the target and
moved to the requested path only once the check passes; the pending file is
removed on every exit. A rejected capture never reaches the requested path,
whatever the check or the clearing raised — so disposal no longer depends on
an exception type.

**Your probes, inverted:**

| Your probe | Ours |
|---|---|
| `test_cleanup_error_replaces_the_sanitized_result_with_a_url` (successful read, failed read, failed action) | `test_a_withheld_result_stays_withheld_when_clearing_fails` across every read, and `test_a_withheld_error_stays_withheld_when_clearing_fails` across every read that can fail and every action |
| `test_screenshot_disposal_depends_on_cleanup_exception_type` | `test_a_rejected_screenshot_never_reaches_its_path`, clearing succeeding and failing — the folder is empty both ways |
| `test_cleanup_diagnostic_reaches_the_next_model_turn` | `test_the_model_never_sees_a_failed_clear` — the real engine; the model sees `cleanup=failed`, not the URL |
| `test_successful_cleanup_withholds_the_original_material` | the round-4 tests, unchanged, plus `test_the_withheld_status_reports_what_clearing_did` |
| `test_cancellation_releases_operation_and_next_guard_clears_page` | the contract now states the cancellation exception beside "checked on every exit" |

**In real Chromium.** `test_a_withheld_read_stays_withheld_when_clearing_really_fails`
runs the redirect sequence and makes the clearing navigation fail for real:
the page is closed just before it, so Playwright itself raises. That is not
your interrupted-navigation timing, which we did not try to reproduce; it is a
real clearing failure of another kind, and its control removes the fix.

Every new test was run against the code with its fix removed and failed; each
has a control.

## 2. The contracts, as they now stand

Changed from round 4 are marked **(changed)**.

- **The system layer, file functions, the path matcher,
  `max_tokens_per_call`, browser outputs, uploads, ids** — unchanged.
- **`allowed_hosts`.** It blocks requests the agent initiates before
  dispatch, and checks the top-level page before every read and action.
  - A READ returns only what the checked document gave it — its result or its
    error. If the page navigates while the read runs, whichever it produced is
    withheld and nothing of either page is passed on.
  - **(changed)** That holds whether or not clearing the page succeeds. The
    withheld error reports what clearing did as one fixed value; after a
    failed clear the page is not reported cleared, and the next call checks
    it again. A rejected screenshot never reaches its path.
  - An ACTION is checked on every exit but cancellation **(changed: the
    exception stated here)**. On normal completion, if the final check finds
    an off-list page, the call fails and the page is cleared — or reported
    not cleared. Leaving and returning to an allowed URL is not detected. An
    action's error after navigation is withheld as a read's is. Its effect
    cannot be undone, and it can act with another page's session.
  - It is neither an egress control nor a content-origin guarantee, and not a
    bound on where an action's effect lands.

## 3. Claims and evidence

As captured at the shipped tree with both opt-in suites enabled, the claim
runner reports 21 claims, 231 test results, every claim reproduced. The
controls harness defines 65 controls, all fire, and every claim has at least
one aimed at its own tests.

Changed this round: **E8** — withholding when clearing fails, for results and
errors; screenshot disposal; the failed clear at call start; the status; the
model never sees a failed clear. **E5** — a real failing clear in Chromium.
The other claims are unchanged.

## 4. Your next acceptance checks

1. **Sanitized withholding survives an ordinary clearing failure, without
   claiming cleanup succeeded.** Done (E8), at every caller of the clearing
   step.
2. **Rejected screenshot disposal independent of the clearing outcome.** Done:
   pending name, moved only after the check, removed on every exit.
3. **The cleanup-failure regressions, their contrasts, through the
   model-facing path; a real failed clear when Chromium is available.** Done;
   the real failure is a closed page, not an interrupted navigation (§1).
4. **Accepted fixes kept; browser exclusion and the enablement conditions
   kept.** Yes.
5. **Qualified-runtime suite and optional checks re-captured.** All in
   `GATE_OUTPUT_G30_R5.txt`.

## 5. Residuals

Unchanged from round 4: browser destinations; where an action's effect lands;
symlinks inside an allowed tree; triggers' own I/O; spend has no platform
ceiling; scaffold drafts approved at run time; the last step's policy stays on
the connector between calls; the image is not deployable as-is; your
navigation matrix is an enablement condition, not run.

New this round: **a page that cannot be cleared stays where it is.** Nothing
is read from it, and every call begins by checking it, so it is cleared once
clearing works; we did not add a page replacement for the failure case.

## 6. Live state

From `LIVE_EVIDENCE_G30_R5.txt`, since phase 1 was deployed:

- 78 real `email-triage-apply` runs, all completed; the governed tool was
  called 78 times under the layer and succeeded every time;
- the largest agent-step output was 143 tokens, against the cap;
- memory distillation: 10921 observations recorded, the largest 2196 output
  tokens;
- 4 real `scheduled-health-report` runs, all completed;
- every real DMARC file on disk is admitted, including all 48 the viewer
  holds.

The round-5 code is deployed, and production still has no browser tool.

## 7. How to run

As in round 4. veracium refuses any runtime its `runtime_supported()` does not
qualify — your run's returned False on SQLite `3.53.1`, the cause of
all 14 failures in your full-suite run. The qualified runtime is a Python
`3.14` linked to the system SQLite of `ubuntu-24.04` (`3.45.1`), the build
`actions/setup-python` installs. From `backend/`:

```
UV_PYTHON=<that python> uv sync --frozen
uv run python ../docs/review-artifacts/g30_reproductions.py
uv run python ../docs/review-artifacts/g30_controls.py     # edits sources in place, restores each
```

Opt-in: `BROWSER_LIVE=1` with Chromium, and `TEST_DATABASE_URL` naming a
disposable Postgres database, for D3.

## 8. What we most want from you

1. **Leaving an uncleared page in place.** Is "nothing read, checked again by
   the next call" enough, or should a failed clear discard the page?
2. **The status format.** Is the bounded status the right shape for the
   model-facing refusal?
3. **Anything in §2** that still claims more than the tests show.
