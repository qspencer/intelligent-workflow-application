# Reviewer's guide — G30: the capability layer

**Branch:** `main` · **Change:** `17ec01a`..`7eeb80d` · **Asks for:** a review of
one control, both phases, before we call capability enforcement anything more
than what §2 states. You asked for this separately when closing G29.

| | |
|---|---|
| The claims and their evidence | §3 — each claim, the tests that execute it, the controls that prove those tests can fail |
| Run the claims | `docs/review-artifacts/g30_reproductions.py` |
| Run the controls | `docs/review-artifacts/g30_controls.py` |
| Gates, claims and controls as run | `docs/archives/GATE_OUTPUT_G30.txt` |
| What production did under the layer | `docs/archives/LIVE_EVIDENCE_G30.txt` (and the script that produced it) |
| What two passes found before sending | §4 |
| Live state | deployed; real runs under the layer — §6 |

---

## 1. What G30 changes

Your G29 closure asked whether the scaffold's provenance gap reached other
privileged definition fields. Checking it found more than that: **production
ran with no system capability layer at all.** A definition can only narrow the
layers above it, so with nothing above it, a definition — including one drafted
by the scaffold model — could declare itself unrestricted. And two of the five
capability attributes, `allowed_hosts` and `max_tokens_per_call`, were declared
and shown on the canvas but enforced nowhere.

- **Phase 1** — a system layer, tracked in `config/system_capabilities.yaml`,
  derived from what production actually uses; `max_tokens_per_call` and
  `allowed_hosts` enforced.
- **Phase 2** — the deterministic file functions consult capabilities. They
  did not; only `pdf_extract` did.
- **Two passes before sending** — our own, then a cold second-seat red-team.
  They found defects in work we had called done (§4).

## 2. The contracts, stated precisely

These are the claims to attack. Where a sentence is narrower than you might
expect, it is narrow on purpose.

- **The system layer is the outermost bound on every workflow** the service or
  an operator script runs. It loads strictly: every attribute must be stated,
  because an omitted one means unrestricted and a typo would fail open; an
  unreadable configured layer stops startup. A script that needs more than
  production allows names a different reviewed file; nothing runs without one.
- **File functions** check every path against the step's resolved
  capabilities before accessing it, and the path checked is the path opened. A
  denial fails the step; it is never added to a function's "skipped" list,
  which would turn it into a delivery of nothing reported as success.
- **The path matcher** takes absolute paths and patterns only, refuses any path
  with a `..` segment (rather than normalizing it — lexical and OS resolution
  can disagree through a symlink), and matches the whole path. `**` is
  recursive.
- **`max_tokens_per_call`** clamps every model call a workflow drives. Agent
  steps take the most restrictive cap across all layers; question suggestion
  and memory distillation take the system layer's only.
- **`allowed_hosts` is not an egress control.** It blocks requests the AGENT
  initiates before dispatch, and keeps a disallowed host's content from the
  agent. Redirect hops during a navigation, a page's subresources, preconnect
  and WebSockets can still reach a disallowed host. That is why no browser tool
  may enter the system layer until destinations are enforced before dispatch —
  a test refuses it. You raised this when closing G29.
- **Browser outputs** stay in the run's own downloads directory, whatever path
  or name the agent or a server supplies.
- **Workflow and step ids** are plain tokens, and agent memory refuses any path
  outside its directory.

## 3. Claims and evidence

`g30_reproductions.py` runs every test behind every claim and maps each result
back. It runs the TESTS rather than re-implementing them: the G29 package's
script duplicated test logic and printed one figure as a typed string, so
re-running it reproduced a number nobody had computed. As run at the shipped
tree, with both opt-in suites enabled, it reports 18 claims, 141 test results,
every claim reproduced.

| | Claim |
|---|---|
| A1 | The system layer loads strictly; an unreadable configured layer refuses |
| A2 | Every engine — the service and every operator script — carries a layer |
| A3 | Every armed workflow runs under the layer or is declared blocked, checked both ways by the paths each step touches |
| A4 | Health reports the layer as counts, never names |
| B1 | Through the real engine, an unlisted tool is refused — on a file that exists, beside a twin that reads it with no layer |
| B2 | The token clamp covers agent steps, suggestion and distillation |
| C1 | The matcher |
| C2 | File functions check before access; a denial is never a skip |
| C3 | Every access follows a check of that same path in that same mode |
| C4 | A parallel sibling step cannot widen a step's capabilities |
| C5 | The live deterministic workflows run under the shipped layer at their real paths |
| D1 | Ids are tokens; memory is contained; import refuses a traversal id |
| D3 | A stored released vocabulary is refused on repository load — real Postgres, as your G29 closure asked |
| E1 | Agent-initiated browser requests are blocked before dispatch; off-list content never reaches the agent |
| E2 | URLs the host check and the browser could parse differently are refused |
| E3 | Browser outputs are confined |
| E4 | The browser stays out of the system layer |
| E5 | Against real Chromium: off-list clicks and navigations send nothing, the redirect residual is measured, the backslash smuggle is refused |

