# Deployment baseline (local systemd service)

The backend runs as the `workflow-be` **systemd --user** service, launched by
`scripts/run-local-be.sh` from this repo's working tree. Restart/logs go through
`systemctl --user` / `journalctl --user` (see
[[project_backend_systemd_service]]).

## The deployed code IS the checked-out branch

`run-local-be.sh` starts `uvicorn --reload`, which watches `src/`, so **the
deployed code is whatever branch is checked out in this repo directory.** The
service **must** run from this directory — the `.secrets/` (Gmail creds),
`.memory/` (learned memory), and `~/.config/workflow-be.env` it needs are
gitignored and are not in a git worktree. So decoupling dev from prod via a
separate worktree is **not** viable; the running code and the dev checkout are
the same tree.

Consequence: a stray `git checkout <other-branch>` silently changes the deployed
code on the next `--reload`. To make that visible rather than surprising, the
pre-flight now logs `deployed code: branch '<x>' @ <sha>` on every start — grep
the journal for it to confirm what's actually running.

## Current baseline (2026-08-09): `p1-reprimitive`, deliberately

The service runs **`p1-reprimitive`**. This branch carries the trace re-primitive
whose Contract-B1 work is **deferred** (see `TRACE_B1_DEFERRAL.md`), but its
read-surface projection is materially better than `main`'s, so it is the chosen
deployed baseline. The B1-specific commits within it are the deferred *record*;
the branch itself continues as the working/deployed branch.

`main` and `p1-reprimitive` share the same DB schema (same 11 Alembic
migrations, same vault tables), so switching between them is schema-safe — the
only difference is projector code.

## Working discipline (until a proper CI/CD or worktree story exists)

- **Do ongoing product work on `p1-reprimitive`** (the deployed branch) so the
  checkout does not switch under the live service. Additive product commits are
  fine; they just also become "deployed."
- **If you must switch branches** on this box, `systemctl --user restart
  workflow-be` afterwards so the deployed state is intentional, and confirm the
  `deployed code:` log line shows what you expect.
- **`main` is the clean, B1-free baseline.** If a real deploy target ever exists,
  land reviewed product work there; keep the deferred trace work on
  `p1-reprimitive` only.
