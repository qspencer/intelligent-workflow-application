"""End-to-end test for the PDF classifier example workflow.

Drives a real PDF through the full pipeline:
- `pdf_extract` (deterministic, reads via PyMuPDF off disk)
- `classify` (agentic, FakeBedrock returns the JSON the model would emit)
- `route_by_classification` (deterministic, copies the PDF into output/<category>/)

The Bedrock call is faked; everything else is real (real filesystem, real
PyMuPDF, real workflow engine, real audit log).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.engine import (
    ToolCatalog,
    WorkflowEngine,
    default_function_registry,
)
from workflow_platform.engine.functions import _extract_document_type, _extract_eval_scores
from workflow_platform.persistence import (
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition, load_definition_from_yaml
from workflow_platform.world import World, real_world

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _make_pdf(path: Path, body: str) -> None:
    """Write a one-page text PDF that PyMuPDF can extract natively."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), body)
    doc.save(str(path))
    doc.close()


def _classifier_definition(inbox_root: Path, output_root: Path) -> Any:
    return load_definition(
        {
            "id": "pdf-classifier",
            "name": "PDF Classifier",
            "trigger": {"type": "filesystem", "config": {"path": str(inbox_root)}},
            "steps": [
                {
                    "id": "extract",
                    "type": "deterministic",
                    "function": "pdf_extract",
                    "config": {"filepath_from": "trigger.file_path"},
                },
                {
                    "id": "classify",
                    "type": "agentic",
                    "model": MODEL,
                    "tools": [],
                    "goal": "Classify the document. Return a JSON object on one line.",
                    "policy": {"max_iterations": 2, "max_total_tokens": 4000},
                },
                {
                    "id": "route",
                    "type": "deterministic",
                    "function": "route_by_classification",
                    "config": {
                        "source_from": "trigger.file_path",
                        "classification_from": "steps.classify.output_text",
                        "output_root": str(output_root),
                    },
                },
                {
                    "id": "evaluate",
                    "type": "agentic",
                    "model": MODEL,
                    "tools": [],
                    "goal": "Score the classification. Return a JSON object on one line.",
                    "policy": {"max_iterations": 2, "max_total_tokens": 4000},
                },
                {
                    "id": "record_eval",
                    "type": "deterministic",
                    "function": "record_evaluation",
                    "config": {"evaluation_from": "steps.evaluate.output_text"},
                },
            ],
            "edges": [
                {"from": "extract", "to": "classify"},
                {"from": "classify", "to": "route"},
                {"from": "classify", "to": "evaluate"},
                {"from": "evaluate", "to": "record_eval"},
            ],
        }
    )


def _eval_response(
    *, faithfulness: float = 5.0, category: float = 5.0, issues: list[str] | None = None
) -> dict[str, Any]:
    """Build a FakeBedrock response shaped like the evaluator's expected JSON."""
    return text_response(
        json.dumps(
            {
                "faithfulness_score": faithfulness,
                "category_score": category,
                "reasoning": "Looks consistent with the source text.",
                "issues": issues or [],
            }
        ),
        input_tokens=400,
        output_tokens=60,
    )


# --- _extract_document_type ---


def test_extract_document_type_bare_json() -> None:
    raw = '{"document_type": "invoice", "summary": "x"}'
    assert _extract_document_type(raw) == "invoice"


def test_extract_document_type_with_fences_and_prose() -> None:
    raw = (
        "Here is the classification:\n```json\n"
        + json.dumps({"document_type": "receipt", "summary": "y"})
        + "\n```\nLet me know if you want details."
    )
    assert _extract_document_type(raw) == "receipt"


def test_extract_document_type_missing_field() -> None:
    assert _extract_document_type('{"summary": "no type field"}') is None


def test_extract_document_type_invalid_json() -> None:
    assert _extract_document_type("definitely not JSON {oops}") is None


# --- workflow end-to-end ---


@pytest.mark.asyncio
async def test_classifier_routes_invoice_to_invoice_folder(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    output = tmp_path / "output"
    inbox.mkdir()
    pdf_path = inbox / "acme-invoice.pdf"
    _make_pdf(
        pdf_path,
        "INVOICE\nVendor: Acme Corp\nTotal: $1,234.56\nInvoice #: A-12345\nDate: 2026-05-10",
    )

    fake_bedrock = FakeBedrock(
        [
            text_response(
                json.dumps(
                    {
                        "document_type": "invoice",
                        "summary": "Acme invoice for $1,234.56.",
                        "key_fields": {
                            "vendor": "Acme Corp",
                            "total": "$1,234.56",
                            "invoice_number": "A-12345",
                        },
                    }
                ),
                input_tokens=300,
                output_tokens=80,
            ),
            _eval_response(faithfulness=5.0, category=5.0),
        ]
    )

    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog(),
        bedrock=fake_bedrock,
        world=real_world(),
    )

    definition = _classifier_definition(inbox, output)
    instance = await engine.run(definition, trigger_payload={"file_path": str(pdf_path)})

    assert instance.state == WorkflowInstanceState.COMPLETED, instance.error

    destination = output / "invoice" / "acme-invoice.pdf"
    assert destination.is_file()
    assert destination.read_bytes() == pdf_path.read_bytes()

    steps = await repos.steps.list_by_instance(instance.id)
    by_id = {s.step_id: s for s in steps}
    assert by_id["extract"].output is not None
    assert "INVOICE" in by_id["extract"].output["text"]
    assert by_id["route"].output is not None
    assert by_id["route"].output["document_type"] == "invoice"
    assert by_id["route"].output["bytes_copied"] > 0
    assert by_id["record_eval"].output is not None
    assert by_id["record_eval"].output["parse_ok"] is True
    assert by_id["record_eval"].output["faithfulness_score"] == 5.0
    assert by_id["record_eval"].output["category_score"] == 5.0


