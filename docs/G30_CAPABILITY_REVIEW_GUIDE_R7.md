# Reviewer's guide — G30 round 7: the default inverted

**Branch:** `main` · **Answers:** your round-6 review of `fdf6ed3` (request
changes; text kept verbatim in `docs/TRACE_G30_R6_REVIEW_RESULTS.md`) ·
**Change since round 6:** `fdf6ed3`..this package. Source last changed at
`a614de1`; later commits are documents and tests.

| | |
|---|---|
| What changed | §1 |
| Your next acceptance checks, one by one | §4 |
| Run the claims | `docs/review-artifacts/g30_reproductions.py` |
| Run the controls | `docs/review-artifacts/g30_controls.py` |
| Gates, claims and controls as run | `docs/archives/GATE_OUTPUT_G30_R7.txt` |
| What production did under the layer | `docs/archives/LIVE_EVIDENCE_G30_R7.txt` |
| The runtime the suite needs | §7 |

---

## 1. What round 6 found, and what changed

| Finding | Fix | Commit | Claim |
|---|---|---|---|
| **R6-F1 (P2)** — a truncated off-list redirect made Playwright's HTTP client raise with a call log quoting the off-list `Location`; a malformed `Location` made the URL parser raise with the server's text; the tool passed both to the model | The default inverted at one boundary; the fetch boundary fixed as you asked; the host check made total | `a614de1` | E8 |

**Why the default, not one more exit.** Each round since the third found one more way
for text from an unchecked page or server to reach the agent: a read's error,
a failed clear, the retry, and now a library's transport error. We had been
securing the errors we build and assuming the rest carried nothing. So the
rule is now enforced as a default, at the one place a browser tool turns an
exception into what the agent sees (`tools/browser.py`, `_error`):

> Under a host policy, an error's TEXT reaches the agent only if this
> application built it, or the fence vouched it is about the checked,
> unchanged page. Any other error — a library's, a parser's, one nobody
> anticipated — arrives as its class: `[call_failed; error=<class>]`.

- **Built:** refusals (`BrowserHostDenied`, `BrowserPageNotCleared`) and the
  new `BrowserCallFailed` share a base, `BrowserBoundedError`; the upload
  tool's own refusals also pass.
- **Vouched:** the fence marks an error about the checked, unchanged page —
  the one kind the agent needs whole, to fix a selector — on exactly that
  path. Unmarked, it would be reduced like any other.
- **Unrestricted runs** are unchanged.

**The specific fix too, so the connector does not rely on the tool.**
`fetch_url` reduces a transport failure to `BrowserCallFailed` at the fetch
boundary; an unparseable redirect `Location` is refused like an off-list one
(`source=redirect`); `url_host_allowed` returns False rather than raising on a
URL Python cannot parse.

**On Playwright's real HTTP client.** `tests/test_browser_fetch_transport.py`
adapts your harness: the actual `APIRequestContext` against a loopback server
that misbehaves on purpose — no Chromium needed, so it runs in every CI build.
The truncated redirect and the malformed `Location` each fail before the fix
and pass after, beside the complete off-list redirect (refused) and a redirect
to an allowed host (fetched), and through the real engine for both failures.

**Your probes, inverted:**

| Your probe | Ours |
|---|---|
| `test_real_transport_outcomes` (truncated, malformed, complete, allowed) | `test_a_truncated_redirect_does_not_deliver_its_location`, `test_a_malformed_location_is_refused_without_its_text`, `test_a_complete_off_list_redirect_is_refused`, `test_a_redirect_to_an_allowed_host_is_fetched`; and `test_the_connector_itself_withholds_a_transport_error` below the tool |
| `test_transport_diagnostic_through_real_engine` | `test_the_model_never_sees_a_transport_or_parse_error`, both failures |
| `test_formatted_name_guard_does_not_check_dataflow_or_concatenation` | `test_the_tripwire_rejects_the_shapes_the_reviewer_showed_passing` — your three snippets, each now rejected |

**The default, tested without recognising any error string.**
`test_an_unanticipated_error_reaches_the_agent_as_its_class_only` raises an
arbitrary error carrying a server-chosen URL from a navigation; the agent sees
its class. Its twins: the same error unchanged without a policy, and a
strict-mode error on the checked page still passed whole.

**The static checks, at their real strength.** You were right that the
round-5 "derivation" checked spelling, not origin. They are now called
tripwires, and stricter:

