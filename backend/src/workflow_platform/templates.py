"""Bundled example workflows offered as starting points in the GUI.

The templates gallery (canvas roadmap C5.2) seeds from the example workflow
YAMLs shipped under the definitions directory — the same files the trigger
orchestrator loads at startup. This module discovers and loads them, and
provides the id-slug helpers the create endpoint uses to mint a fresh,
non-colliding id when a template is cloned (C5.2) or a blank workflow is
created (C5.3).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from workflow_platform.workflow import WorkflowDefinition, load_definition_from_file
from workflow_platform.workflow.topology import WorkflowDefinitionError

logger = logging.getLogger(__name__)


def default_examples_dir() -> Path:
    """The bundled ``examples/`` dir, resolved relative to the repo root.

    The default must NOT be CWD-relative: uvicorn is commonly launched from
    ``backend/``, where a bare ``Path("examples")`` resolves to the
    non-existent ``backend/examples`` — so the templates gallery (and the
    trigger orchestrator, which shares this dir) silently find nothing.
    Resolving via this module's location finds the repo-root ``examples/``
    regardless of the process CWD.

    Falls back to a CWD-relative ``examples`` when the repo-root path isn't
    present (e.g. the Docker image copies only ``src/``, not ``examples/``),
    preserving prior behavior. ``WORKFLOW_DEFINITIONS_DIR`` overrides either way.
    """
    # templates.py lives at <repo>/backend/src/workflow_platform/templates.py,
    # so parents[3] is the repo root.
    repo_root_examples = Path(__file__).resolve().parents[3] / "examples"
    return repo_root_examples if repo_root_examples.is_dir() else Path("examples")


def load_templates(directory: Path) -> list[WorkflowDefinition]:
    """Load every parseable workflow definition under `directory`.

    Walks recursively for `workflow*.yaml` / `*.yml` files (the examples
    convention), skipping any that fail to parse so one malformed file can't
    break the whole gallery. Duplicate ids keep the first seen. Returns
    definitions sorted by name; a missing directory yields an empty list.
    """
    if not directory.is_dir():
        logger.debug("Templates directory %s does not exist.", directory)
        return []
    templates: list[WorkflowDefinition] = []
    seen_ids: set[str] = set()
    for path in sorted(directory.rglob("workflow*.y*ml")):
        try:
            definition = load_definition_from_file(path)
        except (WorkflowDefinitionError, OSError, ValueError) as exc:
            logger.debug("Skipping non-template %s: %s", path, exc)
            continue
        if definition.id in seen_ids:
            continue
        seen_ids.add(definition.id)
        templates.append(definition)
    templates.sort(key=lambda d: d.name.lower())
    return templates


def slugify(text: str) -> str:
    """Lowercase kebab-case slug usable as a workflow id. Empty → `workflow`."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "workflow"


def unique_id(base: str, existing: set[str]) -> str:
    """`base` if free, else `base-2`, `base-3`, … until one is unused."""
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"
