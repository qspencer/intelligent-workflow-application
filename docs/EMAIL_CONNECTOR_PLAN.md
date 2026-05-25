# Email connector — design + build plan

Forward-looking plan for adding email as a connector. Gmail is the initial
target; the abstraction is shaped so Outlook (Microsoft Graph) and generic
IMAP/SMTP can slot in as drop-in alternates without touching workflow YAMLs.

This is plan, not status. The connector itself is on `docs/USE_CASES.md`'s
"What to skip" list under "Email triage (Gmail / Outlook) — OAuth +
IMAP/Graph connector deferred." This doc exists so that when a workload
pulls for it, the design isn't re-derived.

## Why deferred today

Email is the canonical Tier-1 connector per `docs/INTEGRATIONS.md` (file
business correspondence, classify customer requests, summarize threads).
It's been deferred because:

- OAuth setup is a chunk of operator friction with no upside until a real
  email workload exists.
- The current PR-triage and paper-triage workloads cover the agentic
  patterns we needed to validate.
- Wiring email well requires deciding the abstraction layer up front;
  forcing it before the second provider arrives makes the abstraction
  wrong.

When email *is* the answer to "what's the next workload," this plan turns
into a one-week sprint instead of two weeks of architectural argument.

## Architecture

### Layering

Three layers stack on top of the existing `Connector` ABC:

```
Connector (existing ABC — 6 methods, in workflow_platform.connectors.base)
  │
  ├── EmailConnector (new ABC — adds email-specific methods + types)
  │     │
  │     ├── GmailConnector       (Phase 1)
  │     ├── OutlookConnector     (Phase 2 — MS Graph)
  │     └── ImapSmtpConnector    (Phase 3 — Fastmail, ProtonMail Bridge, self-hosted)
  │
  └── (other connector trees — Webhook, S3, etc. — unchanged)
```

The `EmailConnector` ABC carries everything provider-agnostic: message
shape, send shape, label semantics. Provider-specific quirks (OAuth
endpoints, list-vs-history pagination, MIME quirks) live in the concrete
classes only.

### Common types

Pydantic models under `workflow_platform.connectors.email.models`:

```python
class EmailAddress(BaseModel):
    address: str
    name: str | None = None

class EmailMessage(BaseModel):
    """Inbound message shape — what triggers emit as their payload."""
    provider: Literal["gmail", "outlook", "imap"]
    message_id: str          # provider-specific stable id
    thread_id: str | None    # for reply chains
    from_address: EmailAddress
    to: list[EmailAddress]
    cc: list[EmailAddress] = []
    bcc: list[EmailAddress] = []
    subject: str
    body_text: str           # plain-text body (rendered from HTML if needed)
    body_html: str | None    # original HTML, when available
    received_at: datetime
    labels: list[str] = []   # provider labels / folders
    in_reply_to: str | None = None
    headers: dict[str, str] = {}  # selected headers worth preserving

class EmailSendRequest(BaseModel):
    """Outbound send shape — what the agent / send tool builds."""
    to: list[EmailAddress]
    cc: list[EmailAddress] = []
    bcc: list[EmailAddress] = []
    subject: str
    body_text: str
    body_html: str | None = None
    reply_to_message_id: str | None = None  # see threading note below
    labels_to_apply: list[str] = []        # post-send labels (Gmail)
```

**Threading semantics for `reply_to_message_id`.** Real email threading
requires `In-Reply-To` *and* `References` headers — the latter carries
the full chain of prior message-ids. The send tool takes the single
`reply_to_message_id` as input; the connector implementation is
responsible for fetching the referenced message, copying its
`References` header forward, and appending the referenced id. Workflows
don't need to know about this; `GmailConnector.send_email` handles the
lookup internally.

### EmailConnector ABC

