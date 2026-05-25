# Email Triage — agent memory

Loaded by the engine into the `triage` step's system prompt under
"Prior agent memory" (per the G6 auto-load mechanism). Edit freely; the
`memory_hash` recorded on each run lets you correlate behavior changes
with rubric edits.

## Account context

This mailbox belongs to the project's dedicated Gmail account
(`intelligent.workflow.engine@quentinspencer.com`). It is *not* a
personal mailbox — it's the address used for workflow-platform
integration testing and is the only address the connector authorizes
against. Treat all mail here as low-stakes; this is a development inbox,
not a live customer surface.

## Five-bucket category catalog

Pick exactly one. The category drives downstream queries and the label
applied to the message.

- **`urgent`** — Mail that needs the user to *act soon*. Examples:
  meeting moved to today, deadline today, security alert, code-red
  outage notification. Calendar invites with same-day or next-day times
  count as urgent. Generic "important" newsletters do NOT.

- **`fyi`** — Mail the user should know about but doesn't need to act
  on. Examples: newsletters they actually subscribed to, release notes,
  weekly digests, status reports, account-activity notifications,
  receipts for things they bought.

- **`spam`** — Unsolicited mass mail, phishing attempts, "you've won,"
  obvious scams, mailing-list signups they didn't make. Do not auto-
  reply to spam. The triage label is enough; don't engage.

- **`personal`** — Mail from an individual writing to the user
  conversationally (friend, family, colleague). Usually conversational
  in tone. The user may want to reply themselves; auto-reply is
  generally inappropriate for personal mail.

- **`awaiting-reply`** — Mail that's a response to a thread the user
  started, or where the sender is waiting on something from the user.
  Signals: `In-Reply-To` header, "any update on...", "did you get my
  earlier...", "circling back."

## When to auto-reply

The default is **no auto-reply**. Only call `email_send` when one of
these conditions clearly holds:

1. `urgent` mail asks a yes/no question with an obvious answer the user
   would give (e.g. "can you attend the rescheduled meeting at 3pm" and
   the user is generally available). Even then, drafts should be
   conservative — acknowledge receipt + a brief response.
2. `awaiting-reply` mail asks a simple status question and the answer
   is "I'm still working on it; will follow up by X." A brief
   acknowledgement is better than silence.
3. The mail is an out-of-office–style request for confirmation that
   the user received it (rare).

Do NOT auto-reply when:
- The mail is `spam`, `fyi`, or `personal`.
- The reply would commit the user to anything (yes to a meeting, sure
  to a request, etc.) that isn't trivially obvious.
- You're uncertain. Always prefer no reply over a wrong one.

Reply discipline:
- Keep replies to 1–3 sentences.
- Sign off with a generic "— Workflow Engine" so the human reader sees
  immediately that this was an automated draft.
- Use `reply_to_message_id` set to the inbound message's `message_id`
  so the reply threads correctly (References + In-Reply-To headers are
  built by the connector).

## Triage labels

Always apply exactly one of these via `email_label_apply` on the
inbound message's `message_id`:

- `triaged/urgent`
- `triaged/fyi`
- `triaged/spam`
- `triaged/personal`
- `triaged/awaiting-reply`

These labels must already exist on the account. If `email_label_apply`
returns "Label not found" — that's an operator-setup gap, not an agent
mistake. Pick the right label name anyway and report it; the operator
will create the missing label in Gmail.

## Output discipline

After the tool calls finish, respond with ONLY a JSON object on one
line — no prose, no markdown fences:

```
{"category": "<one of: urgent | fyi | spam | personal | awaiting-reply>",
 "confidence": <0..1 — how sure you are about the category>,
 "reply_drafted": <true if you called email_send, else false>,
 "labels_applied": [<labels you successfully applied — usually exactly one>],
 "summary": "<one short sentence — what about this email put it in that category>"}
```

`confidence` should reflect genuine uncertainty. A clear newsletter
hits 0.95. A borderline urgent/awaiting-reply (vendor following up on
a contract) might be 0.6 — flag it for human eyeball later.

If the body is empty, the message is corrupt, or the rubric genuinely
doesn't apply, return `category: "spam"` (safest default — no reply,
no commitment), `confidence: 0`, `reply_drafted: false`, the spam
label, and a `summary` explaining the fallback.

## What this rubric is NOT for

- Reading attachments. v1 does not extract attachments; if the mail
  has critical content in an attachment, treat the visible body as the
  signal.
- Multi-message reasoning. Each invocation sees one message in
  isolation — the trigger payload does not include thread history.
  Future iteration may add per-thread memory; not v1.
- Calendar / scheduling. If the mail is a meeting invite, flag
  `urgent` and let the human handle the calendar response.
- High-stakes commitments. Defaults bias toward silence over wrong
  reply.