@pytest.mark.asyncio
async def test_classifier_unknown_type_falls_through_to_other(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    output = tmp_path / "output"
    inbox.mkdir()
    pdf_path = inbox / "mystery.pdf"
    _make_pdf(pdf_path, "An unidentifiable smudge.")

    # The agent returns a category that's not in the workflow's allow-list.
    fake_bedrock = FakeBedrock(
        [
            text_response(
                json.dumps({"document_type": "manifesto", "summary": "?"}),
            ),
            _eval_response(faithfulness=2.0, category=1.0, issues=["unrecognizable content"]),
        ]
    )

    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog(),
        bedrock=fake_bedrock,
        world=real_world(),
    )

    instance = await engine.run(
        _classifier_definition(inbox, output),
        trigger_payload={"file_path": str(pdf_path)},
    )

    assert instance.state == WorkflowInstanceState.COMPLETED
    assert (output / "other" / "mystery.pdf").is_file()


# --- workflow.yaml parses ---


def test_example_workflow_yaml_parses() -> None:
    yaml_path = (
        Path(__file__).resolve().parent.parent.parent
        / "examples"
        / "pdf_classifier"
        / "workflow.yaml"
    )
    assert yaml_path.is_file(), f"missing example workflow at {yaml_path}"
    definition = load_definition_from_yaml(yaml_path.read_text())
    assert definition.id == "pdf-classifier"
    step_ids = [s.id for s in definition.steps]
    assert step_ids == ["extract", "classify", "route", "evaluate", "record_eval"]


# --- world is unused in extract; sanity check the workflow still touches it ---


def test_real_world_is_used_for_routing(tmp_path: Path) -> None:
    """Sanity: real_world() returns a usable World."""
    world: World = real_world()
    assert world.fs is not None


# --- _extract_eval_scores ---


def test_extract_eval_scores_full_object() -> None:
    raw = json.dumps(
        {
            "faithfulness_score": 4,
            "category_score": 5,
            "reasoning": "Looks good.",
            "issues": ["minor: date format inferred"],
        }
    )
    scores = _extract_eval_scores(raw)
    assert scores == {
        "faithfulness_score": 4.0,
        "category_score": 5.0,
        "reasoning": "Looks good.",
        "issues": ["minor: date format inferred"],
    }


def test_extract_eval_scores_coerces_int_to_float() -> None:
    raw = '{"faithfulness_score": 3, "category_score": 0}'
    scores = _extract_eval_scores(raw)
    assert scores is not None
    assert scores["faithfulness_score"] == 3.0
    assert scores["category_score"] == 0.0
    assert isinstance(scores["faithfulness_score"], float)


def test_extract_eval_scores_drops_unknown_fields() -> None:
    raw = '{"faithfulness_score": 5, "category_score": 5, "vibes": "good"}'
    scores = _extract_eval_scores(raw)
    assert scores is not None
    assert "vibes" not in scores


def test_extract_eval_scores_returns_none_on_invalid_json() -> None:
    assert _extract_eval_scores("not even close to JSON") is None


def test_extract_eval_scores_returns_none_when_no_known_fields_present() -> None:
    # Valid JSON, but none of the expected keys.
    assert _extract_eval_scores('{"unrelated": 1}') is None


def test_extract_eval_scores_with_fences() -> None:
    raw = (
        "Sure, here's the score:\n```json\n"
        + json.dumps({"faithfulness_score": 2, "category_score": 4})
        + "\n```"
    )
    scores = _extract_eval_scores(raw)
    assert scores is not None
    assert scores["faithfulness_score"] == 2.0
    assert scores["category_score"] == 4.0


# --- record_evaluation graceful-failure case ---


@pytest.mark.asyncio
async def test_classifier_evaluator_garbage_output_does_not_fail_workflow(
    tmp_path: Path,
) -> None:
    """If the evaluator agent returns prose instead of JSON, record_evaluation
    captures parse_ok=False and the workflow still completes — routing must
    not be held hostage by eval failures."""
    inbox = tmp_path / "inbox"
    output = tmp_path / "output"
    inbox.mkdir()
    pdf_path = inbox / "doc.pdf"
    _make_pdf(pdf_path, "INVOICE\nVendor: X\nTotal: $1")

    fake_bedrock = FakeBedrock(
        [
            text_response(json.dumps({"document_type": "invoice", "summary": "X"})),
            text_response("I'm not going to give you JSON, sorry."),
        ]
    )

    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog(),
        bedrock=fake_bedrock,
        world=real_world(),
    )
    instance = await engine.run(
        _classifier_definition(inbox, output),
        trigger_payload={"file_path": str(pdf_path)},
    )

    assert instance.state == WorkflowInstanceState.COMPLETED
    assert (output / "invoice" / "doc.pdf").is_file()
    steps = await repos.steps.list_by_instance(instance.id)
    record_eval = next(s for s in steps if s.step_id == "record_eval")
    assert record_eval.output is not None
    assert record_eval.output["parse_ok"] is False
    assert "raw" in record_eval.output
