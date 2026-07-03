"""Shared test fakes for the Gmail connector.

Mirrors the `tests/_bedrock_fakes.py` pattern: importable from any test
that needs to exercise Gmail-touching code without hitting the network.
"""

from __future__ import annotations

import base64
from typing import Any

from googleapiclient.errors import HttpError


class _Request:
    """Stand-in for a googleapiclient Request object. `.execute()` returns
    a pre-staged value (or raises a pre-staged exception)."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def execute(self) -> Any:
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


class _Messages:
    def __init__(self, svc: FakeGmailService) -> None:
        self._svc = svc

    def list(self, **kwargs: Any) -> _Request:
        self._svc.calls.append(("messages.list", kwargs))
        # If a list of pages was configured, advance through them by index.
        if isinstance(self._svc.list_response, list):
            idx = self._svc._list_page_idx
            self._svc._list_page_idx += 1
            return _Request(self._svc.list_response[idx])
        return _Request(self._svc.list_response)

    def get(self, **kwargs: Any) -> _Request:
        self._svc.calls.append(("messages.get", kwargs))
        mid = kwargs["id"]
        if mid in self._svc.get_errors:
            return _Request(self._svc.get_errors[mid])
        if mid not in self._svc.get_responses:
            raise KeyError(f"FakeGmailService has no staged response for id={mid!r}")
        return _Request(self._svc.get_responses[mid])

    def send(self, **kwargs: Any) -> _Request:
        self._svc.calls.append(("messages.send", kwargs))
        return _Request(self._svc.send_response)

    def modify(self, **kwargs: Any) -> _Request:
        self._svc.calls.append(("messages.modify", kwargs))
        return _Request(self._svc.modify_response)

    def attachments(self) -> _Attachments:
        return _Attachments(self._svc)


class _Attachments:
    def __init__(self, svc: FakeGmailService) -> None:
        self._svc = svc

    def get(self, **kwargs: Any) -> _Request:
        self._svc.calls.append(("messages.attachments.get", kwargs))
        key = (kwargs["messageId"], kwargs["id"])
        if key not in self._svc.attachment_responses:
            raise KeyError(f"FakeGmailService has no staged attachment for {key!r}")
        return _Request(self._svc.attachment_responses[key])


class _Labels:
    def __init__(self, svc: FakeGmailService) -> None:
        self._svc = svc

    def list(self, **kwargs: Any) -> _Request:
        self._svc.calls.append(("labels.list", kwargs))
        return _Request(self._svc.labels_response)


class _Users:
    def __init__(self, svc: FakeGmailService) -> None:
        self._svc = svc

    def getProfile(self, userId: str) -> _Request:
        self._svc.calls.append(("getProfile", {"userId": userId}))
        return _Request(self._svc.profile_response)

    def messages(self) -> _Messages:
        return _Messages(self._svc)

    def labels(self) -> _Labels:
        return _Labels(self._svc)


class FakeGmailService:
    """Mimics googleapiclient's chained-call gmail service: `users().messages().list(...).execute()`."""

    def __init__(self) -> None:
        self.list_response: Any = {"messages": []}
        self.get_responses: dict[str, dict[str, Any]] = {}
        self.get_errors: dict[str, Exception] = {}
        self.send_response: dict[str, Any] = {"id": "sent-id-1"}
        self.modify_response: dict[str, Any] = {}
        self.labels_response: dict[str, Any] = {"labels": []}
        self.profile_response: dict[str, Any] = {"emailAddress": "test@example.com"}
        # (message_id, attachment_id) -> attachments.get response ({"data": b64url, ...})
        self.attachment_responses: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._list_page_idx = 0

    def users(self) -> _Users:
        return _Users(self)


class FakeAuthProvider:
    """Structurally-compatible `GmailAuthProvider` for tests. Returns a constant token."""

    def __init__(self, token: str = "fake-access-token") -> None:
        self._token = token

    async def access_token(self) -> str:
        return self._token


def stage_gmail_message(
    msg_id: str,
    *,
    thread_id: str = "thread-1",
    from_addr: str = '"Alice" <alice@example.com>',
    to_addr: str = "me@example.com",
    subject: str = "Hello",
    body_text: str = "Plain body",
    body_html: str | None = None,
    labels: list[str] | None = None,
    internal_ms: int = 1748169600000,  # 2025-05-25T10:00:00Z
    in_reply_to: str | None = None,
    references: str | None = None,
    message_id_header: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a Gmail-shaped message JSON for `messages().get(format='full')`."""
    headers: list[dict[str, str]] = [
        {"name": "From", "value": from_addr},
        {"name": "To", "value": to_addr},
        {"name": "Subject", "value": subject},
    ]
    if in_reply_to:
        headers.append({"name": "In-Reply-To", "value": in_reply_to})
    if references:
        headers.append({"name": "References", "value": references})
    if message_id_header:
        headers.append({"name": "Message-ID", "value": message_id_header})

    payload: dict[str, Any] = {"mimeType": "multipart/alternative", "headers": headers, "parts": []}
    if body_text:
        payload["parts"].append(
            {
                "mimeType": "text/plain",
                "body": {
                    "data": base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ascii")
                },
            }
        )
    if body_html:
        payload["parts"].append(
            {
                "mimeType": "text/html",
                "body": {
                    "data": base64.urlsafe_b64encode(body_html.encode("utf-8")).decode("ascii")
                },
            }
        )
    for att in attachments or []:
        # Gmail's attachment parts carry a filename and a body.attachmentId
        # (the bytes come from a separate attachments.get call).
        payload["parts"].append(
            {
                "mimeType": att.get("mimeType", "application/zip"),
                "filename": att["filename"],
                "body": {
                    "attachmentId": att["attachmentId"],
                    "size": att.get("size", 0),
                },
            }
        )

    return {
        "id": msg_id,
        "threadId": thread_id,
        "internalDate": str(internal_ms),
        "labelIds": labels or [],
        "payload": payload,
    }


def http_error(status: int) -> HttpError:
    """Construct an HttpError with a given status code."""

    class _Resp:
        def __init__(self, s: int) -> None:
            self.status = s
            self.reason = f"HTTP {s}"

    return HttpError(resp=_Resp(status), content=b'{"error":"x"}')
