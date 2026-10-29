# Reviewer's guide — G30 round 2: the four findings, closed

**Branch:** `main` · **Answers:** your round-1 review of `a7284c0` (request
changes; text kept verbatim in `docs/TRACE_G30_REVIEW_RESULTS.md`) ·
**Change since round 1:** `a7284c0`..this package. Source last changed at
`8a0a86c`; later commits are documents and tests.

| | |
|---|---|
| What changed, finding by finding | §1 |
| Your acceptance conditions, one by one | §4 |
| Run the claims | `docs/review-artifacts/g30_reproductions.py` |
| Run the controls | `docs/review-artifacts/g30_controls.py` |
| Gates, claims and controls as run | `docs/archives/GATE_OUTPUT_G30_R2.txt` |
| What production did under the layer | `docs/archives/LIVE_EVIDENCE_G30_R2.txt` |
| **The runtime the suite needs** | §7 — read before running; it explains the failures you saw |

---

## 1. What round 1 found, and what changed

| Finding | Fix | Commit | Claim |
|---|---|---|---|
| **R1 (P1)** — the service ran without a system layer on every launch path but `run-local-be.sh`; the image could not carry the file | One loader for the service and every operator script, never `None`: unset → the shipped layer; set → strict load; set but empty → refused. The image is built from the repo root with an allowlist `.dockerignore` and carries the layer where the service looks for it. | `fb00ae2` | A1, A2 |
| **R2** — a parallel step replaced another's browser host policy mid-redirect | `BrowserConnector.operation(policy)`: one lock per connector, held from installing the step's policy through the action, every redirect hop and the landing check. Every browser tool call runs inside one. | `8a0a86c` | E6 |
| **R3** — every run wrote browser outputs to one shared directory | Each instance gets `<root>/<instance_id>`. | `0d6ea81` | E3 |
| **R4** — browser uploads ignored `file_read` | The upload tool checks `file_read` on the exact path before Playwright sees it, and refuses directories. | `0d6ea81` | E7 |
| Content claim — "nothing a disallowed host returns reaches the agent" | Withdrawn and narrowed (§2). | `8a0a86c` | E1 |

Every new test was run against the code with its fix removed and failed;
each has a control in the harness that repeats that on every run. Your probes
are inverted in the suite — they asserted the defect, ours assert it is
refused:

| Your probe | Ours |
|---|---|
| `test_service_default_can_run_without_the_system_layer` | `test_the_service_default_refuses_an_unlisted_tool_on_a_real_file` — the service's own engine, nothing set, a real file, only the model faked |
| `test_host_policy_race_through_parallel_engine_steps` | `test_a_parallel_sibling_cannot_widen_a_step_host_policy_mid_call`, beside `test_the_serial_twin_refuses_the_same_redirect` |
| `test_sibling_tool_call_replaces_inflight_host_policy` | covered by the same engine test; the lock is what both exercised |
| `test_browser_outputs_share_one_directory_across_real_engine_runs` | `test_two_runs_saving_the_same_name_keep_both_outputs` — default construction path, only the page faked |
| `test_browser_upload_does_not_consult_file_read` | `test_upload_outside_file_read_never_reaches_the_page`, with an allowed twin and a directory refusal |

One adaptation you should know about. Your engine probe held the first
response until the sibling FINISHED. With the lock, the sibling cannot start
until the held call ends, so that probe deadlocks rather than passing. Ours
holds the response for a bounded time instead: without the fix the sibling
acts in that window and the redirect is followed; with it, the sibling is
blocked and the redirect is judged under the narrow step's own policy.

## 2. The contracts, as they now stand

Changed from round 1 are marked **(changed)**.

- **The system layer bounds every workflow** the service runs, on every
  launch path, and every operator script. **(changed)** Nothing named means
  the shipped layer; there is no unrestricted service mode. A layer that is
  named but empty or unreadable stops startup.
- **File functions and the path matcher** — unchanged from round 1.
- **`max_tokens_per_call`** — unchanged, and stated as you suggested: on a
  workflow or step it is an agent-step setting (the most restrictive layer
  wins); question suggestion and memory distillation take the system cap. It
  is a per-call output ceiling, not a cost ceiling.
- **`allowed_hosts` (changed).** It blocks requests the agent initiates before
  dispatch, and the agent reads or acts only while the TOP-LEVEL page is on an
  allowed host; `fetch_url` and downloads save nothing from a disallowed
  host. It is neither an egress control nor a content-origin guarantee: an
  allowed page can render a disallowed host's images, scripts or frames, and
  the agent reads what they put there. A step's policy holds for its whole
  tool call, beside parallel steps sharing the page.
- **Browser outputs (changed)** stay in the run's OWN directory — one per
  instance — whatever path or name is supplied. Resume and retry keep the
  instance and its directory; a fork is a new instance with a new one.
- **Browser uploads (new)** are `file_read`s.
- **Ids** — unchanged.

## 3. Claims and evidence

As captured at the shipped tree with both opt-in suites enabled, the claim
runner reports 20 claims, 156 test results, every claim reproduced. The
controls harness defines 42 controls, all fire, and every claim has at least
one aimed at its own tests.

