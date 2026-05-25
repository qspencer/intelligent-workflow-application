"""Tests for `workflow_platform.connectors.email.models`.

These models are the contract every email connector implements against
and the format that crosses into the engine's audit log + step output.
The tests are deliberately exhaustive on shape — if these regress,
every email workflow regresses with them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from workflow_platform.connectors.email import (
    EmailAddress,
    EmailMessage,
    EmailSendRequest,
)

# --- EmailAddress ---


def test_email_address_minimal() -> None:
    addr = EmailAddress(address="x@example.com")
    assert addr.address == "x@example.com"
    assert addr.name is None


def test_email_address_with_name() -> None:
    addr = EmailAddress(address="x@example.com", name="X User")
    assert addr.name == "X User"


def test_email_address_round_trip() -> None:
    src = EmailAddress(address="alice@example.com", name="Alice")
    dumped = src.model_dump()
    assert dumped == {"address": "alice@example.com", "name": "Alice"}
    restored = EmailAddress.model_validate(dumped)
    assert restored == src


# --- EmailMessage ---


def _valid_message_dict() -> dict[str, object]:
    return {
        "provider": "gmail",
        "message_id": "msg-123",
        "thread_id": "thr-1",
        "from_address": {"address": "sender@example.com", "name": "Sender"},
        "to": [{"address": "recipient@example.com"}],
        "subject": "Hello",
        "body_text": "Plain body",
        "received_at": "2026-05-25T10:00:00+00:00",
    }


def test_email_message_minimal_round_trip() -> None:
    msg = EmailMessage.model_validate(_valid_message_dict())
    assert msg.provider == "gmail"
    assert msg.message_id == "msg-123"
    assert msg.from_address.address == "sender@example.com"
    assert msg.to[0].address == "recipient@example.com"
    # Defaults populate as empty containers, not None.
    assert msg.cc == []
    assert msg.bcc == []
    assert msg.labels == []
    assert msg.headers == {}
    assert msg.thread_id == "thr-1"
    assert msg.body_html is None
    assert msg.in_reply_to is None


def test_email_message_json_round_trip() -> None:
    src = EmailMessage.model_validate(_valid_message_dict())
    payload = src.model_dump(mode="json")
    restored = EmailMessage.model_validate(payload)
    assert restored == src


def test_email_message_provider_literal_rejects_unknown() -> None:
    bad = _valid_message_dict() | {"provider": "yahoo"}
    with pytest.raises(ValidationError):
        EmailMessage.model_validate(bad)


def test_email_message_requires_to_non_empty() -> None:
    bad = _valid_message_dict() | {"to": []}
    with pytest.raises(ValidationError):
        EmailMessage.model_validate(bad)


def test_email_message_requires_received_at() -> None:
    bad = dict(_valid_message_dict())
    del bad["received_at"]
    with pytest.raises(ValidationError):
        EmailMessage.model_validate(bad)


def test_email_message_received_at_is_datetime() -> None:
    msg = EmailMessage.model_validate(_valid_message_dict())
    assert isinstance(msg.received_at, datetime)
    assert msg.received_at == datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)


def test_email_message_preserves_headers_and_labels() -> None:
    payload = _valid_message_dict() | {
        "headers": {"X-Spam-Score": "0.1"},
        "labels": ["INBOX", "UNREAD"],
        "in_reply_to": "<earlier@example.com>",
        "body_html": "<p>hi</p>",
        "cc": [{"address": "cc@example.com"}],
    }
    msg = EmailMessage.model_validate(payload)
    assert msg.headers == {"X-Spam-Score": "0.1"}
    assert msg.labels == ["INBOX", "UNREAD"]
    assert msg.in_reply_to == "<earlier@example.com>"
    assert msg.body_html == "<p>hi</p>"
    assert len(msg.cc) == 1


# --- EmailSendRequest ---


def test_send_request_minimal() -> None:
    req = EmailSendRequest(
        to=[EmailAddress(address="alice@example.com")],
        subject="Re: Hello",
        body_text="Body",
    )
    assert req.subject == "Re: Hello"
    assert req.reply_to_message_id is None
    assert req.labels_to_apply == []


def test_send_request_with_threading() -> None:
    req = EmailSendRequest(
        to=[EmailAddress(address="alice@example.com")],
        subject="Re: Hello",
        body_text="Body",
        reply_to_message_id="<earlier@example.com>",
        labels_to_apply=["triaged/urgent"],
    )
    assert req.reply_to_message_id == "<earlier@example.com>"
    assert req.labels_to_apply == ["triaged/urgent"]


def test_send_request_requires_to_non_empty() -> None:
    with pytest.raises(ValidationError):
        EmailSendRequest(to=[], subject="x", body_text="y")


def test_send_request_round_trip_via_dict() -> None:
    """The EmailConnector.send default impl validates a dict payload —
    this asserts the dict shape an agent tool would build."""
    payload = {
        "to": [{"address": "alice@example.com"}],
        "cc": [{"address": "bob@example.com"}],
        "subject": "Re: Hello",
        "body_text": "Body",
        "body_html": "<p>Body</p>",
        "reply_to_message_id": "<src@example.com>",
        "labels_to_apply": ["triaged/urgent"],
    }
    req = EmailSendRequest.model_validate(payload)
    assert req.to[0].address == "alice@example.com"
    assert req.cc[0].address == "bob@example.com"
    assert req.labels_to_apply == ["triaged/urgent"]
    # Round-trip preserves the fields that were actually set (exclude_unset
    # drops default-populated fields like bcc=[] and nested name=None).
    assert req.model_dump(mode="json", exclude_unset=True) == payload


def test_send_request_rejects_unknown_fields_silently_kept_default() -> None:
    """Pydantic v2 ignores unknown fields by default; this test pins that
    behavior so an agent passing extra keys doesn't crash the send tool."""
    payload = {
        "to": [{"address": "alice@example.com"}],
        "subject": "x",
        "body_text": "y",
        "extra_unknown_field": "ignored",
    }
    req = EmailSendRequest.model_validate(payload)
    assert req.to[0].address == "alice@example.com"
    # Dump doesn't carry the unknown field.
    assert "extra_unknown_field" not in req.model_dump()