```python
class EmailConnector(Connector):
    """Provider-agnostic email connector.

    Abstract:  poll_inbox, send_email, apply_labels, authenticate.
    Concrete:  trigger_poll, send, health_check — implemented here in terms
               of the abstract methods, so subclasses only override the
               provider-specific bits.
    """

    type: ClassVar[str]  # "gmail" | "outlook" | "imap"

    # --- abstract: subclasses must implement ---

    @abstractmethod
    async def poll_inbox(
        self,
        since: datetime,
        label: str | None = None,
        max_messages: int = 50,
    ) -> list[EmailMessage]: ...

    @abstractmethod
    async def send_email(self, req: EmailSendRequest) -> str:
        """Return the provider's message_id of the sent message."""

    @abstractmethod
    async def apply_labels(self, message_id: str, labels: list[str]) -> None:
        """See `Cross-provider label semantics` below — providers map this
        to their closest non-destructive equivalent."""

    @abstractmethod
    async def authenticate(self) -> None: ...

    # --- concrete: default impls in the ABC ---

    async def trigger_poll(self, on_event, cursor):
        """Default loop: read cursor (last received_at), call poll_inbox,
        emit each EmailMessage via on_event, advance cursor to the max
        received_at seen. Subclasses can override for push semantics."""
        ...

    async def send(self, payload: dict) -> dict:
        """Default: validate payload into EmailSendRequest, call send_email,
        return {"message_id": ...}."""
        ...

    async def health_check(self) -> bool:
        """Default: poll_inbox(max_messages=1) and return True if the call
        succeeds. Subclasses can override for cheaper checks."""
        ...
```

The trigger pattern matches what's there today: `trigger_poll` returns a
list of messages-since-cursor; the `TriggerOrchestrator` handles the loop.

### Cross-provider label semantics

`apply_labels` is named after Gmail because Gmail's label model is the
*least destructive* of the three:

- **Gmail:** non-destructive tags. A message can carry many labels;
  applying a label adds metadata, never moves the message. Direct
  implementation.
- **Outlook:** maps to **categories** (also non-destructive) rather than
  folder moves. `OutlookConnector` must *not* implement `apply_labels`
  as a folder move — that would change message location and is
  irreversible from the workflow's point of view.
- **IMAP:** maps to **flags** / custom keywords (`\Seen`, custom
  `$Workflow*` keywords). Non-destructive; flags are server-side.

Workflows write "apply label `urgent`"; the provider decides what that
becomes. If a workflow genuinely needs to *move* a message, that's a
separate `move_to_folder` method added later under a workload pull —
not silently overloaded onto `apply_labels`.

### Auth

OAuth 2.0 refresh-token pattern, abstracted behind a small helper:

```python
class EmailAuthProvider(Protocol):
    async def access_token(self) -> str: ...  # cached + refreshes as needed

class GmailAuthProvider:
    def __init__(self, secret_store: SecretStore, secret_name: str): ...
    # On construction: load refresh_token + client_id + client_secret from
    # secret_store. On access_token(): use cached or hit Google's token
    # endpoint with the refresh_token.
```

Refresh tokens stored in `SecretStore` under a known namespace
(`gmail/<account>/refresh_token`, `gmail/<account>/client_secret`). The
operator setup doc (see below) walks through the one-time consent flow.

**Sync-API wrapping.** Both `googleapiclient` and the Google auth
libraries are synchronous. Every call from the connector wraps in
`asyncio.to_thread`, same pattern used for `boto3` in
`workflow_platform.bedrock` and `workflow_platform.connectors.s3`. This
isn't optional — blocking the event loop in a polling connector wedges
every other workflow on the same engine.

### Capability allowlist

Email capabilities are explicit strings on the workflow / step capability
allowlist, gated through the existing capability intersection model. No
default-on access:

| Capability | Grants |
|---|---|
| `email_read` | `GmailConnector.poll_inbox` (triggers always have this implicitly) |
| `email_send` | `EmailSendTool` (`send_email`) |
| `email_label_apply` | `apply_labels` (deterministic post-step or agent tool) |

