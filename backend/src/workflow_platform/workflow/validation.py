"""Structured, collect-all validation for the canvas (C7.3).

`validate_and_order` (topology.py) raises on the first structural problem — right
for the engine, wrong for an authoring UI that wants every problem at once, keyed
to the node/edge it concerns. This module runs the same checks non-fatally and
returns a list of findings the canvas renders as red node borders + messages.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from workflow_platform.workflow.definition import WorkflowDefinition
from workflow_platform.workflow.topology import WorkflowDefinitionError, validate_and_order


class ValidationFinding(BaseModel):
    level: Literal["error", "warning"]
    code: str
    message: str
    node_id: str | None = None
    edge: dict[str, str] | None = None


def validate_definition(definition: WorkflowDefinition) -> list[ValidationFinding]:
    """Every structural problem in `definition`, keyed to node/edge where possible.

    Errors block a save-able-but-runnable graph; warnings are advisory.
    """
    findings: list[ValidationFinding] = []
    step_ids = [s.id for s in definition.steps]
    valid_ids = set(step_ids)

    # Duplicate step ids.
    seen: set[str] = set()
    dupes: set[str] = set()
    for sid in step_ids:
        if sid in seen:
            dupes.add(sid)
        seen.add(sid)
    for sid in sorted(dupes):
        findings.append(
            ValidationFinding(
                level="error",
                code="duplicate_step_id",
                message=f"Duplicate step id {sid!r}",
                node_id=sid,
            )
        )

    # Edges referencing unknown steps.
    edges_ok = True
    for edge in definition.edges:
        e = {"from": edge.source, "to": edge.target}
        if edge.source not in valid_ids:
            edges_ok = False
            findings.append(
                ValidationFinding(
                    level="error",
                    code="edge_unknown_source",
                    message=f"Edge starts at unknown step {edge.source!r}",
                    edge=e,
                )
            )
        if edge.target not in valid_ids:
            edges_ok = False
            findings.append(
                ValidationFinding(
                    level="error",
                    code="edge_unknown_target",
                    message=f"Edge points to unknown step {edge.target!r}",
                    edge=e,
                )
            )

    # Per-step shape.
    for step in definition.steps:
        if step.type == "agentic" and not step.goal.strip():
            findings.append(
                ValidationFinding(
                    level="error",
                    code="empty_goal",
                    message=f"AI step {step.id!r} has no instructions (goal)",
                    node_id=step.id,
                )
            )
        if step.type == "deterministic" and not step.function.strip():
            findings.append(
                ValidationFinding(
                    level="error",
                    code="empty_function",
                    message=f"Step {step.id!r} has no function set",
                    node_id=step.id,
                )
            )

    # Cycle — only meaningful once ids are unique and edges resolve, since
    # validate_and_order raises on those first. Avoids a misleading message.
    if not dupes and edges_ok:
        try:
            validate_and_order(definition)
        except WorkflowDefinitionError as exc:
            if "cycle" in str(exc).lower():
                findings.append(ValidationFinding(level="error", code="cycle", message=str(exc)))

    # Disconnected steps (advisory): in a multi-step graph, a step touching no
    # edge is probably a mistake. Single-step / edgeless graphs are fine.
    if len(definition.steps) > 1 and definition.edges:
        connected: set[str] = set()
        for edge in definition.edges:
            connected.add(edge.source)
            connected.add(edge.target)
        for step in definition.steps:
            if step.id not in connected:
                findings.append(
                    ValidationFinding(
                        level="warning",
                        code="disconnected_step",
                        message=f"Step {step.id!r} isn't connected to anything",
                        node_id=step.id,
                    )
                )

    return findings
