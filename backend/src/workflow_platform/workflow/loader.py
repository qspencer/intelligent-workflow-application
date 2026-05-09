"""Load workflow definitions from JSON / dicts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from workflow_platform.workflow.definition import WorkflowDefinition
from workflow_platform.workflow.topology import WorkflowDefinitionError, validate_and_order


def load_definition(data: dict[str, Any]) -> WorkflowDefinition:
    """Parse a workflow definition dict and verify its structure."""
    try:
        definition = WorkflowDefinition.model_validate(data)
    except ValidationError as exc:
        raise WorkflowDefinitionError(f"Invalid workflow definition:\n{exc}") from exc
    validate_and_order(definition)
    return definition


def load_definition_from_file(path: str | Path) -> WorkflowDefinition:
    return load_definition(json.loads(Path(path).read_text()))
