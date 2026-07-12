"""Tests for `GmailConnector`. Uses a hand-written `FakeGmailService` to
mimic googleapiclient's chained call shape (`svc.users().messages().list(...).execute()`).
No network, no AWS, no real Gmail.

Covers:
  - poll_inbox happy path + pagination
  - send_email happy path + reply threading (In-Reply-To + References)
  - apply_labels with custom-label name resolution
  - error mapping (404 → GmailMessageNotFound, missing label → GmailLabelNotFound)
"""

from __future__ import annotations

import base64
from email import message_from_bytes
from typing import Any

import pytest
from googleapiclient.errors import HttpError

from tests._email_fakes import (
    FakeAuthProvider,
    FakeGmailService,
    http_error,
    stage_gmail_message,
)
from workflow_platform.connectors.email import (
    EmailAddress,
    EmailMessage,
    EmailSendRequest,
    GmailConnector,
    GmailLabelNotFound,
    GmailMessageNotFound,
)


def _make_connector(svc: FakeGmailService | None = None) -> GmailConnector:
    return GmailConnector(
        account="intelligent.workflow.engine@quentinspencer.com",
        auth_provider=FakeAuthProvider(),
        service=svc if svc is not None else FakeGmailService(),
    )


# ---------- poll_inbox ----------


async def test_poll_inbox_returns_parsed_messages() -> None:
    svc = FakeGmailService()
    svc.list_response = {"messages": [{"id": "m-1"}, {"id": "m-2"}]}
    svc.get_responses["m-1"] = stage_gmail_message(
        "m-1", subject="Hi", from_addr="alice@example.com", labels=["INBOX"]
    )
    svc.get_responses["m-2"] = stage_gmail_message(
        "m-2",
        subject="Hello",
        from_addr='"Bob" <bob@example.com>',
        body_html="<p>HTML body</p>",
        labels=["INBOX", "UNREAD"],
    )
    conn = _make_connector(svc)

    messages = await conn.poll_inbox()

    assert len(messages) == 2
    assert messages[0].message_id == "m-1"
    assert messages[0].subject == "Hi"
    assert messages[0].from_address == EmailAddress(address="alice@example.com")
    assert messages[0].body_text == "Plain body"
    assert messages[0].body_html is None
    assert messages[0].labels == ["INBOX"]
    assert messages[1].from_address == EmailAddress(address="bob@example.com", name="Bob")
    assert messages[1].body_html == "<p>HTML body</p>"
    assert messages[1].labels == ["INBOX", "UNREAD"]


async def test_poll_inbox_paginates_to_max_messages() -> None:
    svc = FakeGmailService()
    # Two pages: 2 + 2. Caller asks for max_messages=3 → only 3 results returned,
    # second page tail dropped, no third page fetched.
    svc.list_response = [
        {"messages": [{"id": "p1-a"}, {"id": "p1-b"}], "nextPageToken": "tok-2"},
        {"messages": [{"id": "p2-a"}, {"id": "p2-b"}]},
    ]
    for mid in ("p1-a", "p1-b", "p2-a", "p2-b"):
        svc.get_responses[mid] = stage_gmail_message(mid)
    conn = _make_connector(svc)

    messages = await conn.poll_inbox(max_messages=3)

    assert [m.message_id for m in messages] == ["p1-a", "p1-b", "p2-a"]
    list_calls = [kw for (m, kw) in svc.calls if m == "messages.list"]
    assert len(list_calls) == 2
    assert list_calls[0].get("pageToken") is None
    assert list_calls[1]["pageToken"] == "tok-2"


async def test_poll_inbox_builds_after_query_from_since() -> None:
    from datetime import UTC, datetime

    svc = FakeGmailService()
    svc.list_response = {"messages": []}
    conn = _make_connector(svc)

    cutoff = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    await conn.poll_inbox(since=cutoff, label="INBOX", max_messages=5)

    list_call = next(kw for (m, kw) in svc.calls if m == "messages.list")
    assert list_call["q"] == f"after:{int(cutoff.timestamp())} label:INBOX"
    assert list_call["maxResults"] == 5