**Controls.** `g30_controls.py` plants, for each detector, the defect it claims
to catch, and requires it to fail and then pass once restored: 35 controls, all
fire. Every claim has at least one aimed at its own tests. Two of those need an
opt-in suite and are reported NOT RUN when it is absent, never as passing.

Two points about the controls themselves:

- **A whole-file control is not counted against a claim.** It fires when any
  test in that file fails. The first version of the claim map credited one
  control to every claim with a test in its file, which read as several.
- **The backslash defence is two independent layers.** Either one alone stops
  the red-team's URL, because that smuggle needs userinfo to make Python read a
  different host. Removing only the backslash clause therefore fired nothing;
  the E5 control removes the whole differential defence.

## 4. What two passes found before this reached you

**Our pass** re-derived every population rather than trusting the ones already
gated, and executed the documents' claims:

| Finding | Status |
|---|---|
| Workflow and step ids became memory paths unconstrained: an imported definition with id `../../x` loaded any readable `.md` file into an agent's prompt | fixed (D1) — read-only, localhost-only here, unreachable via scaffold |
| Browser outputs escaped their directory — an ABSOLUTE `dest_filename` replaced it outright | fixed (E3) — unreachable here, browser off |
| Operator scripts, `fire.py` among them, built engines with no system layer | fixed (A2) |
| `max_tokens_per_call` overclaimed | fixed (B2) |

**A second seat** — another session, given the code and the documents' claims
but not our concerns, so it could not be anchored on them — reported that
nothing got past the shipped system layer itself. What broke was underneath it:

| Finding | Why our pass missed it |
|---|---|
| A parallel sibling undid a step's narrowing — capabilities lived on the SHARED context | every phase-2 test ran one step at a time |
| The token cap missed question suggestion, whose size a draft sets | we read that call and called it "bounded by its config" — the config is bounded only below |
| `fetch_url` reached a disallowed host through a parser differential: `urlsplit` reads `localhost:P\@127.0.0.1` as `127.0.0.1`, WHATWG ends the host at the backslash | we tested hostile URLs through one parser; the bypass lives between two |
| Our "derived from source" guard checked presence, not correctness — a mutant checking the directory instead of the file passed | we believed our own test |
| The threat model overstated spend bounds | a sentence we did not re-execute |

Then it re-checked the fixes: all held, including neighbours we had not tried,
with positive controls proving the URL filter does not refuse everything. Two
leftovers — a weaker mutant that survived because every test input was one
file, and the spend claim repeated in a second section — are closed.

## 5. Residuals, stated rather than claimed away

1. **Browser destinations.** §2. Unreachable in production: no browser tool is
   in the layer, and a test refuses one.
2. **Symlinks.** A symlink inside an allowed tree that points out of it is
   followed by the OS; the check is on the path. We found none in any live
   tree, and no stock function creates one.
3. **Triggers do their own I/O** from operator config. No API path registers a
   trigger — we executed that claim and it holds — so a draft's trigger config
   never runs.
4. **Spend.** The only platform ceiling is per-call output. Step and workflow
   budgets are author-set, and the workflow budget defaults to none. A
   platform-wide ceiling is a recorded follow-up.
5. **Scaffold drafts are approved at run time, not field by field.** A drafted
   workflow's capabilities can now only narrow the system layer, so the
   approval gap you named for G29 no longer reaches capabilities. It still
   reaches fields that are not capabilities.

## 6. Live state

From `docs/archives/LIVE_EVIDENCE_G30.txt` (a reviewer cannot re-run it; it
ships so the queries are on the record). Since phase 1 was deployed:

- 18 real `email-triage-apply` runs, all completed; the one governed tool was
  called 18 times under the layer and succeeded every time;
- the largest agent-step output was 121 tokens, against the cap;
- memory distillation: 10861 observations recorded, the largest 2196 output
  tokens — so the cap truncates none of them;
- every real DMARC file on disk is admitted by the layer, including all 48 the
  viewer holds.

**Not yet observed live:** a real `scheduled-health-report` run writing its
moved log — each deployment reset its hourly timer — and a real DMARC delivery,
which waits on a report email. Both workflows run end to end under the shipped
layer at their real paths in the suite (C5).

## 7. How to run

From `backend/`, in the locked environment (`uv sync`):

```
uv run python ../docs/review-artifacts/g30_reproductions.py
uv run python ../docs/review-artifacts/g30_controls.py     # edits sources in place, restores each
```

For the opt-in claims and controls add `BROWSER_LIVE=1` (real Chromium:
`uv run playwright install chromium`) and `TEST_DATABASE_URL` naming a Postgres
database whose tables you can lose — the fixtures drop them.

## 8. What we most want from you

1. **§2's contracts.** In particular: is "outermost bound on every workflow"
   true of everything that runs a workflow, now that operator scripts carry the
   layer? We derived the engine constructions; we may not have derived every
   way to run a step.
2. **The token clamp's scope.** Suggestion and distillation take the system
   cap, not a workflow's narrower one. Is that the right line?
3. **The residuals.** Is any of §5 reachable in a way we have not seen?
