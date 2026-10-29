# Reviewer's guide — G30 round 4: the error channel, closed

**Branch:** `main` · **Answers:** your round-3 review of `3e12786` (request
changes; text kept verbatim in `docs/TRACE_G30_R3_REVIEW_RESULTS.md`) ·
**Change since round 3:** `3e12786`..this package. Source last changed at
`7b6dabb`; later commits are documents and tests.

| | |
|---|---|
| What changed, finding by finding | §1 |
| Your next acceptance checks, one by one | §4 |
| Run the claims | `docs/review-artifacts/g30_reproductions.py` |
| Run the controls | `docs/review-artifacts/g30_controls.py` |
| Gates, claims and controls as run | `docs/archives/GATE_OUTPUT_G30_R4.txt` |
| What production did under the layer | `docs/archives/LIVE_EVIDENCE_G30_R4.txt` |
| The runtime the suite needs | §7 |

---

## 1. What round 3 found, and what changed

| Finding | Fix | Commit | Claim |
|---|---|---|---|
| **R3-F1 (P2)** — a read whose page navigated and then FAILED passed Playwright's error, which quotes the page, to the agent; an action that errored skipped its landing check | Reads decide on both exits: if the document changed, the result or the error is withheld, replaced by an error carrying only the original's class name. Every action goes through one wrapper with the same error rule and the landing check on a normal return. | `7b6dabb` | E8, E5 |
| **R3-F2 (P3)** — the action wording promised detection of any off-list transition | The contract now states the endpoint check (§2). | `7b6dabb` | E8 |

**We ran your Chromium companion before fixing anything.** Your sandbox could
not install Chromium; ours could. `live_error_probe.py` passed against the
round-3 code: with two matches on the redirect landing, the tool error read

```
browser_read_text failed: ... strict mode violation: locator(".late") resolved to 2 elements:
    1) <p class="late">OFF_LIST_DIAGNOSTIC_FIXTURE</p> ...
```

and the page stayed on the disallowed host. So R3-F1 is a real-browser
finding, not only a connector-level one. Your other probes reproduced too.

Every new test was run against the code with its fix removed and failed; each
has a control. Your probes, inverted:

| Your probe | Ours |
|---|---|
| `test_read_error_skips_fence_and_returns_document_diagnostic` (text and table, off-list and back) | `test_an_error_after_the_page_left_for_an_off_list_host_is_withheld` and `test_an_error_after_off_list_and_back_is_withheld`, each across every read that can fail and every action; the twin `test_an_error_about_the_checked_page_is_kept` |
| `test_read_error_reaches_the_next_model_turn_through_the_real_engine` | `test_the_model_never_sees_an_error_from_an_unchecked_page` — the real engine and agent loop; the model's next turn and the step output carry `withheld`, not the quote |
| `test_action_error_skips_landing_cleanup` | the action half of the first two tests above: the page is cleared and the error withheld |
| `test_action_round_trip_can_return_success` | `test_an_action_that_leaves_and_returns_is_not_detected` — kept as the stated contract, so changing it is a decision |
| `live_error_probe.py` (both cases) | `test_an_error_quoting_an_off_list_page_is_withheld` beside `test_the_same_error_on_a_page_that_stays_put_is_kept`, in E5 |

**What the withheld error carries.** Nothing from either page: not the result,
not the original error's text (its class name only), and not the URL the page
reached, which a redirect's server chose. A test caught one leak while we
built this: the landing check's own error, which names that URL, was riding
along as the new error's `__context__`. Every raise in the withholding path is
now outside the `except` that caught the original, and a test asserts
`__context__` is empty.

**What is kept.** An error about the checked document reaches the agent
untouched — it needs a strict-mode error to fix its selector. Real Chromium
shows the pair: the same two-match error is withheld after a redirect off-list
and kept on a page that stays put.

**Cancellation** is untouched: it is not an `Exception`, so it passes through
without the check, and the next call's guard clears whatever page is left. No
result reaches the agent from a cancelled call.

**The code-shape test** now pins only routing — reads through `_fenced`,
actions through `_acted`. You showed a shape test cannot prove every exit
reaches a check; the behavioural tests above pin the exits.

## 2. The contracts, as they now stand

Changed from round 3 are marked **(changed)**.

- **The system layer, file functions, the path matcher,
  `max_tokens_per_call`, browser outputs, uploads, ids** — unchanged.
