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

**Phrasing rules** (these matter — inconsistent concerns are hard to
aggregate later):
- Short imperative or noun phrases. 3–6 words. Examples below.
- ✅ `"missing description"`, `"add tests"`, `"split into two PRs"`,
  `"verify backward compatibility"`
- ❌ Full sentences with semicolons: `"PR closed without merge; verify
  this was intentional"`
- ❌ Slug-style tags: `"automated-translation-quality"`,
  `"requires-native-speaker-review"`. Use plain prose: `"verify
  translation quality"`.

**The catalog** (pick the right name; don't invent variants):
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
- `from external contributor` — `author_association` is `NONE` or
  `FIRST_TIME_CONTRIBUTOR`. Do NOT flag for `OWNER`, `MEMBER`,
  `COLLABORATOR`, or `CONTRIBUTOR` (those have prior merged PRs). Bot
  accounts (`user.type == "Bot"` or login ending `[bot]`) are machines,
  not external contributors — skip this flag for them.
- `verify <specific thing>` — for narrowly-scoped checks the reviewer
  should perform (e.g. `"verify backward compatibility"`,
  `"verify translation quality"`).

## What is NOT a concern

These are easy to misread as concerns; the rubric explicitly excludes them.

- **PR state (open / closed / merged / draft).** Triage assumes the PR
  was just opened. The `state` field in the payload is for tooling, not
  review. Do not flag `"PR closed without merge"`, `"already merged"`,
  or anything about how old the PR is. We triage the *content*, not the
  workflow status.
- **Bot authorship by itself.** Dependabot, Renovate, release bots,
  translation bots — automated PRs are routine. If a bot PR has a
  problem (large diff, mixed concerns, no description on a non-trivial
  change), flag the problem; don't flag "automated bot PR" as if it
  were the problem.
- **Number of commits.** "Single commit" or "many commits" is a
  squash-strategy detail, not a triage concern.
- **File-count comments alone.** "Three files changed" is data, not a
  concern — only flag if it implies one of the named concerns above
  (e.g. mixed concerns across modules).

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