The agent's tool surface in v1 is **just `email_send`**. The agent
*reads* the message via the trigger payload it was invoked on — it has
no general inbox-read tool. Broader read access (search, fetch by id,
list-by-label) is deferred until a workload pulls; the constraint keeps
the blast radius small while the rubric is still being iterated.

### Trigger payload

The `gmail_poll` trigger's payload is one `EmailMessage` per event — same
shape no matter which provider, so a workflow built for Gmail works on
Outlook with a one-line YAML edit.

## Phase 1: Gmail (read + send)

### Deliverables

| Module | Purpose |
|---|---|
| `workflow_platform.connectors.email.base` | `EmailConnector` ABC |
| `workflow_platform.connectors.email.models` | `EmailMessage`, `EmailSendRequest`, `EmailAddress` |
| `workflow_platform.connectors.email.gmail` | `GmailConnector` |
| `workflow_platform.connectors.email.gmail_auth` | OAuth refresh + first-time consent helper |
| `workflow_platform.triggers.gmail_poll` | Polling trigger wrapping `GmailConnector.trigger_poll` |
| `workflow_platform.tools.email_send` | `EmailSendTool` (agent-callable) for outbound replies |
| `workflow_platform.engine.functions.record_email_triage` | Stock function parsing agent triage JSON into structured fields (mirrors `record_pr_triage` / `record_paper_triage`) |
| `examples/email_triage/` | First validation workflow |
| `backend/tools/gmail_auth.py` | One-shot consent CLI (mirrors `backend/tools/smoke_live.py` shape) |
| `backend/pyproject.toml` | Add `google-api-python-client`, `google-auth`, `google-auth-oauthlib` (pinned per Risk #5) |

### Operator setup (mirrors `docs/BEDROCK_SETUP.md`)

A dedicated Google account has been created for this project; the four
gates below are all performed *as that account*, not against any
personal mailbox. Keeping the workflow account isolated means revoking
consent or rotating credentials never touches anything personal, and
the "Testing"-status 7-day re-consent (Gate 2) is a per-project nuisance
instead of a global one.

Four gates, none load-bearing on the platform itself — all live in
Google Cloud / Google account land:

**Gate 1 — Create a GCP project + enable Gmail API.** Sign in as the
project's dedicated Google account. Create a new GCP project (e.g.
`workflow-platform-dev`); enable Gmail API via Console or
`gcloud services enable gmail.googleapis.com`.

**Gate 2 — Configure OAuth consent screen.** External user type
(Internal requires Workspace). "Testing" status is fine — the
refresh-token-on-Testing 7-day expiry is acceptable for a dedicated
solo-dev account where re-running `gmail_auth.py` is cheap. Add the
project account itself as the sole test user. (Promotion to "In
production" with verification is months of process for sensitive
scopes and not worth it pre-pull.)

Upload `docs/assets/logo.png` (120×120 indigo-on-white diamond DAG,
reproducible from `docs/assets/generate_logo.py`) as the application
logo. App name: "Intelligent Workflow Platform" (or whatever name the
dedicated GCP project carries — consent screen + project name must
agree).

**Gate 3 — Create OAuth Client ID.** Application type: Desktop app
(installed application). Download credentials JSON; load into SecretStore
as `gmail/<account>/client_credentials`.

On-disk convention for solo-dev today (until AWS Secrets Manager comes
online via the deferred Terraform apply, P2.3): the JSON file lives at
`.secrets/gmail/<account>/client_credentials.json`. The `.secrets/`
directory is gitignored, the file is chmod 0600, and the project root
also has a `client_secret_*.json` ignore pattern to catch downloads
that ever land there. The future `gmail_auth.py` CLI reads from
`.secrets/...` on first run and seeds it into whichever `SecretStore`
the process is configured to use (`EnvSecretStore` for dev,
`AwsSecretsManagerStore` when the infra is live). The on-disk file
remains the source of truth in dev — `EnvSecretStore` is process-local,
so its `put()` doesn't persist across runs.

