"""L1/L2 scaffold eval runner (workflow_platform.evals.scaffold_eval).

Pins the framework amendments:
- L2 is constraint satisfaction (tolerant), not exact structural matching.
- Criteria naming capabilities absent from the catalog are `unsatisfiable`,
  excluded from scoring denominators.
- Free-text criteria defer to the L3/L4 judge (`judge` status).
- Reports carry a catalog hash so scores are compared like-for-like.
- The 50-case suite in docs/product/ parses as the single source of truth.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.catalog import build_catalog
from workflow_platform.engine import ToolCatalog, default_function_registry
from workflow_platform.evals import (
    catalog_hash,
    check_case,
    evaluate_model,
    load_cases,
)
from workflow_platform.tools import FileReadTool, FileWriteTool, PdfExtractTool

SUITE = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "product" / "LLM_EVAL_TEST_SUITE.md"
)

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _catalog() -> Any:
    return build_catalog(
        default_function_registry(),
        ToolCatalog([PdfExtractTool(), FileReadTool(), FileWriteTool()]),
    )


def _raw_definition(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "eval-wf",
        "name": "eval-wf",
        "trigger": {"type": "filesystem", "config": {"path": "/incoming"}},
        "steps": [{"id": "move", "type": "deterministic", "function": "copy_files", "config": {}}],
        "edges": [],
    }
    base.update(overrides)
    return base


# --- suite parsing ---


def test_suite_parses_all_50_cases() -> None:
    cases = load_cases(SUITE)
    assert len(cases) == 50
    assert len({c.id for c in cases}) == 50  # unique ids
    categories = {c.category for c in cases}
    assert categories == {"simple", "medium", "complex", "edge_case"}
    assert all(c.input and c.expected for c in cases)


# --- L1 ---


def test_l1_fails_on_unparseable_and_invalid() -> None:
    catalog = _catalog()
    ok, err, _ = check_case({"not": "a workflow"}, {}, catalog)
    assert not ok and err is not None and "parse" in err

    # Parses but structurally invalid: edge to a missing step.
    bad = _raw_definition(edges=[{"from": "move", "to": "ghost"}])
    ok, err, _ = check_case(bad, {}, catalog)
    assert not ok and err is not None


# --- L2 constraint semantics ---


def test_l2_constraints_pass_and_fail() -> None:
    catalog = _catalog()
    raw = _raw_definition(
        steps=[
            {"id": "extract", "type": "deterministic", "function": "pdf_extract", "config": {}},
            {
                "id": "classify",
                "type": "agentic",
                "goal": "classify",
                "model": MODEL,
                "tools": [],
            },
            {"id": "route-a", "type": "deterministic", "function": "copy_files", "config": {}},
            {"id": "route-b", "type": "deterministic", "function": "noop", "config": {}},
        ],
        edges=[
            {"from": "extract", "to": "classify"},
            {"from": "classify", "to": "route-a", "condition": "steps['classify']"},
            {"from": "classify", "to": "route-b", "condition": "not steps['classify']"},
        ],
    )
    expected = {
        "trigger_type": "filesystem",
        "trigger_config_contains": ["/incoming"],
        "min_steps": 2,
        "max_steps": 5,
        "must_have_agentic": True,
        "has_conditional": True,
        "min_branches": 2,
        "functions_used": ["pdf_extract"],
        "step_sequence_contains": ["deterministic", "agentic", "deterministic"],
    }
    ok, _, criteria = check_case(raw, expected, catalog)
    assert ok
    assert all(c.status == "pass" for c in criteria), [
        (c.name, c.status, c.detail) for c in criteria
    ]

    # Same definition against contradicting expectations → failures with detail.
    ok, _, criteria = check_case(
        raw, {"no_agentic_steps": True, "max_steps": 2, "trigger_type": "webhook"}, catalog
    )
    assert ok  # L1 still passes
    assert all(c.status == "fail" for c in criteria)


def test_trigger_alias_email_gmail_poll() -> None:
    catalog = _catalog()
    raw = _raw_definition(trigger={"type": "email", "config": {"provider": "gmail"}})
    ok, _, criteria = check_case(raw, {"trigger_type": "gmail_poll"}, catalog)
    assert ok and criteria[0].status == "pass"


def test_unsatisfiable_criterion_excluded_not_failed() -> None:
    catalog = _catalog()  # no email tools wired
    raw = _raw_definition()
    ok, _, criteria = check_case(
        raw, {"tools_used": ["email_send"], "functions_used": ["copy_files"]}, catalog
    )
    assert ok
    by_name = {c.name: c for c in criteria}
    assert by_name["tools_used:email_send"].status == "unsatisfiable"
    assert by_name["functions_used:copy_files"].status == "pass"


def test_judge_criteria_deferred() -> None:
    ok, _, criteria = check_case(
        _raw_definition(), {"must_not": ["auto-delete anything"]}, _catalog()
    )
    assert ok
    assert criteria[0].status == "judge"


def test_connectors_used_is_tolerant_containment() -> None:
    # "slack — or generic HTTP to Slack webhook": a step label naming Slack passes.
    raw = _raw_definition(
        steps=[
            {
                "id": "notify",
                "type": "deterministic",
                "function": "noop",
                "config": {"url": "https://hooks.slack.com/T00/B00"},
            }
        ]
    )
    ok, _, criteria = check_case(raw, {"connectors_used": ["slack"]}, _catalog())
    assert ok and criteria[0].status == "pass"


# --- end-to-end with a fake model ---


def test_evaluate_model_aggregates_and_hashes() -> None:
    catalog = _catalog()
    cases = [c for c in load_cases(SUITE) if c.id == "simple_file_move"]
    assert len(cases) == 1
    good = _raw_definition(
        steps=[
            {"id": "move", "type": "deterministic", "function": "copy_files", "config": {}},
        ]
    )
    bedrock = FakeBedrock([text_response(json.dumps(good))])
    report = asyncio.run(
        evaluate_model(bedrock, model=MODEL, cases=cases, catalog=catalog, concurrency=1)
    )
    assert report["cases"] == 1
    assert report["l1_pass"] == 1
    assert report["catalog_hash"] == catalog_hash(catalog)
    assert report["l2_rate"] is not None
    # Same catalog → same hash; a different catalog → different hash.
    smaller = build_catalog(default_function_registry(), ToolCatalog([FileWriteTool()]))
    assert catalog_hash(smaller) != catalog_hash(catalog)


def test_scaffold_call_failure_recorded_as_l1(monkeypatch: Any) -> None:
    catalog = _catalog()
    cases = [c for c in load_cases(SUITE) if c.id == "simple_file_move"]
    bedrock = FakeBedrock([text_response("no json here at all")])
    report = asyncio.run(
        evaluate_model(bedrock, model=MODEL, cases=cases, catalog=catalog, concurrency=1)
    )
    assert report["l1_pass"] == 0
    assert "failed" in report["results"][0]["l1_error"]


def test_l1_applies_production_normalization() -> None:
    """The model is never asked to invent an id; name/trigger/description get
    the same defaults the scaffold endpoint applies. A raw output missing all
    of them still parses (matching what a user actually receives)."""
    raw = {
        "name": "Move Files",
        "steps": [{"id": "s1", "type": "deterministic", "function": "noop", "config": {}}],
        "edges": [],
    }
    ok, err, _ = check_case(raw, {}, _catalog())
    assert ok, err
