"""End-to-end test for the invoice_extraction workflow.

Drives the 7-step workflow (pdf_extract → notify_extracted → extract →
record → notify_validated → route → notify_filed) against `FakeBedrock` +
a `FakeGmailService`. Asserts:

  - Extraction produces structured fields
  - `record_invoice_extraction` computes invariant checks correctly
  - All three notify_* steps fire one `messages.send` each
  - The PDF lands in the per-country routing destination

Replay-mode: no network, no AWS, no real Gmail. The five fixtures live
under `examples/invoice_extraction/fixtures/` and are committed.
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
from workflow_platform.engine.functions import _extract_invoice_fields
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.tools import (
    EmailLabelApplyTool,
    EmailSendTool,
    FileReadTool,
    FileWriteTool,
    PdfExtractTool,
)
from workflow_platform.workflow import load_definition_from_file
from workflow_platform.world import mock_world

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / "examples" / "invoice_extraction" / "workflow.yaml"
FIXTURES_DIR = REPO_ROOT / "examples" / "invoice_extraction" / "fixtures"


# --- shared helpers ---


def _build_engine(bedrock: FakeBedrock, svc: FakeGmailService | None = None) -> WorkflowEngine:
    """Build an engine with both the standard tool surface (pdf_extract,
    file_read/write) and the email tools wired to a fake Gmail connector."""
    svc = svc if svc is not None else FakeGmailService()
    svc.labels_response = {"labels": [{"id": "INBOX", "name": "INBOX"}]}
    svc.send_response = {"id": "sent-notify-1"}
    connector = GmailConnector(
        account="intelligent.workflow.engine@quentinspencer.com",
        auth_provider=FakeAuthProvider(),
        service=svc,
    )
    return WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=default_function_registry(),
        tools=ToolCatalog(
            [
                PdfExtractTool(),
                FileReadTool(),
                FileWriteTool(),
                EmailSendTool(connector),
                EmailLabelApplyTool(connector),
            ]
        ),
        bedrock=bedrock,
        world=mock_world(),
    )


# Each notify_* agent makes exactly one email_send tool call then returns
# the message_id as plain text. The main `extract` step returns a JSON
# field-extraction blob. We need 4 agentic responses per run (one per
# agentic step), each composed of a tool_use response (if applicable)
# plus a final text response after the tool result comes back.
def _bedrock_for_happy_path(extracted_json: dict[str, Any]) -> FakeBedrock:
    return FakeBedrock(
        [
            # notify_extracted: tool_use email_send, then return message_id
            tool_use_response(
                tool_uses=[
                    (
                        "ne1",
                        "email_send",
                        {
                            "to": [{"address": "qrsconsulting@quentinspencer.com"}],
                            "subject": "Invoice intake: PDF extracted",
                            "body_text": "Workflow parsed PDF.",
                        },
                    )
                ]
            ),
            text_response("sent-notify-1"),
            # extract: return JSON directly (no tools)
            text_response(json.dumps(extracted_json)),
            # notify_validated: tool_use, then message_id
            tool_use_response(
                tool_uses=[
                    (
                        "nv1",
                        "email_send",
                        {
                            "to": [{"address": "qrsconsulting@quentinspencer.com"}],
                            "subject": "Invoice extracted: 19562 — $787.41",
                            "body_text": "Fields extracted.",
                        },
                    )
                ]
            ),
            text_response("sent-notify-2"),
            # notify_filed: tool_use, then message_id
            tool_use_response(
                tool_uses=[
                    (
                        "nf1",
                        "email_send",
                        {
                            "to": [{"address": "qrsconsulting@quentinspencer.com"}],
                            "subject": "Invoice filed: 19562",
                            "body_text": "Routed to Germany.",
                        },
                    )
                ]
            ),
            text_response("sent-notify-3"),
        ]
    )


def _liz_thompson_extracted() -> dict[str, Any]:
    """What the agent should produce for the Liz Thompson fixture."""
    return {
        "invoice_number": "19562",
        "customer_name": "Liz Thompson",
        "invoice_date": "2012-12-05",
        "ship_mode": "Same Day",
        "ship_to_city": "Hamburg",
        "ship_to_country": "Germany",
        "subtotal": 636.30,
        "shipping": 151.11,
        "total": 787.41,
        "order_id": "ES-2012-LT1711048-41248",
        "line_items": [
            {
                "item": "Apple Smart Phone, Cordless",
                "category": "Phones, Technology, TEC-PH-3147",
                "quantity": 1,
                "rate": 636.30,
                "amount": 636.30,
            }
        ],
    }


# --- workflow shape + fixtures ---


def test_workflow_yaml_loads_and_has_expected_shape() -> None:
    definition = load_definition_from_file(WORKFLOW_PATH)
    assert definition.id == "invoice-extraction"
    assert definition.trigger.type == "filesystem"
    step_ids = [s.id for s in definition.steps]
    # Linear shape: 4 substantive + 3 notify
    assert step_ids == [
        "pdf_extract",
        "notify_extracted",
        "extract",
        "record",
        "notify_validated",
        "route",
        "notify_filed",
    ]
    notify_steps = [s for s in definition.steps if s.id.startswith("notify_")]
    assert len(notify_steps) == 3
    for s in notify_steps:
        assert s.type == "agentic"
        assert s.tools == ["email_send"]


def test_committed_fixtures_are_present() -> None:
    """5 real invoices + 1 blank-template (for the min_chars guard test)."""
    real_invoices = sorted(p for p in FIXTURES_DIR.glob("invoice_*.pdf"))
    assert len(real_invoices) == 5
    for path in real_invoices:
        assert path.stat().st_size > 5000, f"{path.name} suspiciously small"
    assert (FIXTURES_DIR / "blank_template.pdf").exists()


# --- pdf_extract min_chars guard ---


async def test_pdf_extract_min_chars_rejects_blank_template() -> None:
    """The blank-template PDF (~93 chars of boilerplate) should fail
    pdf_extract early when min_chars is set, so the downstream agent
    doesn't burn inference returning empty-fallback JSON."""
    from workflow_platform.engine.context import WorkflowContext
    from workflow_platform.engine.functions import pdf_extract
    from workflow_platform.engine.registry import StepFailure

    blank_path = FIXTURES_DIR / "blank_template.pdf"
    ctx = WorkflowContext(workflow_id="x", instance_id="x", trigger={"file_path": str(blank_path)})
    with pytest.raises(StepFailure, match="min_chars"):
        await pdf_extract(
            {"filepath": str(blank_path), "min_chars": 150},
            ctx,
            mock_world(),
        )