| | Claim | Round 2 |
|---|---|---|
| A1 | The layer loads strictly; unset is the shipped layer, empty or unreadable refuses | changed (R1) |
| A2 | Every engine carries a layer — the real service built with nothing set refuses an unlisted tool on a real file; the image carries the layer | changed (R1) |
| A3 | Every armed workflow is accounted for, both ways | |
| A4 | Health reports counts, never names | |
| B1 | An unlisted tool is refused through the real engine | |
| B2 | The token clamp covers agent steps, suggestion and distillation | |
| C1–C5 | Matcher; file functions; check-before-access; parallel context; live workflows | |
| D1, D3 | Ids are tokens; a stored released vocabulary refuses on Postgres load | |
| E1 | Agent-initiated requests blocked before dispatch; the agent acts only on an allowed top-level page | changed (wording) |
| E2 | URLs the two parsers could read differently are refused | |
| E3 | Outputs stay in the run's own directory; a second run cannot overwrite the first's | changed (R3) |
| E4 | The browser stays out of the system layer | |
| E5 | Against real Chromium | |
| E6 | A step's host policy holds for its whole call beside a parallel sibling | new (R2) |
| E7 | Uploads obey `file_read` | new (R4) |

## 4. Your acceptance conditions

1. **R1 with runtime startup tests and deployment packaging.** Done. The
   tests construct the real service with the variable absent, empty, valid
   and unreadable; none checks keyword presence. The A2 source check now
   requires the VALUE to be the never-`None` loader. We built the image and
   ran it once: the layer loaded, and a missing named layer exited non-zero.
   That image run is not in the capture — a test pins the layout it depends on.
2. **R2 through all awaits and route callbacks, via the real parallel
   engine.** Done (E6). The route callback reads the policy while the action
   runs, so the lock spans the whole call, not one check. Between calls the
   last policy stays installed — see §5.
3. **R3 with instance-bound directories and a two-run test.** Done (E3).
4. **R4 with an allowed control.** Done (E7).
5. **Browser stays excluded.** Kept, and its test still refuses a browser
   tool in the layer. Destination enforcement and provenance-aware content
   claims remain open.
6. **D3/E5 and their controls in the capture; the full suite on the qualified
   SQLite.** All in `GATE_OUTPUT_G30_R2.txt`. The runtime is in §7.

## 5. Residuals

Carried from round 1 and still true: browser destinations (no browser tool
may enter the layer); symlinks inside an allowed tree; triggers' own I/O;
spend has no platform ceiling; scaffold drafts are approved at run time, not
field by field.

New this round:

- **Between browser calls**, the last step's policy stays on the connector.
  A navigation the page starts on its own at that moment is judged by it; the
  next call's landing check then judges where the page sits under that call's
  own policy, and clears it if needed. No agent read happens in between.
- **The backend image is not yet deployable as-is**: its SQLite is not
  veracium's qualified build, so learned memory cannot open in it, and it is
  not built from `uv.lock`. Neither touches capabilities; both are recorded
  follow-ups. Production does not run from the image.

## 6. Live state

From `LIVE_EVIDENCE_G30_R2.txt`, since phase 1 was deployed:

- 52 real `email-triage-apply` runs, all completed; the governed tool was
  called 52 times under the layer and succeeded every time;
- the largest agent-step output was 143 tokens, against the cap;
- memory distillation: 10895 observations recorded, the largest 2196 output
  tokens;
- 2 real `scheduled-health-report` runs, both completed, and the moved log
  now exists — round 1 could only say it had not been observed;
- every real DMARC file on disk is admitted, including all 48 the viewer
  holds. A real delivery still waits on a report email.

## 7. How to run — the runtime matters

**veracium refuses to open its store on any SQLite but the one build it
qualifies,** `3.45.1`. uv's own managed Python bundles a newer one — your
run had `3.53.1` — and on it the learned-memory store refuses at open with
`StoreVersionError: unsupported-sqlite`. That is the cause of all 14 failures
in your full-suite run, every one in `tests/test_learned_memory.py` or the
real-store health test in `tests/test_schema_drift.py`. It is veracium
refusing an unqualified runtime, not a G30 behaviour, and no G30 claim depends
on those tests. We should have said this in round 1.

To get the qualified runtime: a Python `3.14` that links the SYSTEM SQLite on
`ubuntu-24.04`, which is `3.45.1`. That is what CI uses (`actions/setup-python`
with `UV_PYTHON_PREFERENCE: only-system`). Check with:

```
python -c "import sqlite3; print(sqlite3.sqlite_version)"   # must print 3.45.1
```

Then, from `backend/`:

```
UV_PYTHON=<that python> uv sync --frozen
uv run python ../docs/review-artifacts/g30_reproductions.py
uv run python ../docs/review-artifacts/g30_controls.py     # edits sources in place, restores each
```

Opt-in: `BROWSER_LIVE=1` with Chromium installed (`uv run playwright install
chromium`; your round-1 download failed part-way — a retry is worth it, since
E5 is the only real-browser evidence), and `TEST_DATABASE_URL` naming a
Postgres database whose tables you can lose, for D3.

## 8. What we most want from you

1. **R2's design.** Is a per-connector lock across the whole call the right
   ownership, given the between-calls residual in §5? The alternative is a
   connector per step, which would make parallel browser steps truly
   independent but change cookie and session sharing.
2. **R1's shape.** We chose "unset means the shipped layer" over "unset
   refuses to start". The first keeps every documented launch command
   working; the second makes the choice explicit. Either fails closed.
3. **Anything in §2** that still claims more than the tests show.