async def test_poll_inbox_appends_extra_query() -> None:
    svc = FakeGmailService()
    svc.list_response = {"messages": []}
    conn = _make_connector(svc)

    await conn.poll_inbox(label="INBOX", query="has:attachment (filename:zip OR filename:gz)")

    list_call = next(kw for (m, kw) in svc.calls if m == "messages.list")
    assert list_call["q"] == "label:INBOX has:attachment (filename:zip OR filename:gz)"


async def test_poll_inbox_parses_attachment_metadata() -> None:
    svc = FakeGmailService()
    svc.list_response = {"messages": [{"id": "m-att"}]}
    svc.get_responses["m-att"] = stage_gmail_message(
        "m-att",
        subject="Report domain: example.com",
        attachments=[
            {
                "filename": "google.com!example.com!1700000000!1700086400.zip",
                "mimeType": "application/zip",
                "attachmentId": "att-1",
                "size": 1234,
            }
        ],
    )
    conn = _make_connector(svc)

    (msg,) = await conn.poll_inbox()

    (att,) = msg.attachments
    assert att.filename == "google.com!example.com!1700000000!1700086400.zip"
    assert att.mime_type == "application/zip"
    assert att.attachment_id == "att-1"
    assert att.size_bytes == 1234


async def test_download_attachment_decodes_base64url() -> None:
    svc = FakeGmailService()
    original = b"\x00\x01zip-bytes-\xff"
    # Gmail returns unpadded base64url — the connector must re-pad.
    encoded = base64.urlsafe_b64encode(original).decode("ascii").rstrip("=")
    svc.attachment_responses[("m-att", "att-1")] = {"data": encoded, "size": len(original)}
    conn = _make_connector(svc)

    data = await conn.download_attachment("m-att", "att-1")

    assert data == original
    call = next(kw for (m, kw) in svc.calls if m == "messages.attachments.get")
    assert call == {"userId": "me", "messageId": "m-att", "id": "att-1"}


# ---------- service rebuild on token rotation ----------