async def test_pdf_extract_min_chars_zero_or_unset_skips_check() -> None:
    """Without min_chars, the blank PDF extracts to empty fields without
    raising — the prior behavior is preserved when the guard isn't
    configured. Confirms we didn't make blank PDFs a hard failure
    everywhere."""
    from workflow_platform.engine.context import WorkflowContext
    from workflow_platform.engine.functions import pdf_extract

    blank_path = FIXTURES_DIR / "blank_template.pdf"
    ctx = WorkflowContext(workflow_id="x", instance_id="x", trigger={"file_path": str(blank_path)})
    # No min_chars — should succeed and return whatever was extracted.
    out = await pdf_extract({"filepath": str(blank_path)}, ctx, mock_world())
    assert "text" in out
    # Blank template extracts to <150 chars; confirm we're under the threshold
    # that the configured workflow uses (so the test fixture is meaningful).
    assert out["char_count"] < 150


async def test_pdf_extract_min_chars_allows_real_invoice() -> None:
    """Real invoices have ~300+ chars of text; min_chars=150 lets them
    through unchanged."""
    from workflow_platform.engine.context import WorkflowContext
    from workflow_platform.engine.functions import pdf_extract

    real_path = FIXTURES_DIR / "invoice_Liz Thompson_19562.pdf"
    ctx = WorkflowContext(workflow_id="x", instance_id="x", trigger={"file_path": str(real_path)})
    out = await pdf_extract(
        {"filepath": str(real_path), "min_chars": 150},
        ctx,
        mock_world(),
    )
    assert out["char_count"] >= 150
    assert "Liz Thompson" in out["text"]


# --- end-to-end happy path ---


