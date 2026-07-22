"""GmailConnector — concrete EmailConnector against the Gmail v1 API.

Wraps `googleapiclient.discovery` (sync) and runs every API call through
`asyncio.to_thread` — `boto3`-style — so the polling loop never wedges
the engine event loop.

The concrete `GmailAuthProvider` (refresh-token-driven OAuth) lands on
Day 3 in `workflow_platform.connectors.email.gmail_auth`. This module
defines only the structural `GmailAuthProvider` protocol that the
connector consumes; tests inject a fake that satisfies it.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from email.message import EmailMessage as RawMimeMessage
from email.utils import getaddresses, parseaddr
from typing import Any, ClassVar, Protocol

from workflow_platform.connectors.email.base import EmailConnector
from workflow_platform.connectors.email.models import (
    EmailAddress,
    EmailAttachment,
    EmailMessage,
    EmailSendRequest,
)


class GmailAuthProvider(Protocol):
    """Returns a live access token. Implementations cache and refresh as needed."""

    async def access_token(self) -> str: ...


class GmailMessageNotFound(LookupError):
    """Raised when a message id is unknown to Gmail (404)."""


class GmailLabelNotFound(LookupError):
    """Raised when a label name doesn't exist on the account."""


class GmailConnector(EmailConnector):
    type: ClassVar[str] = "gmail"
    USER_ID: ClassVar[str] = "me"  # OAuth scopes always operate as the authenticated user
    LIST_PAGE_SIZE: ClassVar[int] = 100  # Gmail's per-page max for messages.list

    def __init__(
        self,
        *,
        account: str,
        auth_provider: GmailAuthProvider,
        service: Any = None,  # injectable for tests
    ) -> None:
        super().__init__()
        self.account = account
        self.auth_provider = auth_provider
        self._service: Any = service
        # Tests inject a fake service that never expires; only self-built
        # services are rebuilt when the access token rotates.
        self._service_is_injected = service is not None
        self._service_token: str | None = None
        self._label_id_cache: dict[str, str] = {}
        # `googleapiclient.discovery.build()` returns a service whose
        # internal `httplib2.Http()` is documented NOT thread-safe.
        # Multiple `to_thread(request.execute)` calls against one shared
        # service have segfaulted with glibc heap corruption on this
        # host. Serializing all Gmail API calls through one asyncio
        # lock costs ~nothing (per-call latency is ~200ms vs Bedrock's
        # multi-second turns, which dominate per-message work) and
        # eliminates the crash class entirely. If a future workload
        # needs true Gmail concurrency, the real fix is per-call
        # `request.execute(http=httplib2.Http())` — see
        # https://github.com/googleapis/google-api-python-client/blob/main/docs/thread_safety.md.
        self._call_lock = asyncio.Lock()

    # --- service plumbing ---

    async def _get_service(self) -> Any:
        if self._service_is_injected:
            return self._service
        # The service bakes in a bare access token (no refresh fields —
        # refresh lives in the auth_provider, by design). Access tokens
        # expire after ~1h, so consult the provider on every call (cheap:
        # it caches until near-expiry) and rebuild the service whenever the
        # token has rotated. Caching the service forever caused every poll
        # to fail with RefreshError one hour into a long-running process.
        token = await self.auth_provider.access_token()
        if self._service is not None and token == self._service_token:
            return self._service

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(token=token)  # type: ignore[no-untyped-call]
        self._service = await asyncio.to_thread(
            build, "gmail", "v1", credentials=creds, cache_discovery=False
        )
        self._service_token = token
        return self._service

    async def _execute(self, request: Any) -> Any:
        """Run a googleapiclient Request via to_thread, mapping known errors.

        Held under `self._call_lock` because the shared service object's
        internal `httplib2.Http()` isn't thread-safe (see `__init__`).
        """
        from googleapiclient.errors import HttpError

        async with self._call_lock:
            try:
                return await asyncio.to_thread(request.execute)
            except HttpError as exc:
                if exc.resp.status == 404:
                    raise GmailMessageNotFound(str(exc)) from exc
                raise

    # --- EmailConnector abstract methods ---

    async def authenticate(self) -> None:
        svc = await self._get_service()
        await self._execute(svc.users().getProfile(userId=self.USER_ID))

    async def poll_inbox(
        self,
        since: datetime | None = None,
        label: str | None = None,
        max_messages: int = 50,
        query: str | None = None,
    ) -> list[EmailMessage]:
        """List + fetch messages. `query` is an extra raw Gmail search clause
        (e.g. `has:attachment filename:zip`) ANDed with the since/label parts —
        server-side filtering so triggers don't fire on irrelevant mail."""
        svc = await self._get_service()
        query_parts: list[str] = []
        if since is not None:
            # Gmail's `after:` accepts integer seconds-since-epoch.
            query_parts.append(f"after:{int(since.timestamp())}")
        if label:
            query_parts.append(f"label:{label}")
        if query:
            query_parts.append(query)
        q = " ".join(query_parts)

        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < max_messages:
            kwargs: dict[str, Any] = {
                "userId": self.USER_ID,
                "maxResults": min(self.LIST_PAGE_SIZE, max_messages - len(ids)),
            }
            if q:
                kwargs["q"] = q
            if page_token:
                kwargs["pageToken"] = page_token
            resp = await self._execute(svc.users().messages().list(**kwargs))
            for entry in resp.get("messages", []) or []:
                ids.append(entry["id"])
                if len(ids) >= max_messages:
                    break
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        messages: list[EmailMessage] = []
        for mid in ids:
            raw = await self._execute(
                svc.users().messages().get(userId=self.USER_ID, id=mid, format="full")
            )
            messages.append(_parse_gmail_message(raw))
        return messages

    async def send_email(self, req: EmailSendRequest) -> str:
        svc = await self._get_service()
        in_reply_to = ""
        references = ""
        thread_id: str | None = None
        if req.reply_to_message_id:
            prior = await self._execute(
                svc.users()
                .messages()
                .get(
                    userId=self.USER_ID,
                    id=req.reply_to_message_id,
                    format="metadata",
                    metadataHeaders=["Message-ID", "References"],
                )
            )
            headers = _header_map(prior.get("payload", {}).get("headers", []))
            prior_mid = headers.get("Message-ID", "")
            prior_refs = headers.get("References", "")
            in_reply_to = prior_mid
            references = f"{prior_refs} {prior_mid}".strip() if prior_refs else prior_mid
            thread_id = prior.get("threadId")

        raw_bytes = _build_rfc_5322(
            req, from_account=self.account, in_reply_to=in_reply_to, references=references
        )
        body: dict[str, Any] = {
            "raw": base64.urlsafe_b64encode(raw_bytes).decode("ascii"),
        }
        if thread_id:
            body["threadId"] = thread_id
        sent = await self._execute(svc.users().messages().send(userId=self.USER_ID, body=body))
        sent_id = str(sent["id"])

        if req.labels_to_apply:
            await self.apply_labels(sent_id, req.labels_to_apply)
        return sent_id

    async def apply_labels(self, message_id: str, labels: list[str]) -> None:
        if not labels:
            return
        svc = await self._get_service()
        label_ids = [await self._resolve_label_id(name) for name in labels]
        await self._execute(
            svc.users()
            .messages()
            .modify(
                userId=self.USER_ID,
                id=message_id,
                body={"addLabelIds": label_ids},
            )
        )

    async def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Fetch one attachment's bytes. Gmail returns attachment content only
        via a dedicated call (`messages.attachments.get`), base64url-encoded."""
        svc = await self._get_service()
        resp = await self._execute(
            svc.users()
            .messages()
            .attachments()
            .get(userId=self.USER_ID, messageId=message_id, id=attachment_id)
        )
        data = resp["data"]
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

    # --- helpers ---

    async def create_label(self, name: str) -> str:
        """Create a user label; returns its id. Idempotent for callers that
        check existence first (Gmail 409s on duplicates — surfaced as
        HttpError). Operator-CLI surface (setup_triage_labels), NOT reachable
        from EmailLabelApplyTool — the tool's refuse-to-create resolution is
        a deliberate fence (EMAIL_TRIAGE_ACT_PLAN §4)."""
        svc = await self._get_service()
        resp = await self._execute(
            svc.users().labels().create(userId=self.USER_ID, body={"name": name})
        )
        self._label_id_cache[name] = resp["id"]
        return str(resp["id"])

    async def _resolve_label_id(self, name: str) -> str:
        # System labels (INBOX, UNREAD, ...) have id == name; Gmail accepts either.
        if name in self._label_id_cache:
            return self._label_id_cache[name]
        svc = await self._get_service()
        resp = await self._execute(svc.users().labels().list(userId=self.USER_ID))
        for entry in resp.get("labels", []) or []:
            self._label_id_cache[entry["name"]] = entry["id"]
        if name not in self._label_id_cache:
            raise GmailLabelNotFound(f"Label {name!r} not found on account {self.account!r}")
        return self._label_id_cache[name]


# --- module-level parsing / building helpers ---


def _header_map(headers: list[dict[str, str]]) -> dict[str, str]:
    """Case-preserving header lookup. Gmail returns headers as a list of
    {name, value} dicts; we collapse to a dict keyed by exact name."""
    return {h["name"]: h["value"] for h in headers}


def _parse_address(value: str) -> EmailAddress:
    name, addr = parseaddr(value)
    return EmailAddress(address=addr, name=name or None)


def _parse_address_list(value: str | None) -> list[EmailAddress]:
    if not value:
        return []
    return [EmailAddress(address=addr, name=name or None) for name, addr in getaddresses([value])]


def _decode_b64url(data: str) -> str:
    """Gmail base64url-encodes message body data. Pad if needed and decode."""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")


def _extract_bodies(payload: dict[str, Any]) -> tuple[str, str | None]:
    """Walk Gmail's nested `payload.parts` tree, returning (text, html)."""
    text = ""
    html: str | None = None

    def walk(part: dict[str, Any]) -> None:
        nonlocal text, html
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data:
            if mime == "text/plain" and not text:
                text = _decode_b64url(data)
            elif mime == "text/html" and html is None:
                html = _decode_b64url(data)
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return text, html


