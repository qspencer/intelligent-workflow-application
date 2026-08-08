# Code-review archives

Secret-clean, reproducible snapshots of the repo cut for external reviewers.

The tarballs themselves are **gitignored** (see `.gitignore`) — they're binary,
large, and exactly reproducible from their commit SHA, so committing them would
bloat the repo for no benefit. This README is the durable record; the archive
is a build artifact you regenerate on demand.

## Why `git archive` (not a folder copy)

`git archive <sha>` emits **only tracked files at that commit**. Everything
sensitive — `.secrets/`, `.env`, `~/.config/workflow-be.env`, refresh tokens,
`.venv` — is gitignored and therefore **cannot** be in the archive. A plain
`tar`/`cp` of the working tree can leak those; `git archive` cannot. Always use
`git archive` for a handoff.

## Regenerate an archive

```sh
# reproducible (gzip -n drops the timestamp → identical bytes for a given SHA)
SHA=$(git rev-parse --short HEAD)
git archive --format=tar "$SHA" | gzip -n > docs/archives/<topic>-review-$SHA.tar.gz
```

Naming: `<topic>-review-<short-sha>.tar.gz`. Verify secret-cleanliness with
`tar tzf <file> | grep -iE '\.secrets|\.env$|refresh_token|\.venv'` (expect
nothing but the `secrets/` **source module**, which is code, not credentials).

## Two kinds of review

**Code** reviews (`trace-governance-review-*`) ask whether the implementation
holds. **Design** reviews (`trace-1.4a-design-review-*`) ask whether an unbuilt
spec is sound. Both ship the whole repo — design reviewers have repeatedly found
blocking issues by reading code the spec described inaccurately.

## Manifest

| Archive | SHA | Date | For | Cover note |
|---|---|---|---|---|
| `trace-1.4a-design-review-r5-0b6e748.tar.gz` | `0b6e748` | 2026-08-08 | **§1.4a DESIGN review, round 5** — after folding round 4's four blockers (upstream semantic closure · completion-time revocation fence · audit-vs-scrub · approval expiry) + both contract corrections | `docs/TRACE_1_4A_REVIEW_ROUND5.md` |
| `trace-1.4a-design-review-r4-90dee87.tar.gz` | `90dee87` | 2026-08-08 | **§1.4a DESIGN review, round 4** — the per-workflow declassification approval. Whole repo, not docs-only: round 3's blocking finding came from reading `engine/functions.py`. Nothing in §1.4a is built | `docs/TRACE_1_4A_REVIEW_ROUND4.md` |
| `trace-governance-review-e397b8b.tar.gz` | `e397b8b` | 2026-08-02 | External **code** review of the trace-governance build (TG1–TG3d-1 + gate-wiring; Contract A + B1) | `docs/TRACE_CODE_REVIEW_GUIDE.md` |
| `trace-governance-review-0c847fa.tar.gz` | `0c847fa` | 2026-08-03 | **Round 3** — after the four build-conformance primitives (P1 typed projector, P2 surface inventory, P4 grant+vault CAS, P3a rehydration predicate); code at `29a42f5`, guide refreshed at `0c847fa` | `docs/TRACE_CODE_REVIEW_GUIDE.md` |
| `trace-governance-review-2cfacfc.tar.gz` | `2cfacfc` | 2026-08-02 | **Re-review** after remediating all 10 findings from the `e397b8b` review (code fixes at `5e0d84b`; see `docs/NEXT_STEPS.md` G-Trace-Review + `backend/tests/test_trace_review_fixes.py`) | `docs/TRACE_CODE_REVIEW_GUIDE.md` |

## Integrity hashes (round-5 design package)

Published so the reviewer can confirm they received what was sent — round 4's
reviewer verified by SHA-256 unprompted, so it is worth pre-supplying.

```
archive  f20322dd34753c6c2e7e357dcd3378b52b12819162547709007a47ed8d587a3a
         trace-1.4a-design-review-r5-0b6e748.tar.gz

docs, as archived:
  ade602b282c2e0e369c7da5506245689d80bf5105ecf244eb73c84315c1f803f  TRACE_1_4A_REVIEW_ROUND5.md
  29f6612cb53036f712edbf99f7220ff46183b52dd418c37f8d79f3bbab046da8  TRACE_GOVERNANCE_PLAN.md
  ff37a8834d9831a4c19c6e366043904f642e759fc1eff09a4720caaa1d4fc7a6  THREAT_MODEL.md
  e658c01907cd7fdb6985d6cad1a761213abc0832656a25706d3404ba9bae75c4  EXECUTION_SEMANTICS.md
```

**The two companion hashes are byte-identical to the ones round 4 reported**
(`ff37a883…`, `e658c019…`), so `THREAT_MODEL.md` and `EXECUTION_SEMANTICS.md` are
provably unchanged since that review and need no re-reading. Only the handoff and
the governance plan moved.
