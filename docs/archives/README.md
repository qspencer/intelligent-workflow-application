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

## Manifest

| Archive | SHA | Date | For | Cover note |
|---|---|---|---|---|
| `trace-governance-review-e397b8b.tar.gz` | `e397b8b` | 2026-08-02 | External **code** review of the trace-governance build (TG1–TG3d-1 + gate-wiring; Contract A + B1) | `docs/TRACE_CODE_REVIEW_GUIDE.md` |
