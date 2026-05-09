"""DAG validation + topological ordering for workflow definitions."""

from __future__ import annotations

from collections import defaultdict, deque

from workflow_platform.workflow.definition import WorkflowDefinition


class WorkflowDefinitionError(ValueError):
    """Raised when a workflow definition is structurally invalid."""


def validate_and_order(definition: WorkflowDefinition) -> list[str]:
    """Validate a definition's structure and return step ids in topological order.

    Checks: unique step ids, edges reference existing steps, the graph is a DAG.
    Tool / function name resolution against runtime registries happens at the
    engine layer when execution starts.
    """
    step_ids = [s.id for s in definition.steps]
    if len(step_ids) != len(set(step_ids)):
        seen: set[str] = set()
        dupes = sorted({sid for sid in step_ids if sid in seen or seen.add(sid)})  # type: ignore[func-returns-value]
        raise WorkflowDefinitionError(f"Duplicate step ids: {dupes}")

    valid = set(step_ids)
    for edge in definition.edges:
        if edge.source not in valid:
            raise WorkflowDefinitionError(
                f"Edge references unknown step {edge.source!r} (in `from`)"
            )
        if edge.target not in valid:
            raise WorkflowDefinitionError(f"Edge references unknown step {edge.target!r} (in `to`)")

    return _topological_sort(step_ids, definition.edges)


def _topological_sort(step_ids: list[str], edges: list) -> list[str]:  # type: ignore[type-arg]
    in_degree: dict[str, int] = dict.fromkeys(step_ids, 0)
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adjacency[edge.source].append(edge.target)
        in_degree[edge.target] += 1

    # Kahn's algorithm. Use definition order (not arbitrary) as tie-breaker so
    # workflows with no edges run in declared order.
    queue: deque[str] = deque(sid for sid in step_ids if in_degree[sid] == 0)
    ordered: list[str] = []
    while queue:
        node = queue.popleft()
        ordered.append(node)
        for neighbor in adjacency[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(ordered) != len(step_ids):
        cycle_nodes = sorted(sid for sid, deg in in_degree.items() if deg > 0)
        raise WorkflowDefinitionError(f"Workflow contains a cycle involving steps: {cycle_nodes}")
    return ordered