Account namespace today is `intelligent.workflow.engine@quentinspencer.com`
— the dedicated project Gmail (Google-Workspace-hosted on the personal
domain). Single account; the namespace shape already supports a second
account dropping in alongside (`gmail/<other-account>/...`) without any
code or convention change.

**Gate 4 — Run one-time consent.** `backend/tools/gmail_auth.py --account
<email>` opens a browser to Google's consent page (sign in as the
project account), captures the authorization code on localhost callback,
exchanges for refresh + access tokens, writes the refresh token to
SecretStore. On "Testing"-status consent screens the refresh token
expires after 7 days; the CLI must surface that as a re-consent prompt
rather than a generic auth error.

Each gate gets a code-citable error message in `gmail_auth.py` so a
future operator sees "you haven't completed Gate 3" rather than a raw
Google API error.

### Cost ballpark

Gmail API: free for solo-dev volumes (1 B quota units/day per project,
well under any realistic personal use). Bedrock inference per email
triage at Haiku 4.5 ($1/$5 per 1 M tokens) is roughly **$0.001 per
email** assuming ~1 K input + ~200 output tokens — under the noise
floor for the validation workload. Sending costs nothing beyond the
inference that drafts the reply.

### Example workflow: `examples/email_triage/`

```yaml
id: email-triage
name: Email Triage
trigger:
  type: gmail_poll
  config:
    label: "INBOX"
    poll_interval_seconds: 60
    secret_namespace: gmail/me@example.com

steps:
  - id: triage
    type: agentic
    model: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tools: [email_send]  # so the agent can reply if appropriate
    goal: |
      Categorize and route this email. Apply the rubric from your
      memory to decide: urgent / fyi / spam / personal / awaiting-reply.
      If the email warrants an auto-reply (out-of-office, simple
      acknowledgement), call email_send. Otherwise, just record.
    policy: {max_iterations: 3, max_total_tokens: 4000}

  - id: record
    type: deterministic
    function: record_email_triage  # new stock function
    config:
      triage_from: steps.triage.output_text

edges:
  - from: triage
    to: record
```

`agent_memory.md` carries the categorization rubric. `examples/email_triage/data/`
seeds a stable set of test emails (slimmed real or hand-crafted) — same
convention as `arxiv_batch_50.json` in paper triage. Unlike the PDF
classifier fixtures (see G1 in `docs/NEXT_STEPS.md`), email fixtures are
JSON-only and carry no host-specific paths, so Bedrock replay-mode
recordings are fully portable across machines without the
hash-normalization workaround.

### Tests

All test files live under `backend/tests/` (matching the rest of the
test suite — `test_pdf_classifier_workflow.py`, `test_github_pr_triage.py`,
etc.):

| Test file | Coverage |
|---|---|
| `backend/tests/test_email_models.py` | Pydantic round-trip, validation edges |
| `backend/tests/test_gmail_connector.py` | `GmailConnector` against fakes (mocked `googleapiclient`) — poll happy path, pagination, send happy path, label apply, error mapping, `In-Reply-To` + `References` header construction on replies |
| `backend/tests/test_gmail_auth.py` | Token refresh logic; happy path, expired refresh, revoked refresh (surfaces as escalation) |
| `backend/tests/test_email_triage_workflow.py` | End-to-end against `FakeBedrock` + the example workflow (replay-mode, machine-portable) |
| `backend/tests/test_gmail_live.py` | `@pytest.mark.live` opt-in via `GMAIL_LIVE=1` — hits the dedicated project account, costs nothing (free tier), mirrors the Bedrock `@pytest.mark.live` shape |

### Build sequence (Phase 1)

Roughly day-by-day for a focused sprint. Every call into
`googleapiclient` or `google.auth` from this point on is wrapped in
`asyncio.to_thread` — never call sync Google libs from the event loop
directly.

1. **EmailConnector ABC + models.** Add `google-api-python-client` /
   `google-auth` / `google-auth-oauthlib` to `pyproject.toml`. Tests:
   model round-trips, type validation. *Half day.*