- **`allowed_hosts` (changed).** It blocks requests the agent initiates before
  dispatch, and checks the top-level page before every read and action.
  - A READ returns only what the checked document gave it — its result or its
    error. If the page navigates while the read runs, whichever it produced is
    withheld, nothing of either page is passed on, and the page is cleared if
    it is now off-list.
  - An ACTION is checked on every exit. On normal completion, if the final
    check finds an off-list page, the call fails and the page is cleared.
    Intermediate navigation, including leaving and returning to an allowed
    URL, is not detected by this check. If the action fails after the page
    navigated, its error is withheld as a read's is. Its effect cannot be
    undone: if the page navigates while Playwright waits for the element, the
    action can land on the new page and act with that page's session.
  - It is neither an egress control nor a content-origin guarantee, and it is
    not a bound on where an action's effect lands.

## 3. Claims and evidence

As captured at the shipped tree with both opt-in suites enabled, the claim
runner reports 21 claims, 212 test results, every claim reproduced. The
controls harness defines 59 controls, all fire, and every claim has at least
one aimed at its own tests.

Changed this round: **E8** — a read's result or error, and an action's error,
withheld after navigation; the checked page's error kept; the model never sees
the quote; routing pinned. **E5** — your two-match sequence and its stable
twin, in real Chromium. The other claims are unchanged from round 3.

## 4. Your next acceptance checks

1. **Read errors validated, with a real-engine assertion and a real-Chromium
   multiple-match case.** Done (E8, E5).
2. **Action cleanup on error exits; the off-list-and-back action contract.**
   Done; clarified rather than strengthened — failing every action whose page
   navigated would fail every link click.
3. **The accepted fixes retained.** Yes, all claims and controls from round 3
   still run.
4. **Browser tools excluded.** Yes. Your enablement conditions are recorded in
   `docs/NEXT_STEPS.md` under G30: actions bound to the checked document if
   `allowed_hosts` is to bound effects, and a real-browser matrix for the
   fence's ordering before enablement.
5. **Qualified-runtime suite and optional checks re-captured.** All in
   `GATE_OUTPUT_G30_R4.txt`, with `runtime_supported()` in its header.

## 5. Residuals

Carried and still true: browser destinations; actions' effect location (above);
symlinks inside an allowed tree; triggers' own I/O; spend has no platform
ceiling; scaffold drafts approved at run time; the last step's policy stays on
the connector between calls; the image is not deployable as-is.

The fence's ordering assumption is pinned in real Chromium for a script
navigation through a redirect, for a successful read and a failing one. Your
navigation matrix — `pushState`/hash, history and BFCache restoration,
same-URL replacement, abort-before-commit against a committed error page — is
not yet run; it is recorded as an enablement condition rather than claimed.

## 6. Live state

From `LIVE_EVIDENCE_G30_R4.txt`, since phase 1 was deployed:

- 73 real `email-triage-apply` runs, all completed; the governed tool was
  called 73 times under the layer and succeeded every time;
- the largest agent-step output was 143 tokens, against the cap;
- memory distillation: 10916 observations recorded, the largest 2196 output
  tokens;
- 4 real `scheduled-health-report` runs, all completed;
- every real DMARC file on disk is admitted, including all 48 the viewer
  holds.

The round-4 code is deployed, and production still has no browser tool.

## 7. How to run

As in round 3. veracium refuses any runtime its `runtime_supported()` does not
qualify — your run's returned False on SQLite `3.53.1`, the cause of
all 14 failures in your full-suite run, again in learned-memory tests and the
real-store health test. The qualified runtime is a Python `3.14` linked to the
system SQLite of `ubuntu-24.04` (`3.45.1`), the build `actions/setup-python`
installs. From `backend/`:

```
UV_PYTHON=<that python> uv sync --frozen
uv run python ../docs/review-artifacts/g30_reproductions.py
uv run python ../docs/review-artifacts/g30_controls.py     # edits sources in place, restores each
```

Opt-in: `BROWSER_LIVE=1` with Chromium (E5 now carries your two-match case),
and `TEST_DATABASE_URL` naming a disposable Postgres database, for D3.

## 8. What we most want from you

1. **The error rule.** Is "withhold if the document changed, keep otherwise"
   the right line, given an error about the checked page can still name
   elements a disallowed frame inserted into it (the content-origin residual)?
2. **What the withheld error carries** — the original's class name and
   whether the page was cleared. Is anything else safe and useful to keep?
3. **Anything in §2** that still claims more than the tests show.