async def test_service_rebuilds_when_access_token_rotates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the self-built service bakes in a bare access token (no
    refresh fields). Caching it forever meant every Gmail call failed with
    RefreshError one hour into a long-running process — first surfaced the
    day the backend started running as a persistent systemd service. The
    connector must consult the auth provider per call and rebuild the
    service when the token rotates (and NOT rebuild when it hasn't)."""
    import googleapiclient.discovery as discovery

    class RotatingAuth:
        def __init__(self) -> None:
            self.token = "token-1"

        async def access_token(self) -> str:
            return self.token

    built: list[str] = []

    def fake_build(api: str, version: str, *, credentials: Any, cache_discovery: bool) -> object:
        built.append(credentials.token)
        return object()

    monkeypatch.setattr(discovery, "build", fake_build)

    auth = RotatingAuth()
    conn = GmailConnector(account="a@b.com", auth_provider=auth)  # no injected service

    s1 = await conn._get_service()
    s2 = await conn._get_service()
    assert s1 is s2 and built == ["token-1"]  # cached while token unchanged

    auth.token = "token-2"  # provider refreshed (old token expired)
    s3 = await conn._get_service()
    assert s3 is not s1
    assert built == ["token-1", "token-2"]


async def test_injected_service_is_never_rebuilt() -> None:
    """Test-injected fakes don't expire; the rotation logic must not touch
    them (or the auth provider)."""

    class ExplodingAuth:
        async def access_token(self) -> str:
            raise AssertionError("auth provider must not be consulted for injected services")

    svc = FakeGmailService()
    conn = GmailConnector(account="a@b.com", auth_provider=ExplodingAuth(), service=svc)
    assert await conn._get_service() is svc


# ---------- send_email ----------


async def test_send_email_simple_no_reply() -> None:
    svc = FakeGmailService()
    svc.send_response = {"id": "sent-42"}
    conn = _make_connector(svc)

    sent_id = await conn.send_email(
        EmailSendRequest(
            to=[EmailAddress(address="alice@example.com", name="Alice")],
            subject="Re: hello",
            body_text="Body text",
        )
    )

    assert sent_id == "sent-42"
    send_call = next(kw for (m, kw) in svc.calls if m == "messages.send")
    raw_b64 = send_call["body"]["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    parsed = message_from_bytes(raw_bytes)
    assert parsed["From"] == "intelligent.workflow.engine@quentinspencer.com"
    assert parsed["To"] == '"Alice" <alice@example.com>'
    assert parsed["Subject"] == "Re: hello"
    # No prior message → no threading headers
    assert parsed["In-Reply-To"] is None
    assert parsed["References"] is None
    assert "threadId" not in send_call["body"]


async def test_send_email_reply_threading_builds_references_chain() -> None:
    svc = FakeGmailService()
    # Prior message has its own Message-ID and an existing References chain.
    svc.get_responses["src-msg"] = {
        "id": "src-msg",
        "threadId": "thr-7",
        "internalDate": "0",
        "payload": {
            "headers": [
                {"name": "Message-ID", "value": "<earliest@example.com>"},
                {"name": "References", "value": "<root@example.com> <middle@example.com>"},
            ]
        },
    }
    svc.send_response = {"id": "reply-id"}
    conn = _make_connector(svc)

    await conn.send_email(
        EmailSendRequest(
            to=[EmailAddress(address="alice@example.com")],
            subject="Re: thread",
            body_text="My reply",
            reply_to_message_id="src-msg",
        )
    )

    send_call = next(kw for (m, kw) in svc.calls if m == "messages.send")
    raw_bytes = base64.urlsafe_b64decode(send_call["body"]["raw"].encode("ascii"))
    parsed = message_from_bytes(raw_bytes)
    assert parsed["In-Reply-To"] == "<earliest@example.com>"
    # References must carry the prior chain + the prior message's id.
    assert parsed["References"] == "<root@example.com> <middle@example.com> <earliest@example.com>"
    # And the threadId from the prior message rides along on the send body, so
    # Gmail groups the reply with its source.
    assert send_call["body"]["threadId"] == "thr-7"


async def test_send_email_reply_when_prior_has_no_references_header() -> None:
    """Common case: replying to the first message in a thread. References
    becomes just the prior Message-ID, not an empty-prefix string."""
    svc = FakeGmailService()
    svc.get_responses["src-msg"] = {
        "id": "src-msg",
        "threadId": "thr-1",
        "internalDate": "0",
        "payload": {
            "headers": [
                {"name": "Message-ID", "value": "<only@example.com>"},
            ]
        },
    }
    svc.send_response = {"id": "reply-id"}
    conn = _make_connector(svc)

    await conn.send_email(
        EmailSendRequest(
            to=[EmailAddress(address="alice@example.com")],
            subject="Re: first",
            body_text="reply",
            reply_to_message_id="src-msg",
        )
    )

    send_call = next(kw for (m, kw) in svc.calls if m == "messages.send")
    parsed = message_from_bytes(base64.urlsafe_b64decode(send_call["body"]["raw"]))
    assert parsed["In-Reply-To"] == "<only@example.com>"
    assert parsed["References"] == "<only@example.com>"


async def test_send_email_with_html_alternative() -> None:
    svc = FakeGmailService()
    conn = _make_connector(svc)

    await conn.send_email(
        EmailSendRequest(
            to=[EmailAddress(address="alice@example.com")],
            subject="HTML test",
            body_text="plain",
            body_html="<p>html</p>",
        )
    )

    send_call = next(kw for (m, kw) in svc.calls if m == "messages.send")
    raw = base64.urlsafe_b64decode(send_call["body"]["raw"])
    parsed = message_from_bytes(raw)
    # Multipart/alternative is built by set_content + add_alternative
    assert parsed.is_multipart()
    types = {
        p.get_content_type()
        for p in parsed.walk()
        if p.get_content_type() != "multipart/alternative"
    }
    assert types == {"text/plain", "text/html"}


async def test_send_email_applies_labels_after_send() -> None:
    svc = FakeGmailService()
    svc.send_response = {"id": "sent-id"}
    svc.labels_response = {"labels": [{"id": "Label_5", "name": "triaged/urgent"}]}
    conn = _make_connector(svc)

    await conn.send_email(
        EmailSendRequest(
            to=[EmailAddress(address="alice@example.com")],
            subject="x",
            body_text="y",
            labels_to_apply=["triaged/urgent"],
        )
    )

    modify_call = next(kw for (m, kw) in svc.calls if m == "messages.modify")
    assert modify_call["id"] == "sent-id"
    assert modify_call["body"] == {"addLabelIds": ["Label_5"]}


# ---------- apply_labels ----------


async def test_apply_labels_resolves_custom_label_name_to_id() -> None:
    svc = FakeGmailService()
    svc.labels_response = {
        "labels": [
            {"id": "INBOX", "name": "INBOX"},
            {"id": "Label_42", "name": "triaged/urgent"},
        ]
    }
    conn = _make_connector(svc)

    await conn.apply_labels("m-1", ["triaged/urgent"])

    modify_call = next(kw for (m, kw) in svc.calls if m == "messages.modify")
    assert modify_call["body"] == {"addLabelIds": ["Label_42"]}


async def test_apply_labels_caches_label_lookup() -> None:
    svc = FakeGmailService()
    svc.labels_response = {"labels": [{"id": "Label_1", "name": "x"}]}
    conn = _make_connector(svc)
    await conn.apply_labels("m-1", ["x"])
    await conn.apply_labels("m-2", ["x"])

    label_list_calls = [m for (m, _) in svc.calls if m == "labels.list"]
    assert len(label_list_calls) == 1, "labels.list should be cached after first lookup"


async def test_apply_labels_unknown_label_raises_gmail_label_not_found() -> None:
    svc = FakeGmailService()
    svc.labels_response = {"labels": [{"id": "Label_1", "name": "exists"}]}
    conn = _make_connector(svc)

    with pytest.raises(GmailLabelNotFound, match="missing"):
        await conn.apply_labels("m-1", ["missing"])


async def test_apply_labels_noop_on_empty_list() -> None:
    svc = FakeGmailService()
    conn = _make_connector(svc)
    await conn.apply_labels("m-1", [])
    assert svc.calls == [], "empty labels should skip all API calls entirely"


# ---------- error mapping ----------


async def test_messages_get_404_maps_to_gmail_message_not_found() -> None:
    svc = FakeGmailService()
    svc.list_response = {"messages": [{"id": "lost"}]}
    svc.get_errors["lost"] = http_error(404)
    conn = _make_connector(svc)

    with pytest.raises(GmailMessageNotFound):
        await conn.poll_inbox()


async def test_messages_get_500_propagates_unchanged() -> None:
    svc = FakeGmailService()
    svc.list_response = {"messages": [{"id": "boom"}]}
    svc.get_errors["boom"] = http_error(500)
    conn = _make_connector(svc)

    with pytest.raises(HttpError) as exc_info:
        await conn.poll_inbox()
    assert exc_info.value.resp.status == 500


# ---------- authenticate ----------


async def test_authenticate_hits_get_profile() -> None:
    svc = FakeGmailService()
    conn = _make_connector(svc)
    await conn.authenticate()
    assert any(m == "getProfile" for (m, _) in svc.calls)


# ---------- inherited from EmailConnector base ---


async def test_health_check_returns_true_when_poll_succeeds() -> None:
    svc = FakeGmailService()
    svc.list_response = {"messages": []}
    conn = _make_connector(svc)
    assert await conn.health_check() is True


async def test_health_check_returns_false_when_poll_raises() -> None:
    svc = FakeGmailService()
    # Stage a 500 on list itself
    svc.list_response = http_error(500)
    conn = _make_connector(svc)
    # The fake list returns whatever value is staged, including exceptions
    # via _Request.execute(). Confirm health_check swallows it.
    assert await conn.health_check() is False


async def test_trigger_poll_advances_cursor_and_returns_dicts() -> None:
    svc = FakeGmailService()
    msg = stage_gmail_message("m-1", internal_ms=1_748_169_600_000)
    svc.list_response = {"messages": [{"id": "m-1"}]}
    svc.get_responses["m-1"] = msg
    conn = _make_connector(svc)

    events = await conn.trigger_poll()
    assert len(events) == 1
    # trigger_poll returns dicts (Connector.trigger_poll contract)
    assert isinstance(events[0], dict)
    assert events[0]["message_id"] == "m-1"
    # Round-trip via EmailMessage to confirm it's a valid payload.
    EmailMessage.model_validate(events[0])

    # Cursor advanced to the message's received_at — next poll should query
    # only newer messages.
    svc.list_response = {"messages": []}
    svc.calls.clear()
    await conn.trigger_poll()
    next_list_call = next(kw for (m, kw) in svc.calls if m == "messages.list")
    assert next_list_call["q"].startswith("after:")
