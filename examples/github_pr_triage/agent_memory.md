# GitHub PR Triage — agent memory

Loaded by the engine into the `triage` step's system prompt under
"Prior agent memory". Edit freely; structured Markdown is the storage
format. Memory edits change behavior — the audit log records the new
`memory_hash` so changes are correlatable with output drift.

## Categories

| category | What to look for |
|---|---|
| `bug_fix` | Title or body says "fix", "fixes", "closes" with an issue ref. Touches code, not docs. Small or medium typically. |
| `feature` | Adds new capability, API surface, or user-visible behavior. Often labeled "feat" or has "implement" / "add" in the title. |
| `refactor` | Rearranges code without changing behavior. Body usually mentions "refactor", "cleanup", "simplify". No new public API. |
| `docs` | README, comments, type stubs, ADRs only. Diffs touch `.md` / `.rst` / docstrings. |
| `chore` | Build config, CI, lint rules, dev tooling. Not user-visible. |
| `dependency` | Touches `package.json` / `requirements.txt` / `go.mod` / `Cargo.toml` etc.; often opened by Dependabot / Renovate. |
| `other` | Doesn't fit above. Mixed concerns also go here — flag in `concerns`. |

If a PR genuinely spans categories (e.g. a feature that includes a
refactor and dependency bump), pick the *load-bearing* category for the
review experience and add a concern noting the mix.

## Complexity scale

Sized to reviewer attention, not raw LOC:

| complexity | Signal |
|---|---|
| `trivial` | < 10 lines changed, single file, obviously safe (typo fix, version bump). |
| `small` | < 100 lines, < 3 files. Reviewable in one sitting. |
| `medium` | < 500 lines, < 10 files. Needs a focused review session. |
| `large` | < 2000 lines, < 30 files. Should probably have been split. |
| `gigantic` | Anything larger. Add a concern about reviewability regardless of category. |

Documentation-only diffs default one tier smaller than line counts
suggest (prose is faster to review than code).

## Concerns to flag

Pick the ones that apply; don't list every possibility. Empty list is a
valid answer for a perfect PR.

- `missing description` — body empty or one-line "fix bug" with no context.
- `no linked issue` — body doesn't reference a tracking issue or
  conversation. Acceptable for tiny PRs; flag for medium+.
- `large diff` — exceeds the `large` threshold above. Mention even when
  the category warrants size (e.g. a real feature).
- `no tests, code change` — touches `*.py` / `*.ts` / `*.go` outside
  `tests/` directory but no test file is in the diff. Doesn't apply to
  `docs`, `chore`, `dependency`.
- `touches load-bearing files` — modifies migrations, auth, billing,
  CI workflows, or anything named `main.*` / `app.*` / `__init__.py`.
  Worth a human eyeball even if otherwise clean.
- `mixed concerns` — refactor + feature + dependency bump in one PR.
  Reviewer can't easily separate "is this safe?" from "is this right?"
- `from external contributor` — `user.login` is not a recognized
  maintainer. Heightens the bar on the other checks.

## needs_tests heuristic

True if **all** of:
- category in (`bug_fix`, `feature`, `refactor`)
- the diff touches non-test source files (`.py`, `.ts`, `.go`, etc.)
- no test file (`test_*`, `*.spec.*`, `*_test.*`) is in the changed files

False for `docs`, `chore`, `dependency`, and `other` categories — those
don't usually need tests.

## Output discipline

- Respond with **only** the JSON object. No prose, no fences.
- `summary`: one short sentence describing what the PR does. Don't
  restate the title verbatim.
- `concerns`: short imperative strings ("add tests", "split into two PRs",
  "describe the why"). Don't write paragraphs.
- If the PR is genuinely empty / corrupt / spam, return `category: "other"`,
  `complexity: "trivial"`, `concerns: ["unable to interpret PR contents"]`.

## What this rubric is NOT for

- Code-quality review (style, naming, perf). The agent only sees
  metadata, not the diff body. Don't speculate about code you can't see.
- Approval / merge decisions. The output is advisory; humans approve.
- Security scanning. We have other tools for that; flagging "looks
  suspicious" without specifics is noise.