async def test_full_pipeline_happy_path() -> None:
    """One PDF in → 7 steps run → 3 notify emails sent → PDF lands in
    routing destination → record step captures structured fields."""
    pdf_path = FIXTURES_DIR / "invoice_Liz Thompson_19562.pdf"
    svc = FakeGmailService()
    bedrock = _bedrock_for_happy_path(_liz_thompson_extracted())
    engine = _build_engine(bedrock, svc)
    definition = load_definition_from_file(WORKFLOW_PATH)

    # Seed the source PDF into the mock world so pdf_extract can read it.
    payload = pdf_path.read_bytes()
    await engine.world.fs.write_bytes(str(pdf_path), payload)

    instance = await engine.run(definition, trigger_payload={"file_path": str(pdf_path)})

    assert instance.state.value == "completed", instance.error

    # All three notify_* steps fired exactly one messages.send each.
    send_calls = [kw for (m, kw) in svc.calls if m == "messages.send"]
    assert len(send_calls) == 3

    # The record step parsed the extracted JSON + computed invariants.
    steps = await engine.repositories.steps.list_by_instance(instance.id)
    record_step = next(s for s in steps if s.step_id == "record")
    assert record_step.output is not None
    assert record_step.output["parse_ok"] is True
    assert record_step.output["invoice_number"] == "19562"
    assert record_step.output["ship_to_country"] == "Germany"
    assert record_step.output["total"] == 787.41
    # Invariants — Liz Thompson's totals math out cleanly (no discount).
    assert record_step.output["total_balanced"] is True
    assert record_step.output["line_items_sum_matches_subtotal"] is True
    assert record_step.output["invoice_date_iso"] is True

    # Route step copied the PDF into output/germany/.
    route_step = next(s for s in steps if s.step_id == "route")
    assert route_step.output is not None
    assert route_step.output["safe_value"] == "germany"
    assert "/germany/" in route_step.output["destination"]


async def test_pipeline_handles_invariant_failure_without_blocking() -> None:
    """When the agent's math is off, the workflow still completes; the
    invariant check just lands False. Downstream queries find these via
    `total_balanced = false`."""
    bad_extraction = _liz_thompson_extracted() | {
        "shipping": 50.00,  # subtotal - 0 + 50 = 686.30 ≠ total=787.41
    }
    pdf_path = FIXTURES_DIR / "invoice_Liz Thompson_19562.pdf"
    svc = FakeGmailService()
    bedrock = _bedrock_for_happy_path(bad_extraction)
    engine = _build_engine(bedrock, svc)
    definition = load_definition_from_file(WORKFLOW_PATH)
    payload = pdf_path.read_bytes()
    await engine.world.fs.write_bytes(str(pdf_path), payload)

    instance = await engine.run(definition, trigger_payload={"file_path": str(pdf_path)})
    assert instance.state.value == "completed"

    steps = await engine.repositories.steps.list_by_instance(instance.id)
    record_step = next(s for s in steps if s.step_id == "record")
    assert record_step.output is not None
    assert record_step.output["parse_ok"] is True
    assert record_step.output["total_balanced"] is False
    # Workflow still finished + routed.
    route_step = next(s for s in steps if s.step_id == "route")
    assert route_step.output is not None


async def test_pipeline_validates_discounted_invoice_math() -> None:
    """When an invoice has a discount line, the invariant should pass:
    total = subtotal - discount + shipping. Trudy Brown is the live
    example that exposed this rubric gap on the first batch run."""
    discounted = {
        "invoice_number": "26186",
        "customer_name": "Trudy Brown",
        "invoice_date": "2012-09-25",
        "ship_mode": "Standard Class",
        "ship_to_city": "Yangon",
        "ship_to_country": "Myanmar (Burma)",
        "subtotal": 437.72,
        "discount": 74.41,  # 17% off
        "shipping": 85.40,
        "total": 448.71,  # 437.72 - 74.41 + 85.40
        "order_id": "IN-2012-TB2162588-41177",
        "line_items": [
            {
                "item": "KitchenAid Refrigerator, Black",
                "category": "Appliances, Office Supplies, OFF-AP-4960",
                "quantity": 1,
                "rate": 437.72,
                "amount": 437.72,
            }
        ],
    }
    pdf_path = FIXTURES_DIR / "invoice_Trudy Brown_26186.pdf"
    bedrock = _bedrock_for_happy_path(discounted)
    engine = _build_engine(bedrock)
    definition = load_definition_from_file(WORKFLOW_PATH)
    payload = pdf_path.read_bytes()
    await engine.world.fs.write_bytes(str(pdf_path), payload)
    instance = await engine.run(definition, trigger_payload={"file_path": str(pdf_path)})

    steps = await engine.repositories.steps.list_by_instance(instance.id)
    record_step = next(s for s in steps if s.step_id == "record")
    assert record_step.output is not None
    assert record_step.output["discount"] == 74.41
    assert record_step.output["total_balanced"] is True
    assert abs(record_step.output["computed_total"] - 448.71) < 0.01


