# Email triage — acting variant

The first workflow where an agent holds a mutating external capability:
after classification, a minimal second agent step applies `wf/<category>`
to the message in Gmail. Design + security analysis:
`docs/EMAIL_TRIAGE_ACT_PLAN.md` (privilege split, input minimization,
category enum gate, label allowlisting, partial-failure semantics).

## Prerequisites

1. Credentials for the mailbox under `.secrets/gmail/<account>/` (the
   per-account tool `email_label_apply__<sanitized-account>` is wired at boot for
   every credentialed account, allowlisted to `wf/*`).
2. The seven labels pre-created once (the tool refuses to create labels —
   deliberate fence):

   ```bash
   cd backend && uv run python tools/setup_triage_labels.py qspencer@gmail.com
   ```

## Why this ships with a manual trigger

The orchestrator registers every example's trigger at boot. If this
workflow shipped with an email trigger it would poll the same mailbox as
`email_triage_live` — double classification spend, and acting before the
supervised window was opened deliberately.

## Cutover (start the supervised window)

In ONE edit, then restart the backend:

1. Here: replace `trigger.type: manual` + `example_payload` with the email
   trigger block from `../email_triage_live/workflow.yaml` (provider,
   account, poll interval, `slim_payload: true`).
2. There (`email_triage_live/workflow.yaml`): set `trigger.type: manual`
   (keep its example_payload) — it becomes the rollback artifact.
3. `systemctl --user restart workflow-be`. The G9 cursor keys by
   workflow id + account, so the new workflow starts polling from "now"
   (no historical flood).

Rollback is the same edit in reverse. Validation criteria for the window:
`docs/EMAIL_TRIAGE_ACT_PLAN.md` §8 (100% category parity, zero unexpected
writes, cost ≤ ~$0.001/message on the apply step).