2. **GmailConnector against mocks.** `googleapiclient.discovery` build +
   the few methods we need (`users().messages().list/get/send/modify`),
   each wrapped via `asyncio.to_thread`. Tests: poll, send, label,
   reply-with-References-chain. *One day.*
3. **OAuth helper + one-shot consent CLI.** `backend/tools/gmail_auth.py`.
   Manual verification: complete consent flow once as the project
   account. *One day.*
4. **`gmail_poll` trigger + orchestrator wiring.** Tests: orchestrator
   recognizes the new trigger type, fires the engine on a fake message.
   *Half day.*
5. **`EmailSendTool` for outbound.** Agent-callable tool with `to` /
   `subject` / `body` / `reply_to_message_id` schema, gated on the
   `email_send` capability. Tests: tool dispatch, capability deny path.
   *Half day.*
6. **`examples/email_triage/` + replay-mode pytest.** Workflow YAML,
   `agent_memory.md` (urgent / fyi / spam / personal rubric), five
   synthetic fixtures, helper scripts. *One day.*
7. **Live integration test** behind `GMAIL_LIVE=1`. Send-to-self loop
   against the project account: compose → send → poll-and-receive →
   assert match. *Half day.*
8. **CI cadence for live tests.** Add a scheduled GitHub Actions job
   running the `GMAIL_LIVE=1` and `BEDROCK_LIVE=1` suites nightly (or
   weekly) against a dedicated branch. Catches provider API drift early
   per Risk #5; backfills the same coverage for the existing Bedrock
   live tests. *Half day.*

Total: ~5.5 days focused. The OAuth flow is the only piece with real
unknown unknowns; everything else is well-traveled territory.

## Phase 2: Outlook (Microsoft Graph)

Once Phase 1 is real, Outlook is almost mechanical:

- `OutlookConnector(EmailConnector)` against Microsoft Graph API
- Same `EmailMessage` / `EmailSendRequest` types — no workflow changes
- Workflow YAML swap: `trigger.type: outlook_poll`
- Different OAuth provider (MS identity platform) → new
  `OutlookAuthProvider` implementing the same protocol

Effort: ~2 days, mostly fighting the Graph API's idiosyncrasies (mailbox
folders vs labels, the `value` envelope shape, batching semantics).

## Phase 3: Generic IMAP/SMTP

For providers without first-class API access (Fastmail, ProtonMail bridge,
self-hosted, dedicated dev mailboxes):

- `ImapSmtpConnector(EmailConnector)` using `imaplib` (read) + `smtplib`
  (send) from stdlib
- App-password auth instead of OAuth — simpler, but less granular
- Stateless poll: track last-seen IMAP UID per folder
- Single connection per cursor; close cleanly

Effort: ~2 days. Worth doing once it's the only way to integrate with a
specific provider.

## Phase 4: Push notifications

Polling is the right default (simple, no inbound infra). For workloads
that need < 60s latency:

- **Gmail:** Pub/Sub + watch API. Google pushes to a webhook on
  new-mail events. Reuses our existing `WebhookTrigger` — the webhook
  handler just decodes the push payload into an `EmailMessage` and
  forwards to the engine.
- **Outlook:** Microsoft Graph subscriptions. Same shape.

Effort: ~2 days plus Pub/Sub setup friction (another Bedrock-style
operator gate).

## Out of scope (deferred *within* email)

Don't build these in v1; pull in when a workload demands:

| Feature | When to revisit |
|---|---|
| Attachment read/write | A workflow needs to triage attached PDFs (then it's mostly wiring `pdf_extract` to the message's attachments) |
| Calendar integration | Separate connector — same provider, different API surface |
| Contact / address-book operations | Same |
| Full-text search beyond label + recency | Workflow needs "find messages matching X" rather than "process new" |
| Per-thread agent memory | A workflow needs to remember "what we last said to alice@" |
| Multi-account in one workflow | One operator runs multiple mailboxes through the same workflow |
| HTML rendering as more than `body_html` field | An agentic step needs to *understand* the HTML structure, not just see it |

