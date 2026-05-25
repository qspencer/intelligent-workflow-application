"""Replay-mode end-to-end tests for the research paper triage workflow.

Covers:
- `_extract_paper_triage` parser (field happy path, optional fields, bad JSON).
- The full triage workflow against each committed fixture, with `FakeBedrock`
  returning a representative JSON response per case.

Workflow definition, agent memory, and fixtures live at
`examples/research_paper_triage/`. Edits there are picked up on the next
test run.
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
from workflow_platform.engine.functions import _extract_paper_triage
from workflow_platform.persistence import (
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition_from_yaml
from workflow_platform.world import mock_world

EXAMPLE_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "research_paper_triage"
WORKFLOW_YAML = EXAMPLE_DIR / "workflow.yaml"
FIXTURES = EXAMPLE_DIR / "fixtures"


# --- _extract_paper_triage parser ---


def test_extract_paper_triage_full_object() -> None:
    raw = json.dumps(
        {
            "relevance_score": 5,
            "relevance_bucket": "directly_relevant",
            "summary": "Hierarchical memory tiers for LLM agents.",
            "key_concepts": ["MemGPT", "virtual memory", "long context"],
            "tags": ["empirical"],
        }
    )
    triage = _extract_paper_triage(raw)
    assert triage == {
        "relevance_score": 5.0,
        "relevance_bucket": "directly_relevant",
        "summary": "Hierarchical memory tiers for LLM agents.",
        "key_concepts": ["MemGPT", "virtual memory", "long context"],
        "concept_count": 3,
        "tags": ["empirical"],
        "tag_count": 1,
    }


def test_extract_paper_triage_partial_object() -> None:
    raw = '{"relevance_score": 2, "relevance_bucket": "methodology_only"}'
    triage = _extract_paper_triage(raw)
    assert triage == {
        "relevance_score": 2.0,
        "relevance_bucket": "methodology_only",
    }


def test_extract_paper_triage_drops_unknown_keys() -> None:
    raw = '{"relevance_score": 3, "vibes": "academic"}'
    triage = _extract_paper_triage(raw)
    assert triage is not None
    assert "vibes" not in triage


def test_extract_paper_triage_returns_none_on_invalid_json() -> None:
    assert _extract_paper_triage("not JSON") is None


def test_extract_paper_triage_handles_fences() -> None:
    raw = "Triage:\n```json\n" + json.dumps({"relevance_score": 4}) + "\n```"
    triage = _extract_paper_triage(raw)
    assert triage is not None
    assert triage["relevance_score"] == 4.0


# --- workflow end-to-end against each fixture ---


@pytest.mark.asyncio
async def test_memgpt_paper_triages_as_directly_relevant() -> None:
    await _run_fixture(
        "01_directly_relevant_memgpt.json",
        agent_response={
            "relevance_score": 5,
            "relevance_bucket": "directly_relevant",
            "summary": "Hierarchical memory tiers for LLM context management.",
            "key_concepts": ["MemGPT", "hierarchical memory", "virtual context"],
            "tags": ["empirical"],
        },
        expect={
            "relevance_bucket": "directly_relevant",
            "relevance_score": 5.0,
            "concept_count": 3,
            "tag_count": 1,
        },
    )


@pytest.mark.asyncio
async def test_agent_planning_paper_triages_as_tangentially_relevant() -> None:
    await _run_fixture(
        "02_tangentially_agent_planning.json",
        agent_response={
            "relevance_score": 3,
            "relevance_bucket": "tangentially_relevant",
            "summary": "Hierarchical planning for web-navigation agents; no memory contribution.",
            "key_concepts": ["hierarchical planning", "web agents"],
            "tags": ["empirical"],
        },
        expect={"relevance_bucket": "tangentially_relevant", "relevance_score": 3.0},
    )


@pytest.mark.asyncio
async def test_rag_eval_paper_triages_as_methodology_only() -> None:
    await _run_fixture(
        "03_methodology_rag_eval.json",
        agent_response={
            "relevance_score": 4,
            "relevance_bucket": "methodology_only",
            "summary": "Multi-dimensional RAG eval framework distinguishing recall from citation faithfulness.",
            "key_concepts": ["RAG evaluation", "citation faithfulness"],
            "tags": ["benchmark", "empirical"],
        },
        expect={"relevance_bucket": "methodology_only", "tag_count": 2},
    )


@pytest.mark.asyncio
async def test_vision_paper_triages_as_out_of_scope() -> None:
    await _run_fixture(
        "04_out_of_scope_vision.json",
        agent_response={
            "relevance_score": 0,
            "relevance_bucket": "out_of_scope",
            "summary": "Sub-pixel convolution optimization for real-time 4K video super-resolution.",
            "key_concepts": ["video super-resolution"],
            "tags": [],
        },
        expect={
            "relevance_bucket": "out_of_scope",
            "relevance_score": 0.0,
            "tag_count": 0,
        },
    )


@pytest.mark.asyncio
async def test_long_context_survey_triages_with_survey_tag() -> None:
    await _run_fixture(
        "05_survey_long_context.json",
        agent_response={
            "relevance_score": 3,
            "relevance_bucket": "tangentially_relevant",
            "summary": "Comprehensive survey of long-context LLM methods across training and inference.",
            "key_concepts": ["long context", "KV-cache compression", "positional encoding"],
            "tags": ["survey"],
        },
        expect={"relevance_bucket": "tangentially_relevant"},
    )


@pytest.mark.asyncio
async def test_workflow_handles_garbage_agent_output() -> None:
    """A misbehaving agent reply leaves parse_ok=False but doesn't fail the run."""
    definition = load_definition_from_yaml(WORKFLOW_YAML.read_text())
    repos = in_memory_repositories()
    bedrock = FakeBedrock([text_response("I'd rather discuss the meaning of life.")])
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )
    instance = await engine.run(
        definition,
        trigger_payload=json.loads((FIXTURES / "01_directly_relevant_memgpt.json").read_text()),
    )
    assert instance.state == WorkflowInstanceState.COMPLETED
    steps = await repos.steps.list_by_instance(instance.id)
    record = next(s for s in steps if s.step_id == "record_triage")
    assert record.output is not None
    assert record.output["parse_ok"] is False
    assert "raw" in record.output


