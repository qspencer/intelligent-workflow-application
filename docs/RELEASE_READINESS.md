# Release Readiness audit

A ship/block risk lens for "is this ready to deploy?" — distinct from line-level review
(`/code-review`) and line-level security (`/security-review`). It runs from **local evidence
only**: no uploading the repo to an external scanner, no `npx <pkg>@latest` as the audit path.
Distilled from `affaan-m/ECC`'s `production-audit` (MIT), recast for our surface.

Use it before a deploy, a demo, or a customer/investor walkthrough — or any time CI is green
but you want *production risk*, not test status. Green CI is not readiness.

## Evidence first (cheap, local)

```bash
git status --short --branch
git log --oneline --decorate -20
git diff --stat origin/main...HEAD
```
Then inspect the surface that actually exists in our repo:
- Trigger entry points: filesystem / webhook / schedule / Gmail-poll; the `TriggerOrchestrator`.
- Auth + capability spine: OIDC/dev-mode, RBAC `require_roles`, the capability intersection.
- Data: Alembic migrations, the Postgres repositories, the audit log.
- Connectors + secrets: `.secrets/` perms, `SecretStore` wiring, Bedrock creds.
- Ops: `/metrics`, structured logs, `infra/` Terraform, the manual-testing playbooks.
- Cost: budget enforcement (`max_total_tokens`, `budget_action`) on the workflows in scope.

## Risk lenses

### Agent / LLM surface (our differentiator — check it first)
- Does untrusted content (a PDF, an email body, a scraped page, a webhook payload) ever reach
  a privileged tool call **without** a capability gate between it and the action?
- Are step capabilities the *intersection* (system → workflow → step → runtime), so a prompt
  can't talk the agent into a tool it was never granted?
- Is every tool call in the audit log, with the `memory_hash` in effect?
- Is `budget_action` set so a runaway agent **pauses/escalates** rather than burning unbounded?

### Security & auth
- Public vs authenticated routes clearly separated; auth enforced server-side (middleware +
  `require_roles`), not just hidden in the UI.
- Secrets out of client bundles, logs, example output, and committed files (`.secrets/` 0600,
  gitignored).
- **Our known gap:** the webhook trigger endpoint still needs HMAC verification before it's
  exposed publicly — treat an unverified public webhook as a blocker (see below).

### Data integrity
- Migrations run forward cleanly **and** have a rollback/recovery note (see the
  `database-migrations` borrow: expand-contract, no NOT-NULL-without-default).
- Writes, retries, and trigger handlers are **idempotent** — re-delivery or a re-fired
  filesystem/schedule/S3-cursor event must not double-process.

### Operations
- Starts from a clean checkout with documented commands (`MANUAL_TESTING.md`).
- Required env vars named, validated, fail-fast (`DATABASE_URL`, `BEDROCK_MODE`,
  `WORKFLOW_DEFINITIONS_DIR`, …).
- A health check proves dependencies are reachable (`/api/health`, `/metrics`).
- Deploy + rollback path documented; `infra/` applied (or the run-local posture stated).
- Logs useful without leaking secrets/PII.

### User experience
- Launch-critical paths exercised (the canvas create→edit→run flow; a real trigger firing).
- Loading / empty / error / permission-denied states each tell the user what happened.
- A recovery path exists when a critical op fails (retry / resume / fork / escalate).

## Scoring (force prioritization, not false precision)

| Band | Score | Meaning |
|---|---|---|
| Blocked | 0–49 | Don't ship until the top risks are fixed |
| Risky | 50–69 | Ship only behind an internal beta / tiny rollout |
| Launchable with caveats | 70–84 | Ship if owners accept the listed risks |
| Strong | 85–100 | No obvious blockers from available evidence |

**Cap at 69** if any hold: auth/authz missing on sensitive data or actions; a public webhook
isn't signature-verified; a trigger handler isn't idempotent; required migrations can't run
safely; secrets exposed; no rollback for a high-impact release.
**Cap at 84** if CI isn't green or the launch-critical path wasn't tested end to end.

## Output format

Lead with one sentence, then the lists:

```
Release readiness: 76/100, launchable with caveats — webhook HMAC and a
documented Alembic rollback are the two risks to close before public exposure.

Blockers:        must-fix before deploy
High-value fixes: next, to raise the score
Evidence checked: files / commands / CI runs / URLs inspected
Evidence missing: what would change confidence
Next action:     one concrete fix or verification step
```

Keep strengths short — the useful answer is the remaining risk and the next action. Don't end
with a generic "let me know what you want to do"; name the next action.

## Anti-patterns

- Treating green CI as production readiness.
- Producing a score without naming the evidence checked.
- Running a remote scanner / uploading source to a third party as the default path.

## See also

`docs/MANUAL_TESTING.md` (operator playbook) · `docs/CANVAS_ROADMAP.md` (C6 trust wedge) ·
`/code-review`, `/security-review` (line-level, run during implementation, not at ship time).
