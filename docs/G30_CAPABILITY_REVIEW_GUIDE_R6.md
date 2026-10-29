# Reviewer's guide — G30 round 6: no refusal names a page's URL

**Branch:** `main` · **Answers:** your round-5 review of `93cc364` (request
changes; text kept verbatim in `docs/TRACE_G30_R5_REVIEW_RESULTS.md`) ·
**Change since round 5:** `93cc364`..this package. Source last changed at
`d2f6627`; later commits are documents and tests.

| | |
|---|---|
| What changed | §1 |
| Your next acceptance checks, one by one | §4 |
| Run the claims | `docs/review-artifacts/g30_reproductions.py` |
| Run the controls | `docs/review-artifacts/g30_controls.py` |
| Gates, claims and controls as run | `docs/archives/GATE_OUTPUT_G30_R6.txt` |
| What production did under the layer | `docs/archives/LIVE_EVIDENCE_G30_R6.txt` |
| The runtime the suite needs | §7 |

---

## 1. What round 5 found, and what changed

| Finding | Fix | Commit | Claim |
|---|---|---|---|
| **R5-F1 (P2)** — after a failed clear, the retry's start-of-call refusal named the URL the withheld call had kept back | The refusals themselves carry no URL a page or server chose, so no caller has to redact. Each carries a bounded status instead. | `d2f6627` | E8, E5 |

**Fixed as a property, not a path.** Round 4 sanitized the path you showed us
— the withheld error — and left the property false. This round the rule is
stated once and enforced where refusals are made: **no refusal names a URL a
page or server chose.** The clearing step's three refusals now read:

```
... it was cleared [page_not_allowed; cleanup=cleared]
... clearing it failed; nothing from it was read ... [page_not_allowed; error=<class>; cleanup=failed]
... navigation ... was blocked; the page was cleared ... [navigation_blocked; cleanup=cleared]
```

**Two more of the same shape, found by grepping every refusal.** Neither was
in your review: a `fetch_url` redirect off-list named the `Location` the server
sent, and an off-list click-download named its source. Both now carry
`[url_not_allowed; source=redirect]` or `[url_not_allowed; source=download]`.

**The one URL still named** is one the agent asked for itself — `navigate` or
`fetch_url` refusing its own argument — with `source=request`. The agent chose
it, and the refusal is more useful naming it; a test pins that as the positive
control.

**A derivation keeps it that way.** `test_no_refusal_formats_a_url_a_page_or_server_chose`
walks every host refusal in the connector's source and requires each value
formatted into it to come from a fixed set: the error's class, the parts of a
withheld error, and the agent's own `url`. Formatting `current`, `source`, a
`Location`, or anything new fails it until someone looks.

**Your probes, inverted:**

| Your probe | Ours |
|---|---|
| `test_retry_guard_returns_the_url_withheld_by_the_previous_call` | `test_a_call_starting_on_an_off_list_page_does_not_name_it`, clearing succeeding and failing — refused, unread, unnamed |
| `test_two_calls_deliver_the_url_to_the_real_engine_model_loop` | `test_a_retry_after_a_failed_clear_does_not_deliver_the_withheld_url` — the real engine, two tool calls, the retry's clear failing and succeeding; the model's third request and the step output never carry the URL |
| — | `test_a_redirect_off_list_is_refused_without_naming_it`, `test_an_off_list_download_is_refused_without_naming_it`, `test_a_url_the_agent_asked_for_is_still_named` |

**Why our tests missed it.** They stopped after one call, though the refusal
itself tells the model to try again. The new engine test makes the retry.

**In real Chromium.** The closed-page test now makes the retry as well: the
second call is refused without reading or naming the page. The `fetch_url`
redirect test now asserts its refusal does not name the disallowed host.

Every new test was run against the code with its fix removed and failed; each
has a control.

## 2. The contracts, as they now stand

Changed from round 5 are marked **(changed)**.

- **The system layer, file functions, the path matcher,
  `max_tokens_per_call`, browser outputs, uploads, ids** — unchanged.
- **`allowed_hosts`.** It blocks requests the agent initiates before
  dispatch, and checks the top-level page before every read and action.
  - A READ returns only what the checked document gave it — its result or its
    error — whether or not clearing succeeds; the withheld error reports what
    clearing did as one fixed value.
  - An ACTION is checked on every exit but cancellation, judged by where the
    page ends; leaving and returning is not detected; its error after
    navigation is withheld; its effect cannot be undone.
  - **(changed)** No refusal names a URL a page or server chose — where the
    page went, a redirect's target, a download's source — at any call,
    including the retry after a failed clear. Each carries a bounded status.
    A URL the agent asked for is still named.
  - It is neither an egress control nor a content-origin guarantee, and not a
    bound on where an action's effect lands.

## 3. Claims and evidence

As captured at the shipped tree with both opt-in suites enabled, the claim
runner reports 21 claims, 239 test results, every claim reproduced. The
controls harness defines 72 controls, all fire, and every claim has at least
one aimed at its own tests.

Changed this round: **E8** — no page- or server-chosen URL in any refusal,
across the retry, redirects and downloads; the agent's own URL kept; the
derivation. **E5** — the retry after a real failed clear, and the redirect
refusal, in Chromium. The other claims are unchanged.

## 4. Your next acceptance checks

1. **Reached URLs removed from both landing refusals, across their callers.**
   Done, in the refusals themselves; and from the redirect and download
   refusals.
2. **Two-call engine tests, the retry's clear succeeding and failing.** Done.
3. **Round-4 handling, the status, screenshot staging and earlier fixes
   kept.** Yes; every earlier claim and control still runs.
4. **Browser exclusion and the enablement conditions kept; re-captured.**
   Yes. Your recovery recommendation — mark a page that cannot be cleared
   unusable, open a fresh one on the next explicit navigation, never replay
   the failed action — is added to the enablement conditions in
   `docs/NEXT_STEPS.md`.

## 5. Residuals

Unchanged from round 5: browser destinations; where an action's effect lands;
symlinks inside an allowed tree; triggers' own I/O; spend has no platform
ceiling; scaffold drafts approved at run time; the last step's policy stays on
the connector between calls; the image is not deployable as-is; the
navigation matrix and page replacement are enablement conditions, not built.

The status is a readable suffix, not structured fields. Nothing parses it
today; if something comes to, structured fields beside the message are the
better shape, as you noted.

## 6. Live state

From `LIVE_EVIDENCE_G30_R6.txt`, since phase 1 was deployed:

- 88 real `email-triage-apply` runs, all completed; the governed tool was
  called 88 times under the layer and succeeded every time;
- the largest agent-step output was 143 tokens, against the cap;
- memory distillation: 10931 observations recorded, the largest 2196 output
  tokens;
- 5 real `scheduled-health-report` runs, all completed;
- every real DMARC file on disk is admitted, including all 48 the viewer
  holds.

The round-6 code is deployed, and production still has no browser tool.

## 7. How to run

As before. veracium refuses any runtime its `runtime_supported()` does not
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

1. **The rule.** Is "no refusal names a URL a page or server chose; the
   agent's own URL is named" the right line?
2. **The derivation.** Does checking every formatted value against a fixed
   set of names hold the rule, or can a URL reach a refusal some other way?
3. **Anything in §2** that still claims more than the tests show.
