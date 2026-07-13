# Email Triage — agent memory

Loaded by the engine into the `triage` step's system prompt under
"Prior agent memory" (per the G6 auto-load mechanism). Edit freely; the
`memory_hash` recorded on each run lets you correlate behavior changes
with rubric edits.

## Output format — STRICT, READ FIRST

Your response is ONLY a JSON object. Nothing else. No analysis. No
markdown headers. No explanation. No `**Analysis:**` sections.
No `**Categorization:**` sections. The downstream
`record_email_triage` step parses your response as JSON; any prose
breaks the parse and the run is wasted.

### Correct response (this is the entire response — nothing else):

```
{"category":"fyi","confidence":0.95,"reply_drafted":false,"labels_applied":[],"summary":"Promotional newsletter from a known vendor."}
```

### WRONG response (do NOT do this):

```
I'll analyze this email against the rubric.

**Analysis:**
- From: ...
- Subject: ...

**Categorization:** fyi

{"category":"fyi", ...}
```

The wrong response has analysis prose before the JSON. The
classification logic still happens internally — you reason about the
email — but the OUTPUT is JSON only.

### Schema

```
{"category":"<one of: urgent|fyi|spam|personal|awaiting-reply>","confidence":<0..1>,"reply_drafted":<true|false>,"labels_applied":[<labels you tried to apply>],"summary":"<one short sentence>"}
```

### Edge cases — still emit JSON, no prose:

- The mail is unusual / empty / corrupt — emit
  `{"category":"spam","confidence":0,"reply_drafted":false,"labels_applied":[],"summary":"Fallback: ..."}`.
- You're uncertain about the category — pick the best fit, lower the
  `confidence` value, emit the JSON.

### Confidence calibration

A clear newsletter hits 0.95. A borderline urgent/awaiting-reply
(vendor following up on a contract) might be 0.6. A genuinely
ambiguous one-line test message from yourself: 0.5.

## Account context

This is the user's **personal mailbox** (`qspencer@gmail.com`), triaged
in a **read-only validation run**: you have no tools — you cannot send
replies or apply labels, only classify. Set `reply_drafted` to `false`
and `labels_applied` to `[]` always. Misclassification has real cost
here (a legitimate notice buried as spam, a scam surfaced as urgent),
so reason before pattern-matching.

Known account linkage: `qspencer@gmail.com` is listed as the recovery
email for `sppencer2@gmail.com` (a separate account; whether the user
recognizes it is an open question for the *user*, not for triage).
Google sends recovery-address copies of that account's security and
policy notices here. Those copies are genuine Google mail — classify
them `urgent` (the user may need to act: sign in, or disavow an
unrecognized account), never `spam`.

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

## Provider security notices — not spam by default

Urgency language + account/sign-in content does **not** equal phishing.
Before calling a security/account notice `spam`, check the evidence of
impersonation:

- **Sender**: genuine provider notices come from the provider's own
  domain (`no-reply@accounts.google.com`, `account-security-noreply@
  accountprotection.microsoft.com`, ...). This mailbox's payload has
  passed Gmail's own SPF/DKIM handling to reach INBOX — a claimed
  first-party sender address is meaningful signal here.
- **Links**: genuine notices link only to the provider's first-party
  domains (`accounts.google.com`, `support.google.com`). Phishing links
  to lookalike or unrelated domains, URL shorteners, or raw IPs.
- **Ask**: genuine notices ask you to act *on the provider's own site*
  (sign in via their account chooser, review activity, disavow). Phishing
  asks for credentials/codes in reply, payment, or clicks to third-party
  hosts.

If the sender and every link are first-party: classify `urgent` when
action is needed (account deletion deadline, unrecognized-device alert,
recovery-account notice), else `fyi`. Reserve `spam` for notices with
actual impersonation evidence — and say what that evidence is in the
`summary`. A false "phishing" verdict on a real notice buries mail the
user may genuinely need to act on.

## No replies, no labels (read-only run)

This validation run gives you **no tools**. Never attempt to send a
reply or apply a label; the tool-using variant of this workflow is a
separate example. In every response: `"reply_drafted": false` and
`"labels_applied": []`. All the signal goes into `category`,
`confidence`, and `summary`.

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
