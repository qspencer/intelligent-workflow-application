"""Load + dump workflow definitions to JSON or YAML.

JSON is canonical (the schema is JSON-shaped); YAML is offered for human
authoring. Round-tripping a definition through YAML must be lossless.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
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
    p = Path(path)
    text = p.read_text()
    if p.suffix.lower() in {".yaml", ".yml"}:
        return load_definition_from_yaml(text)
    return load_definition(json.loads(text))


def load_definition_from_yaml(text: str) -> WorkflowDefinition:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WorkflowDefinitionError(f"Invalid YAML:\n{exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowDefinitionError("Workflow YAML must parse to a mapping at the top level")
    return load_definition(data)


def dump_definition_to_json(definition: WorkflowDefinition, *, indent: int = 2) -> str:
    return json.dumps(
        definition.model_dump(by_alias=True, exclude_none=False),
        indent=indent,
        default=str,
    )


def dump_definition_to_yaml(definition: WorkflowDefinition) -> str:
    return yaml.safe_dump(
        definition.model_dump(by_alias=True, exclude_none=False),
        sort_keys=False,
    )