def _extract_attachments(payload: dict[str, Any]) -> list[EmailAttachment]:
    """Walk `payload.parts` collecting real attachments — parts that carry a
    filename and a Gmail `attachmentId` (bodies fetched separately)."""
    found: list[EmailAttachment] = []

    def walk(part: dict[str, Any]) -> None:
        body = part.get("body") or {}
        filename = part.get("filename") or ""
        if filename and body.get("attachmentId"):
            found.append(
                EmailAttachment(
                    filename=filename,
                    mime_type=part.get("mimeType", "application/octet-stream"),
                    attachment_id=body["attachmentId"],
                    size_bytes=int(body.get("size", 0) or 0),
                )
            )
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return found


def _parse_gmail_message(raw: dict[str, Any]) -> EmailMessage:
    payload = raw.get("payload", {})
    headers = _header_map(payload.get("headers", []))
    body_text, body_html = _extract_bodies(payload)
    received_at = datetime.fromtimestamp(int(raw["internalDate"]) / 1000.0, tz=UTC)
    return EmailMessage(
        provider="gmail",
        message_id=str(raw["id"]),
        thread_id=raw.get("threadId"),
        from_address=_parse_address(headers.get("From", "")),
        to=_parse_address_list(headers.get("To")) or [EmailAddress(address="")],
        cc=_parse_address_list(headers.get("Cc")),
        bcc=_parse_address_list(headers.get("Bcc")),
        subject=headers.get("Subject", ""),
        body_text=body_text,
        body_html=body_html,
        received_at=received_at,
        labels=list(raw.get("labelIds", []) or []),
        in_reply_to=headers.get("In-Reply-To"),
        headers=headers,
        attachments=_extract_attachments(payload),
    )


def _format_address(addr: EmailAddress) -> str:
    return f'"{addr.name}" <{addr.address}>' if addr.name else addr.address


def _build_rfc_5322(
    req: EmailSendRequest,
    *,
    from_account: str,
    in_reply_to: str = "",
    references: str = "",
) -> bytes:
    msg = RawMimeMessage()
    msg["From"] = from_account
    msg["To"] = ", ".join(_format_address(a) for a in req.to)
    if req.cc:
        msg["Cc"] = ", ".join(_format_address(a) for a in req.cc)
    if req.bcc:
        msg["Bcc"] = ", ".join(_format_address(a) for a in req.bcc)
    msg["Subject"] = req.subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(req.body_text)
    if req.body_html:
        msg.add_alternative(req.body_html, subtype="html")
    return msg.as_bytes()