## Open questions

These need answers before the build, not now:

1. **OAuth callback URL in production.** Solo-dev uses localhost against
   the dedicated project account. A deployed instance needs an HTTPS
   callback. Either: ship with the Terraform stack applied (P2.3) and
   use the ALB URL, or document an ngrok / Tailscale Funnel workaround.
2. **Dry-run mode for sending.** Sending wrong email is worse than
   logging wrong concern. Should `EmailSendTool` have a `dry_run` flag
   that logs the would-be-sent message instead of actually sending,
   controllable per workflow or per role?
3. **Multi-account namespacing.** Today there is exactly one account —
   the dedicated project account. If the same operator later runs
   multiple project accounts through different workflows, the
   secret-store keys already disambiguate (`gmail/<account>/...`) and
   the trigger config already carries `secret_namespace`. Confirm the
   shape holds on first real two-account scenario.
4. **Rate limiting / quota.** Gmail's free tier is 1 B units/day per
   project — plenty for solo-dev. But the connector should surface
   quota errors clearly when they happen, not retry forever.

**Resolved during this review** (kept here as a breadcrumb so the next
revision sees the decision, not the question):

- *Token expiry / revoke surfacing.* When Google decides a refresh
  token is dead, the next poll fails — route through
  `RequestHumanReviewTool` as an `escalation_requested` audit entry.
  Test in `test_gmail_auth.py` covers the revoked-refresh path.

## Risks

1. **OAuth complexity is real friction.** Mitigation: invest in the
   one-shot consent CLI + the operator-facing setup doc up front. Don't
   ship the connector without the gates documented as well as
   `docs/BEDROCK_SETUP.md`.
2. **Sending wrong email is high-stakes.** Mitigation: dry-run mode
   (open question #2), and per-workflow capability allowlist must
   include `email_send` explicitly — no default-on access (see the
   capability table above).
3. **Email is a deliverability minefield at scale.** If this platform
   sends thousands of emails, providers will throttle and flag. Out of
   scope for v1; mention as a Phase 2+ concern when volume matters.
4. **Refresh tokens can expire / be revoked.** Mitigation: route the
   "token dead" condition through the escalation primitive. Don't hide
   it in logs.
5. **Provider API drift.** Google has deprecated APIs before with
   sub-1-year notice. Mitigation: pin `google-api-python-client` /
   `google-auth` / `google-auth-oauthlib` versions in `pyproject.toml`,
   and run the live integration test on a CI schedule (build sequence
   step 8). The Bedrock live tests get the same scheduled run as a
   side-effect.

## When this graduates

The trigger that opens this plan back up: **a concrete email workflow
someone actually wants automated.** Personal inbox triage (urgent vs.
fyi), customer-support triage (ticket type → routing), invoice intake
(forward + extract), out-of-office auto-reply, weekly digest from a
folder. Any of those provides the pull. Until then, this doc waits.

When that pull arrives:
1. Re-read this doc, pick which sub-features the workload needs.
2. Execute Phase 1's build sequence.
3. Iterate the workflow's `agent_memory.md` rubric the same way
   PR-triage and paper-triage iterated theirs.
4. Update `docs/USE_CASES.md` to move "email triage" from "What to skip"
   into a candidate workload entry.
5. Update this doc with what changed during the build — what was easier
   than planned, what was harder. The doc earns its keep by being kept
   honest, not by being preserved.

## How this doc goes stale

- Phase 1 begins → migrate this doc's design sections into the actual
  code as docstrings; keep this doc for the historical "why" + the
  out-of-scope list.
- A new email provider becomes the primary target (e.g., Apple Mail iCloud
  becomes available via API) → add a Phase 5 section, don't rewrite the
  ABC.
- A risk above turns into a real bug → log it in
  `docs/NEXT_STEPS.md` with reference to this doc.
