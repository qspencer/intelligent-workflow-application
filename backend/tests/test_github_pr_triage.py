"""Replay-mode end-to-end tests for the GitHub PR triage workflow.

Covers:
- `_extract_pr_triage` parser (field happy path, optional fields, bad JSON).
- The full triage workflow against each committed fixture, with `FakeBedrock`
  returning a representative JSON response per case.

The workflow definition, agent memory, and PR fixtures live alongside the
rest of the example at `examples/github_pr_triage/`. Edit them there; the
test reads them verbatim so any change is picked up on the next run.
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
from workflow_platform.engine.functions import _extract_pr_triage
from workflow_platform.persistence import (
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition_from_yaml
from workflow_platform.world import mock_world

EXAMPLE_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "github_pr_triage"
WORKFLOW_YAML = EXAMPLE_DIR / "workflow.yaml"
FIXTURES = EXAMPLE_DIR / "fixtures"


# --- _extract_pr_triage parser ---


def test_extract_pr_triage_full_object() -> None:
    raw = json.dumps(
        {
            "category": "bug_fix",
            "complexity": "small",
            "needs_tests": True,
            "summary": "Retry transient Stripe webhooks.",
            "concerns": [],
        }
    )
    triage = _extract_pr_triage(raw)
    assert triage == {
        "category": "bug_fix",
        "complexity": "small",
        "needs_tests": True,
        "summary": "Retry transient Stripe webhooks.",
        "concerns": [],
        "concern_count": 0,
    }


def test_extract_pr_triage_counts_concerns() -> None:
    raw = json.dumps(
        {
            "category": "other",
            "complexity": "large",
            "concerns": ["mixed concerns", "large diff", "no tests, code change"],
        }
    )
    triage = _extract_pr_triage(raw)
    assert triage is not None
    assert triage["concern_count"] == 3


def test_extract_pr_triage_drops_unknown_keys() -> None:
    raw = '{"category": "feature", "vibes": "good", "summary": "x"}'
    triage = _extract_pr_triage(raw)
    assert triage is not None
    assert "vibes" not in triage


def test_extract_pr_triage_returns_none_on_invalid_json() -> None:
    assert _extract_pr_triage("not even close to JSON") is None


def test_extract_pr_triage_handles_fences() -> None:
    raw = (
        "Sure, here's the triage:\n```json\n"
        + json.dumps({"category": "docs", "summary": "y"})
        + "\n```"
    )
    triage = _extract_pr_triage(raw)
    assert triage is not None
    assert triage["category"] == "docs"


# --- workflow end-to-end against each fixture ---


@pytest.mark.asyncio
async def test_perfect_pr_triages_clean() -> None:
    await _run_fixture(
        "01_perfect_pr.json",
        agent_response={
            "category": "bug_fix",
            "complexity": "small",
            "needs_tests": True,
            "summary": "Adds bounded retries and dead-lettering for transient Stripe webhook failures.",
            "concerns": [],
        },
        expect={
            "category": "bug_fix",
            "complexity": "small",
            "needs_tests": True,
            "concern_count": 0,
        },
    )


@pytest.mark.asyncio
async def test_missing_description_flagged() -> None:
    await _run_fixture(
        "02_missing_description.json",
        agent_response={
            "category": "bug_fix",
            "complexity": "small",
            "needs_tests": True,
            "summary": "Unclear bug fix touching four files.",
            "concerns": ["missing description", "no linked issue", "no tests, code change"],
        },
        expect={"concern_count": 3, "category": "bug_fix"},
    )


@pytest.mark.asyncio
async def test_huge_diff_flagged_large() -> None:
    await _run_fixture(
        "03_huge_diff.json",
        agent_response={
            "category": "refactor",
            "complexity": "gigantic",
            "needs_tests": False,
            "summary": "Extracts auth/billing/notifications into separate packages.",
            "concerns": ["large diff", "touches load-bearing files"],
        },
        expect={"complexity": "gigantic", "concern_count": 2},
    )


@pytest.mark.asyncio
async def test_dependency_bump_no_tests_no_concerns() -> None:
    await _run_fixture(
        "04_dependency_bump.json",
        agent_response={
            "category": "dependency",
            "complexity": "trivial",
            "needs_tests": False,
            "summary": "Bumps axios from 1.6.7 to 1.7.4 in the frontend.",
            "concerns": [],
        },
        expect={"category": "dependency", "needs_tests": False, "concern_count": 0},
    )


@pytest.mark.asyncio
async def test_doc_only_external_contributor() -> None:
    await _run_fixture(
        "05_doc_only.json",
        agent_response={
            "category": "docs",
            "complexity": "trivial",
            "needs_tests": False,
            "summary": "Clarifies pagination semantics in the /v1/orders endpoint.",
            "concerns": ["from external contributor"],
        },
        expect={"category": "docs", "concern_count": 1},
    )


@pytest.mark.asyncio
async def test_workflow_handles_garbage_agent_output(tmp_path: Path) -> None:
    """If the agent ignores the rubric and replies in prose, record_pr_triage
    captures parse_ok=False and the workflow completes — we don't want a
    misbehaving agent to fail the whole run."""
    definition = load_definition_from_yaml(WORKFLOW_YAML.read_text())
    repos = in_memory_repositories()
    bedrock = FakeBedrock([text_response("Hm, I'd rather not return JSON today.")])
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )
    instance = await engine.run(
        definition,
        trigger_payload=json.loads((FIXTURES / "01_perfect_pr.json").read_text()),
    )
    assert instance.state == WorkflowInstanceState.COMPLETED

    steps = await repos.steps.list_by_instance(instance.id)
    record = next(s for s in steps if s.step_id == "record_triage")
    assert record.output is not None
    assert record.output["parse_ok"] is False
    assert "raw" in record.output


def test_workflow_yaml_parses() -> None:
    definition = load_definition_from_yaml(WORKFLOW_YAML.read_text())
    assert definition.id == "github-pr-triage"
    assert definition.trigger.type == "webhook"
    assert [s.id for s in definition.steps] == ["triage", "record_triage"]


def test_all_fixtures_are_valid_json() -> None:
    for path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(path.read_text())
        # Every fixture should have at minimum the fields the agent needs.
        for field in ("number", "title", "body", "user", "additions", "deletions", "changed_files"):
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
