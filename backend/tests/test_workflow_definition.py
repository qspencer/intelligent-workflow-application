"""Tests for workflow definition loading + DAG validation."""

from __future__ import annotations

from typing import Any

import pytest

from workflow_platform.workflow import (
    WorkflowDefinitionError,
    load_definition,
    validate_and_order,
)


def _basic_definition(steps: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "wf",
        "name": "wf",
        "trigger": {"type": "manual", "config": {}},
        "steps": steps,
        "edges": edges,
    }


def test_load_simple_linear_workflow() -> None:
    definition = load_definition(
        _basic_definition(
            steps=[
                {"id": "a", "type": "deterministic", "function": "noop"},
                {"id": "b", "type": "deterministic", "function": "noop"},
            ],
            edges=[{"from": "a", "to": "b"}],
        )
    )
    order = validate_and_order(definition)
    assert order == ["a", "b"]


def test_diamond_topology_orders_correctly() -> None:
    definition = load_definition(
        _basic_definition(
            steps=[
                {"id": "a", "type": "deterministic", "function": "noop"},
                {"id": "b", "type": "deterministic", "function": "noop"},
                {"id": "c", "type": "deterministic", "function": "noop"},
                {"id": "d", "type": "deterministic", "function": "noop"},
            ],
            edges=[
                {"from": "a", "to": "b"},
                {"from": "a", "to": "c"},
                {"from": "b", "to": "d"},
                {"from": "c", "to": "d"},
            ],
        )
    )
    order = validate_and_order(definition)
    assert order[0] == "a"
    assert order[-1] == "d"
    assert set(order[1:3]) == {"b", "c"}


def test_cycle_is_rejected() -> None:
    with pytest.raises(WorkflowDefinitionError, match="cycle"):
        load_definition(
            _basic_definition(
                steps=[
                    {"id": "a", "type": "deterministic", "function": "noop"},
                    {"id": "b", "type": "deterministic", "function": "noop"},
                ],
                edges=[{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
            )
        )


def test_duplicate_step_ids_rejected() -> None:
    with pytest.raises(WorkflowDefinitionError, match="Duplicate step ids"):
        load_definition(
            _basic_definition(
                steps=[
                    {"id": "a", "type": "deterministic", "function": "noop"},
                    {"id": "a", "type": "deterministic", "function": "noop"},
                ],
                edges=[],
            )
        )


def test_edge_references_unknown_step() -> None:
    with pytest.raises(WorkflowDefinitionError, match="unknown step"):
        load_definition(
            _basic_definition(
                steps=[{"id": "a", "type": "deterministic", "function": "noop"}],
                edges=[{"from": "a", "to": "ghost"}],
            )
        )


def test_agentic_step_parses_with_defaults() -> None:
    definition = load_definition(
        _basic_definition(
            steps=[
                {
                    "id": "classify",
                    "type": "agentic",
                    "goal": "Classify the document",
                    "model": "anthropic.claude-3-haiku",
                    "tools": ["pdf_extract"],
                }
            ],
            edges=[],
        )
    )
    step = definition.steps[0]
    assert step.type == "agentic"
    assert step.policy.max_iterations == 10
    assert step.policy.max_total_tokens == 200_000
