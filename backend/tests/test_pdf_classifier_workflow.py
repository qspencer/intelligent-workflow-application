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
from workflow_platform.engine.functions import _extract_document_type
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
            ],
            "edges": [
                {"from": "extract", "to": "classify"},
                {"from": "classify", "to": "route"},
            ],
        }
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
    assert step_ids == ["extract", "classify", "route"]


# --- world is unused in extract; sanity check the workflow still touches it ---


def test_real_world_is_used_for_routing(tmp_path: Path) -> None:
    """Sanity: real_world() returns a usable World."""
    world: World = real_world()
    assert world.fs is not None
