"""End-to-end test for the email_triage workflow.

Drives the full workflow against `FakeBedrock` + `FakeGmailService`:
loads the YAML, fires it with each fixture, asserts the agent's
tool calls landed and the deterministic record step parsed the agent's
JSON correctly.

Replay-mode: no network, no AWS, no real Gmail. The `FakeBedrock`
responses are constructed per-fixture; the staged `FakeGmailService`
responses simulate Gmail's label-id resolution + send response shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from tests._email_fakes import FakeAuthProvider, FakeGmailService
from workflow_platform.connectors.email import GmailConnector
from workflow_platform.engine import (
    ToolCatalog,
    WorkflowEngine,
    default_function_registry,
)
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.tools.email import EmailLabelApplyTool, EmailSendTool
from workflow_platform.workflow import load_definition_from_file
from workflow_platform.world import mock_world

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / "examples" / "email_triage" / "workflow.yaml"
FIXTURES_DIR = REPO_ROOT / "examples" / "email_triage" / "fixtures"


# --- shared fixtures ---


def _load_fixture(name: str) -> dict[str, Any]:
    return dict(json.loads((FIXTURES_DIR / name).read_text()))


def _label_response() -> dict[str, Any]:
    """Pre-staged Gmail label list — system + the five triage labels."""
    return {
        "labels": [
            {"id": "INBOX", "name": "INBOX"},
            {"id": "UNREAD", "name": "UNREAD"},
            {"id": "Label_T01", "name": "triaged/urgent"},
            {"id": "Label_T02", "name": "triaged/fyi"},
            {"id": "Label_T03", "name": "triaged/spam"},
            {"id": "Label_T04", "name": "triaged/personal"},
            {"id": "Label_T05", "name": "triaged/awaiting-reply"},
        ]
    }


def _build_engine(bedrock: FakeBedrock, svc: FakeGmailService | None = None) -> WorkflowEngine:
    svc = svc if svc is not None else FakeGmailService()
    svc.labels_response = _label_response()
    svc.send_response = {"id": "sent-by-agent-1"}
    connector = GmailConnector(
        account="intelligent.workflow.engine@quentinspencer.com",
        auth_provider=FakeAuthProvider(),
        service=svc,
    )
    return WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=default_function_registry(),
        tools=ToolCatalog([EmailSendTool(connector), EmailLabelApplyTool(connector)]),
        bedrock=bedrock,
        world=mock_world(),
    )


# --- the example artifacts load ---


def test_workflow_yaml_loads_and_has_expected_shape() -> None:
    definition = load_definition_from_file(WORKFLOW_PATH)
    assert definition.id == "email-triage"
    assert definition.trigger.type == "gmail_poll"
    assert [s.id for s in definition.steps] == ["triage", "record"]
    triage = definition.steps[0]
    assert triage.type == "agentic"
    assert set(triage.tools) == {"email_send", "email_label_apply"}


def test_fixtures_are_all_valid_email_messages() -> None:
    """Every committed fixture must parse as a valid EmailMessage —
    otherwise the workflow would never see them in production either."""
    from workflow_platform.connectors.email import EmailMessage

    fixture_files = sorted(FIXTURES_DIR.glob("*.json"))
    assert len(fixture_files) == 5
    for path in fixture_files:
        payload = json.loads(path.read_text())
        # Will raise ValidationError if any fixture has the wrong shape.
        EmailMessage.model_validate(payload)


# --- end-to-end happy-path scenarios ---


async def test_urgent_email_triage_calls_label_apply_and_records() -> None:
    """Fixture 01 (urgent meeting moved): agent applies the triage label
    and emits a JSON record. Reply optional — this test goes label-only
    to keep the bedrock response minimal."""
    payload = _load_fixture("01_urgent_meeting_moved.json")
    bedrock = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[
                    (
                        "tu1",
                        "email_label_apply",
                        {
                            "message_id": payload["message_id"],
                            "labels": ["triaged/urgent"],
                        },
                    )
                ]
            ),
            text_response(
                json.dumps(
                    {
                        "category": "urgent",
                        "confidence": 0.9,
                        "reply_drafted": False,
                        "labels_applied": ["triaged/urgent"],
                        "summary": "Standup moved same-day; requires acknowledgement.",
                    }
                )
            ),
        ]
    )
    svc = FakeGmailService()
    engine = _build_engine(bedrock, svc)
    definition = load_definition_from_file(WORKFLOW_PATH)

    instance = await engine.run(definition, trigger_payload=payload)

    assert instance.state.value == "completed"

    # FakeGmailService observed the label apply call.
    modify_calls = [kw for (m, kw) in svc.calls if m == "messages.modify"]
    assert len(modify_calls) == 1
    assert modify_calls[0]["id"] == payload["message_id"]
    assert modify_calls[0]["body"] == {"addLabelIds": ["Label_T01"]}

    # The record step parsed the agent's JSON into structured fields.
    steps = await engine.repositories.steps.list_by_instance(instance.id)
    record_step = next(s for s in steps if s.step_id == "record")
    assert record_step.output is not None
    assert record_step.output["parse_ok"] is True
    assert record_step.output["category"] == "urgent"
    assert record_step.output["confidence"] == 0.9
    assert record_step.output["reply_drafted"] is False
    assert record_step.output["labels_applied"] == ["triaged/urgent"]
    assert record_step.output["label_count"] == 1


async def test_awaiting_reply_triggers_email_send_and_label() -> None:
    """Fixture 05 (vendor follow-up): the agent both replies and labels."""
    payload = _load_fixture("05_awaiting_reply.json")
    bedrock = FakeBedrock(
        [
            # First turn: agent calls both tools at once.
            tool_use_response(
                tool_uses=[
                    (
                        "tu1",
                        "email_send",
                        {
                            "to": [{"address": "procurement@aws-enterprise-sales.com"}],
                            "subject": "Re: " + payload["subject"],
                            "body_text": (
                                "Thanks for the follow-up — still discussing internally. "
                                "Will get back to you by end of next week.\n\n— Workflow Engine"
                            ),
                            "reply_to_message_id": payload["message_id"],
                        },
                    ),
                    (
                        "tu2",
                        "email_label_apply",
                        {
                            "message_id": payload["message_id"],
                            "labels": ["triaged/awaiting-reply"],
                        },
                    ),
                ]
            ),
            text_response(
                json.dumps(
                    {
                        "category": "awaiting-reply",
                        "confidence": 0.8,
                        "reply_drafted": True,
                        "labels_applied": ["triaged/awaiting-reply"],
                        "summary": "Vendor circling back on quote; sent ack with timeline.",
                    }
                )
            ),
        ]
    )
    svc = FakeGmailService()
    # The connector fetches the prior message when building a reply —
    # stage a stub response for the reply_to_message_id lookup.
    svc.get_responses[payload["message_id"]] = {
        "id": payload["message_id"],
        "threadId": payload["thread_id"],
        "internalDate": "0",
        "payload": {
            "headers": [
                {"name": "Message-ID", "value": payload["headers"]["Message-ID"]},
            ]
        },
    }
    engine = _build_engine(bedrock, svc)
    definition = load_definition_from_file(WORKFLOW_PATH)

    instance = await engine.run(definition, trigger_payload=payload)
    assert instance.state.value == "completed"

    # Both tool calls happened.
    assert any(m == "messages.send" for (m, _) in svc.calls)
    assert any(m == "messages.modify" for (m, _) in svc.calls)

    steps = await engine.repositories.steps.list_by_instance(instance.id)
    record_step = next(s for s in steps if s.step_id == "record")
    assert record_step.output is not None
    assert record_step.output["category"] == "awaiting-reply"
    assert record_step.output["reply_drafted"] is True
    assert record_step.output["labels_applied"] == ["triaged/awaiting-reply"]


async def test_spam_triage_does_not_send_reply() -> None:
    """Fixture 03 (phishing): agent labels as spam, does NOT call email_send.
    The rubric explicitly forbids replying to spam, so we assert the
    FakeGmailService records zero send calls."""
    payload = _load_fixture("03_spam_phishing.json")
    bedrock = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[
                    (
                        "tu1",
                        "email_label_apply",
                        {
                            "message_id": payload["message_id"],
                            "labels": ["triaged/spam"],
                        },
                    )
                ]
            ),
            text_response(
                json.dumps(
                    {
                        "category": "spam",
                        "confidence": 0.98,
                        "reply_drafted": False,
                        "labels_applied": ["triaged/spam"],
                        "summary": "Phishing pattern: lookalike sender domain, urgency tactics.",
                    }
                )
            ),
        ]
    )
    svc = FakeGmailService()
    engine = _build_engine(bedrock, svc)
    definition = load_definition_from_file(WORKFLOW_PATH)
    instance = await engine.run(definition, trigger_payload=payload)
    assert instance.state.value == "completed"

    # No reply was sent.
    assert not any(m == "messages.send" for (m, _) in svc.calls)


# --- record_email_triage robustness ---


async def test_record_email_triage_handles_unparseable_output_without_failing() -> None:
    """If the agent goes off-script and emits prose instead of JSON, the
    workflow should still complete with parse_ok=False so the run is
    queryable. Mirrors the resilience of record_pr_triage / record_paper_triage."""
    payload = _load_fixture("02_fyi_newsletter.json")
    bedrock = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[
                    (
                        "tu1",
                        "email_label_apply",
                        {
                            "message_id": payload["message_id"],
                            "labels": ["triaged/fyi"],
                        },
                    )
                ]
            ),
            text_response("This is a newsletter, definitely fyi, no reply needed."),
        ]
    )
    engine = _build_engine(bedrock)
    definition = load_definition_from_file(WORKFLOW_PATH)
    instance = await engine.run(definition, trigger_payload=payload)
    assert instance.state.value == "completed"

    steps = await engine.repositories.steps.list_by_instance(instance.id)
    record_step = next(s for s in steps if s.step_id == "record")
    assert record_step.output is not None
    assert record_step.output["parse_ok"] is False
    assert "raw" in record_step.output
    assert "newsletter" in record_step.output["raw"]


# --- record_email_triage unit-level (parsing & schema) ---


def test_extract_email_triage_picks_up_all_documented_fields() -> None:
    from workflow_platform.engine.functions import _extract_email_triage

    raw = json.dumps(
        {
            "category": "urgent",
            "confidence": 0.85,
            "reply_drafted": True,
            "labels_applied": ["triaged/urgent", "needs-followup"],
            "summary": "x",
        }
    )
    out = _extract_email_triage(raw)
    assert out == {
        "category": "urgent",
        "confidence": 0.85,
        "reply_drafted": True,
        "labels_applied": ["triaged/urgent", "needs-followup"],
        "label_count": 2,
        "summary": "x",
    }


@pytest.mark.parametrize(
    "raw,expected_none_reason",
    [
        ("not json", "no JSON object"),
        ('"a string, not an object"', "not a dict"),
        ("{}", "no recognized fields"),
    ],
)
def test_extract_email_triage_rejects_unparseable_or_empty(
    raw: str, expected_none_reason: str
) -> None:
    from workflow_platform.engine.functions import _extract_email_triage

    assert _extract_email_triage(raw) is None, expected_none_reason


def test_extract_email_triage_drops_wrong_types() -> None:
    """Wrong-typed fields are dropped, not raised — matches the resilience
    of the other record_* extractors."""
    from workflow_platform.engine.functions import _extract_email_triage

    raw = json.dumps(
        {
            "category": 42,  # wrong type
            "confidence": "high",  # wrong type
            "reply_drafted": "yes",  # wrong type
            "labels_applied": "triaged/fyi",  # wrong type (should be list)
            "summary": "ok",
        }
    )
    out = _extract_email_triage(raw)
    # Only the well-typed `summary` survives.
    assert out == {"summary": "ok"}


# --- Tolerant-parse rescue (json-repair fallback) ---


def test_extract_email_triage_rescues_unescaped_inner_quotes() -> None:
    """The actual failure case from the 1000-message validation run:
    agent emits valid-looking JSON but with unescaped double quotes
    inside the `summary` value. Strict json.loads fails; json-repair
    recovers it."""
    from workflow_platform.engine.functions import _extract_email_triage

    raw = (
        '{"category":"spam","confidence":0.92,"reply_drafted":false,'
        '"labels_applied":["triaged/spam"],"summary":"Unsolicited '
        'marketing email using urgency ("final notice") to pressure '
        "warranty plan purchase.\"}"
    )
    out = _extract_email_triage(raw)
    assert out is not None
    assert out["category"] == "spam"
    assert out["confidence"] == 0.92
    assert out["reply_drafted"] is False
    assert out["labels_applied"] == ["triaged/spam"]
    assert "final notice" in out["summary"]


def test_extract_email_triage_rescues_trailing_comma() -> None:
    """LLMs occasionally trail a comma after the last field."""
    from workflow_platform.engine.functions import _extract_email_triage

    raw = '{"category":"fyi","confidence":0.95,"reply_drafted":false,"summary":"x",}'
    out = _extract_email_triage(raw)
    assert out is not None
    assert out["category"] == "fyi"


def test_extract_email_triage_rescues_single_quoted_strings() -> None:
    """Some agents (esp. after few-shot prompting with Python-style
    examples) emit single-quoted strings. json-repair handles."""
    from workflow_platform.engine.functions import _extract_email_triage

    raw = "{'category':'personal','confidence':0.9,'reply_drafted':false,'summary':'note'}"
    out = _extract_email_triage(raw)
    assert out is not None
    assert out["category"] == "personal"
    assert out["summary"] == "note"


def test_extract_email_triage_returns_none_on_truly_unparseable() -> None:
    """Tolerance has limits — completely shapeless text returns None,
    not garbage. Pin this so a future json-repair upgrade can't silently
    start "recovering" things that should fail."""
    from workflow_platform.engine.functions import _extract_email_triage

    # No braces, no structure, just prose.
    assert _extract_email_triage("I think this email is fyi.") is None
