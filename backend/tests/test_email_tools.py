"""Tests for `EmailSendTool` and `EmailLabelApplyTool`.

Unit tests cover the tool's own logic (params validation, connector error
mapping). One agent-flow integration test verifies that capability denial
through the standard `Agent` dispatch returns the expected
`Capability denied` ToolResult — same code path as the existing file
tools, just exercising the new tool names.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from tests._email_fakes import FakeAuthProvider, FakeGmailService
from workflow_platform.connectors.email import GmailConnector
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
)
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.security import CapabilityPolicy
from workflow_platform.tools.email import EmailLabelApplyTool, EmailSendTool
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _make_connector(svc: FakeGmailService | None = None) -> GmailConnector:
    return GmailConnector(
        account="intelligent.workflow.engine@quentinspencer.com",
        auth_provider=FakeAuthProvider(),
        service=svc if svc is not None else FakeGmailService(),
    )


# ---------- EmailSendTool ----------


async def test_email_send_happy_path() -> None:
    svc = FakeGmailService()
    svc.send_response = {"id": "sent-1"}
    tool = EmailSendTool(_make_connector(svc))

    result = await tool.execute(
        {
            "to": [{"address": "alice@example.com"}],
            "subject": "Hello",
            "body_text": "Body",
        }
    )

    assert result.ok
    assert result.content == {"message_id": "sent-1"}
    send_call = next(kw for (m, kw) in svc.calls if m == "messages.send")
    assert "raw" in send_call["body"]


async def test_email_send_passes_reply_threading_to_connector() -> None:
    """The tool just hands the EmailSendRequest to the connector — the
    connector is what builds References. This test verifies the request
    reaches the connector with `reply_to_message_id` set."""
    svc = FakeGmailService()
    svc.get_responses["src-1"] = {
        "id": "src-1",
        "threadId": "thr-1",
        "internalDate": "0",
        "payload": {
            "headers": [
                {"name": "Message-ID", "value": "<prior@example.com>"},
            ]
        },
    }
    svc.send_response = {"id": "reply-1"}
    tool = EmailSendTool(_make_connector(svc))

    result = await tool.execute(
        {
            "to": [{"address": "alice@example.com"}],
            "subject": "Re: hi",
            "body_text": "reply",
            "reply_to_message_id": "src-1",
        }
    )

    assert result.ok
    # Connector should have fetched the prior message to build the chain.
    assert any(m == "messages.get" and kw["id"] == "src-1" for (m, kw) in svc.calls)


async def test_email_send_rejects_missing_required_field() -> None:
    tool = EmailSendTool(_make_connector())
    result = await tool.execute({"to": [{"address": "a@b.com"}], "subject": "x"})
    assert not result.ok
    assert result.error is not None
    assert "body_text" in result.error.lower() or "missing" in result.error.lower()


async def test_email_send_rejects_empty_recipient_list() -> None:
    """`to` must have at least 1 entry — empty list is a validation error."""
    tool = EmailSendTool(_make_connector())
    result = await tool.execute({"to": [], "subject": "x", "body_text": "y"})
    assert not result.ok
    assert result.error is not None


async def test_email_send_handles_connector_exception() -> None:
    class BoomConnector:
        async def send_email(self, req: Any) -> str:
            raise RuntimeError("smtp server on fire")

    tool = EmailSendTool(BoomConnector())  # type: ignore[arg-type]
    result = await tool.execute({"to": [{"address": "a@b.com"}], "subject": "x", "body_text": "y"})
    assert not result.ok
    assert result.error is not None
    assert "smtp server on fire" in result.error


async def test_email_send_handles_auth_revoked_with_friendly_message() -> None:
    """Auth-revoked is the operator-action case — surface a message that
    points at the consent CLI rather than a raw exception."""
    from workflow_platform.connectors.email.gmail_auth import GmailAuthRevoked

    class RevokedConnector:
        async def send_email(self, req: Any) -> str:
            raise GmailAuthRevoked("revoked")

    tool = EmailSendTool(RevokedConnector())  # type: ignore[arg-type]
    result = await tool.execute({"to": [{"address": "a@b.com"}], "subject": "x", "body_text": "y"})
    assert not result.ok
    assert result.error is not None
    assert "consent" in result.error.lower() or "revoked" in result.error.lower()


def test_email_send_tool_metadata() -> None:
    """Name + schema match the contract in docs/EMAIL_CONNECTOR_PLAN.md."""
    assert EmailSendTool.name == "email_send"
    schema = EmailSendTool.parameters_schema
    assert schema["required"] == ["to", "subject", "body_text"]
    assert "reply_to_message_id" in schema["properties"]
    assert "labels_to_apply" in schema["properties"]


# ---------- EmailLabelApplyTool ----------


async def test_email_label_apply_happy_path() -> None:
    svc = FakeGmailService()
    svc.labels_response = {
        "labels": [
            {"id": "INBOX", "name": "INBOX"},
            {"id": "Label_42", "name": "triaged/urgent"},
        ]
    }
    tool = EmailLabelApplyTool(_make_connector(svc))

    result = await tool.execute({"message_id": "m-1", "labels": ["triaged/urgent"]})

    assert result.ok
    assert result.content == {"message_id": "m-1", "labels_applied": ["triaged/urgent"]}
    modify_call = next(kw for (m, kw) in svc.calls if m == "messages.modify")
    assert modify_call["body"] == {"addLabelIds": ["Label_42"]}


async def test_email_label_apply_rejects_missing_message_id() -> None:
    tool = EmailLabelApplyTool(_make_connector())
    result = await tool.execute({"labels": ["x"]})
    assert not result.ok
    assert result.error is not None
    assert "message_id" in result.error


async def test_email_label_apply_rejects_empty_labels() -> None:
    tool = EmailLabelApplyTool(_make_connector())
    result = await tool.execute({"message_id": "m-1", "labels": []})
    assert not result.ok
    assert result.error is not None
    assert "labels" in result.error.lower()


async def test_email_label_apply_rejects_non_string_labels() -> None:
    tool = EmailLabelApplyTool(_make_connector())
    result = await tool.execute({"message_id": "m-1", "labels": ["ok", 42]})
    assert not result.ok
    assert result.error is not None


async def test_email_label_apply_handles_unknown_label() -> None:
    svc = FakeGmailService()
    svc.labels_response = {"labels": [{"id": "INBOX", "name": "INBOX"}]}
    tool = EmailLabelApplyTool(_make_connector(svc))

    result = await tool.execute({"message_id": "m-1", "labels": ["missing"]})
    assert not result.ok
    assert result.error is not None
    assert "missing" in result.error


def test_email_label_apply_tool_metadata() -> None:
    assert EmailLabelApplyTool.name == "email_label_apply"
    schema = EmailLabelApplyTool.parameters_schema
    assert schema["required"] == ["message_id", "labels"]


# ---------- capability gating through Agent dispatch ----------


async def test_email_send_capability_denial_through_agent() -> None:
    """Integration: an agent calls email_send through a workflow whose
    capability policy does NOT include `email_send`. The Agent's dispatch
    should produce a `Capability denied` ToolResult (visible in the audit
    log). This exercises the same `tool_allowed` gate that protects all
    other tools — verifying the new tool name is subject to it."""
    repos = in_memory_repositories()
    svc = FakeGmailService()
    svc.send_response = {"id": "sent-1"}
    tool = EmailSendTool(_make_connector(svc))

    # Bedrock issues a tool_use for email_send, then ends after the denial.
    bedrock = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[
                    (
                        "u1",
                        "email_send",
                        {
                            "to": [{"address": "alice@example.com"}],
                            "subject": "x",
                            "body_text": "y",
                        },
                    )
                ]
            ),
            text_response("done after denial"),
        ]
    )

    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "Send a reply.",
                    "model": MODEL,
                    "tools": ["email_send"],
                    "policy": {"max_iterations": 2, "max_total_tokens": 1000},
                    # Deliberately empty allowlist — denies ALL tools.
                    "capabilities": CapabilityPolicy(tools=[]).model_dump(),
                }
            ],
            "edges": [],
        }
    )

    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([tool]),
        bedrock=bedrock,
        world=mock_world(),
    )
    instance = await engine.run(definition)

    audit = await repos.audit.list_by_instance(instance.id)
    tool_call = next(e for e in audit if e.action == "tool_call")
    assert "Capability denied" in tool_call.detail["result"]["error"]
    assert "email_send" in tool_call.detail["result"]["error"]

    # And the FakeGmailService never saw a send call — denial was hard.
    assert not any(m == "messages.send" for (m, _kw) in svc.calls)


@pytest.mark.parametrize("tool_name", ["email_send", "email_label_apply"])
def test_email_tools_are_exported_from_package(tool_name: str) -> None:
    """Both tools live under workflow_platform.tools and are importable
    via the top-level __init__."""
    from workflow_platform.tools import EmailLabelApplyTool, EmailSendTool

    names = {EmailSendTool.name, EmailLabelApplyTool.name}
    assert tool_name in names