async def test_pipeline_routes_to_unknown_country_when_field_missing() -> None:
    """If the agent omits ship_to_country, the route step falls back."""
    missing_country = _liz_thompson_extracted()
    del missing_country["ship_to_country"]
    pdf_path = FIXTURES_DIR / "invoice_Liz Thompson_19562.pdf"
    bedrock = _bedrock_for_happy_path(missing_country)
    engine = _build_engine(bedrock)
    definition = load_definition_from_file(WORKFLOW_PATH)
    payload = pdf_path.read_bytes()
    await engine.world.fs.write_bytes(str(pdf_path), payload)
    instance = await engine.run(definition, trigger_payload={"file_path": str(pdf_path)})

    steps = await engine.repositories.steps.list_by_instance(instance.id)
    route_step = next(s for s in steps if s.step_id == "route")
    assert route_step.output is not None
    assert route_step.output["safe_value"] == "unknown_country"
    assert "/unknown_country/" in route_step.output["destination"]


# --- record_invoice_extraction unit tests ---


def test_extract_invoice_fields_full_round_trip() -> None:
    raw = json.dumps(_liz_thompson_extracted())
    out = _extract_invoice_fields(raw)
    assert out is not None
    assert out["invoice_number"] == "19562"
    assert out["customer_name"] == "Liz Thompson"
    assert out["invoice_date"] == "2012-12-05"
    assert out["ship_to_country"] == "Germany"
    assert out["total"] == 787.41
    assert len(out["line_items"]) == 1
    assert out["line_item_count"] == 1
    assert out["line_items"][0]["amount"] == 636.30


def test_extract_invoice_fields_drops_wrong_types() -> None:
    """Wrong-typed fields drop silently — same pattern as record_email_triage."""
    raw = json.dumps(
        {
            "invoice_number": 19562,  # wrong type (int, not str)
            "customer_name": "Liz",
            "subtotal": "636.30",  # wrong type (str)
            "total": 787.41,
            "line_items": [{"item": "x", "quantity": True, "rate": 1.0, "amount": 1.0}],
        }
    )
    out = _extract_invoice_fields(raw)
    assert out is not None
    assert "invoice_number" not in out  # dropped
    assert out["customer_name"] == "Liz"
    assert "subtotal" not in out  # dropped
    assert out["total"] == 787.41
    # quantity=True is a bool — dropped from line_item
    assert len(out["line_items"]) == 1
    assert "quantity" not in out["line_items"][0]


@pytest.mark.parametrize(
    "raw,expected_none_reason",
    [
        ("not json at all", "no JSON object found"),
        ('"a string, not a dict"', "JSON is not a dict"),
        ("{}", "empty dict produces empty out"),
    ],
)
def test_extract_invoice_fields_rejects_bad_input(raw: str, expected_none_reason: str) -> None:
    assert _extract_invoice_fields(raw) is None, expected_none_reason


def test_extract_invoice_fields_line_item_dropping_partial_entries() -> None:
    """An entry with no recognizable fields is dropped entirely."""
    raw = json.dumps(
        {
            "customer_name": "x",
            "line_items": [
                {"item": "good", "quantity": 1, "rate": 1.0, "amount": 1.0},
                {"unknown_field": "garbage"},  # dropped
            ],
        }
    )
    out = _extract_invoice_fields(raw)
    assert out is not None
    assert out["line_item_count"] == 1
    assert out["line_items"][0]["item"] == "good"