- every host refusal is built from literal text — no concatenation, no
  variable — formatting only names from a fixed set, and the agent's `url`
  is never rebound in the connector modules;
- every `except Exception` in the browser tools returns through `_error`, and
  an exception is formatted into text nowhere else.

They are not data-flow analysis. What a tripwire cannot see, the boundary
withholds anyway — that is the point of inverting the default.

**Checked and not kept.** We tried the same disclosure at the navigate path in
real Chromium: an allowed URL redirecting to an off-list host that refuses the
connection. Playwright's `goto` error (`1.63`) names only the requested URL there,
so a test could not fail; we dropped it rather than claim it.

Every new test was run against the code with its fix removed and failed; each
has a control.

## 2. The contracts, as they now stand

Changed from round 6 are marked **(changed)**.

- **The system layer, file functions, the path matcher,
  `max_tokens_per_call`, browser outputs, uploads, ids** — unchanged.
- **`allowed_hosts`.** It blocks requests the agent initiates before
  dispatch, and checks the top-level page before every read and action.
  - Reads, actions, failed clears and retries — unchanged from round 6.
  - Refusals this application builds name no URL a page or server chose;
    only a URL the agent asked for.
  - **(changed)** Under a policy, an error reaches the agent with its text
    only if this application built it or the fence vouched it is about the
    checked, unchanged page; any other error arrives as its class. A vouched
    error can quote the checked page, URLs in it included: this is not a
    secrecy guarantee for URLs, only that no error text reaches the agent
    from a page or server it did not check.
  - It is neither an egress control nor a content-origin guarantee, and not a
    bound on where an action's effect lands.

## 3. Claims and evidence

As captured at the shipped tree with both opt-in suites enabled, the claim
runner reports 21 claims, 254 test results, every claim reproduced. The
controls harness defines 79 controls, all fire, and every claim has at least
one aimed at its own tests.

Changed this round: **E8** — the boundary default, the real-transport cases,
the total host check, and both tripwires with the self-test. The other claims
are unchanged.

## 4. Your next acceptance checks

1. **Raw fetch transport and parsing diagnostics withheld, including failures
   before a response object exists.** Done, at the fetch boundary and again
   at the tool boundary.
2. **Marker absence at the tool and engine boundaries for incomplete
   redirects and malformed Locations, with the twins.** Done, on the real
   HTTP client.
3. **Earlier repairs kept.** Yes; every earlier claim and control still runs.
4. **The static test described at its actual strength; the universal wording
   reconciled with the error boundary.** Done (§1, §2).
5. **Browser exclusion and enablement conditions kept; re-captured.** Yes.

## 5. Residuals

Unchanged from round 6: browser destinations; where an action's effect lands;
symlinks inside an allowed tree; triggers' own I/O; spend has no platform
ceiling; scaffold drafts approved at run time; the last step's policy stays on
the connector between calls; the image is not deployable as-is; the
navigation matrix and page replacement are enablement conditions, not built.

New this round: **a reduced error is less useful.** Under a policy, a
navigation that fails for an ordinary reason (a name that does not resolve)
now reaches the agent as its class, without Chromium's `net::` code. We chose
not to extract that code from the text: a server can put characters in what
the extraction reads.

## 6. Live state

From `LIVE_EVIDENCE_G30_R7.txt`, since phase 1 was deployed:

- 92 real `email-triage-apply` runs, all completed; the governed tool was
  called 92 times under the layer and succeeded every time;
- the largest agent-step output was 143 tokens, against the cap;
- memory distillation: 10935 observations recorded, the largest 2196 output
  tokens;
- 6 real `scheduled-health-report` runs, all completed;
- every real DMARC file on disk is admitted, including all 48 the viewer
  holds.

The round-7 code is deployed, and production still has no browser tool.

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

The new real-transport tests need no opt-in. Opt-in: `BROWSER_LIVE=1` with
Chromium, and `TEST_DATABASE_URL` naming a disposable Postgres database, for
D3.

## 8. What we most want from you

1. **The default.** Is "built or vouched, else its class" the right
   boundary, and is the tool layer the right place for it?
2. **The vouching.** The fence vouches for an error only on the checked,
   unchanged-document path. Is there another error the agent needs whole that
   this now withholds?
3. **Anything in §2** that still claims more than the tests show.