def test_workflow_yaml_parses() -> None:
    definition = load_definition_from_yaml(WORKFLOW_YAML.read_text())
    assert definition.id == "research-paper-triage"
    assert definition.trigger.type == "webhook"
    assert [s.id for s in definition.steps] == ["triage", "record_triage"]


def test_all_fixtures_are_valid_json_with_expected_shape() -> None:
    for path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(path.read_text())
        for field in (
            "id",
            "title",
            "abstract",
            "authors",
            "primary_category",
            "categories",
            "published",
        ):
            assert field in data, f"{path.name} missing {field!r}"


# --- helpers ---


async def _run_fixture(
    fixture_name: str,
    *,
    agent_response: dict[str, Any],
    expect: dict[str, Any],
) -> None:
    definition = load_definition_from_yaml(WORKFLOW_YAML.read_text())
    payload = json.loads((FIXTURES / fixture_name).read_text())
    bedrock = FakeBedrock([text_response(json.dumps(agent_response))])
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )
    instance = await engine.run(definition, trigger_payload=payload)
    assert instance.state == WorkflowInstanceState.COMPLETED, instance.error

    steps = await repos.steps.list_by_instance(instance.id)
    record = next(s for s in steps if s.step_id == "record_triage")
    assert record.output is not None
    assert record.output["parse_ok"] is True
    for key, expected in expect.items():
        assert record.output[key] == expected, (
            f"{fixture_name}: expected {key}={expected!r}, got {record.output.get(key)!r}"
        )
