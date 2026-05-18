"""Stock deterministic step functions.

Workflows reference these by name in `step.function`. New functions register
through `FunctionRegistry`.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

from workflow_platform.engine.context import WorkflowContext
from workflow_platform.engine.registry import FunctionRegistry, StepFailure
from workflow_platform.tools import PdfExtractTool, ToolContext
from workflow_platform.world import World


async def noop(config: dict[str, Any], context: WorkflowContext, world: World) -> dict[str, Any]:
    """Pass `config` through unchanged. Useful for tests and placeholder steps."""
    return dict(config)


async def pdf_extract(
    config: dict[str, Any], context: WorkflowContext, world: World
) -> dict[str, Any]:
    """Extract text from a PDF via PdfExtractTool.

    Reads the file path from `config["filepath"]` if present, else from the
    dotted context path in `config["filepath_from"]` (e.g. "trigger.file_path").
    """
    filepath = config.get("filepath") or _resolve_path(context, config.get("filepath_from"))
    if not filepath:
        raise StepFailure("pdf_extract requires `filepath` or `filepath_from` in config")

    tool_ctx = ToolContext(
        world=world,
        workflow_instance_id=context.instance_id,
        capabilities=context.capabilities,
    )
    result = await PdfExtractTool().execute({"filepath": filepath}, context=tool_ctx)
    if not result.ok:
        raise StepFailure(result.error or "pdf_extract failed")
    return dict(result.content) if isinstance(result.content, dict) else {"value": result.content}


async def route_by_classification(
    config: dict[str, Any], context: WorkflowContext, world: World
) -> dict[str, Any]:
    """Copy a file into a per-category subfolder based on a prior agent's classification.

    Config:
      source_from        — dotted context path to the source file (default `"trigger.file_path"`).
      classification_from — dotted context path to a string containing JSON with a
                            `document_type` field (default `"steps.classify.output_text"`).
      output_root        — destination root directory.
      categories         — allowed document_type values; anything outside is mapped to
                            `fallback_category` (default category list mirrors the prototype's).
      fallback_category  — used when document_type is missing or unrecognized (default `"other"`).
    """
    source = _resolve_path(context, config.get("source_from", "trigger.file_path"))
    if not source:
        raise StepFailure("route_by_classification could not resolve source file path")

    raw = _resolve_path(context, config.get("classification_from", "steps.classify.output_text"))
    if not raw:
        raise StepFailure("route_by_classification could not resolve classification output")

    extracted = _extract_document_type(raw)
    categories = config.get(
        "categories",
        ["invoice", "receipt", "contract", "report", "letter", "form", "other"],
    )
    fallback = str(config.get("fallback_category", "other"))
    document_type: str = (
        extracted if extracted is not None and extracted in categories else fallback
    )

    output_root = config.get("output_root")
    if not output_root:
        raise StepFailure("route_by_classification requires `output_root` in config")

    filename = PurePosixPath(source).name
    destination = str(PurePosixPath(output_root) / document_type / filename)

    payload = await world.fs.read_bytes(source)
    await world.fs.write_bytes(destination, payload)

    return {
        "source": source,
        "destination": destination,
        "document_type": document_type,
        "bytes_copied": len(payload),
    }


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_document_type(raw: str) -> str | None:
    """Pull `document_type` out of an agent's text response.

    Tolerant of: bare JSON, JSON wrapped in ``` fences, surrounding prose. Returns None
    if no JSON object with a string `document_type` field is parseable."""
    match = _JSON_OBJECT_RE.search(raw)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    value = parsed.get("document_type") if isinstance(parsed, dict) else None
    return value if isinstance(value, str) else None


def _extract_eval_scores(raw: str) -> dict[str, Any] | None:
    """Pull evaluator scores out of a judge agent's text response.

    Expected JSON shape:
        {"faithfulness_score": 0..5, "category_score": 0..5,
         "reasoning": "...", "issues": ["..."]}

    Numeric fields are coerced to float; strings/lists are passed through with
    light validation. Returns None if no parseable JSON object is found.
    Unknown keys are dropped — the schema is what `record_evaluation` queries
    against."""
    match = _JSON_OBJECT_RE.search(raw)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("faithfulness_score", "category_score"):
        val = parsed.get(key)
        if isinstance(val, int | float) and not isinstance(val, bool):
            out[key] = float(val)
    reasoning = parsed.get("reasoning")
    if isinstance(reasoning, str):
        out["reasoning"] = reasoning
    issues = parsed.get("issues")
    if isinstance(issues, list):
        out["issues"] = [str(x) for x in issues]
    return out or None


async def record_evaluation(
    config: dict[str, Any], context: WorkflowContext, world: World
) -> dict[str, Any]:
    """Parse a judge agent's JSON output into structured score fields.

    Reads the evaluator's `output_text` (default
    `"steps.evaluate.output_text"`) and lifts `faithfulness_score`,
    `category_score`, `reasoning`, `issues` into the step's own output dict.
    Downstream queries hit those fields directly instead of re-parsing the
    JSON string.

    On unparseable input: returns a record with `parse_ok=False` and the raw
    text under `raw`. The step does not fail — the workflow continues.
    """
    source = config.get("evaluation_from", "steps.evaluate.output_text")
    raw = _resolve_path(context, source)
    if not raw:
        raise StepFailure(f"record_evaluation could not resolve {source!r}")

    scores = _extract_eval_scores(raw)
    if scores is None:
        return {"parse_ok": False, "raw": raw}
    return {"parse_ok": True, **scores}


def _resolve_path(context: WorkflowContext, dotted: str | None) -> str | None:
    if not dotted:
        return None
    parts = dotted.split(".")
    head = parts[0]
    rest = parts[1:]
    cursor: Any
    if head == "trigger":
        cursor = context.trigger
    elif head == "steps":
        cursor = context.steps
    else:
        return None
    for part in rest:
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor if isinstance(cursor, str) else None


def default_function_registry() -> FunctionRegistry:
    """Registry pre-populated with stock step functions."""
    return FunctionRegistry(
        {
            "noop": noop,
            "pdf_extract": pdf_extract,
            "route_by_classification": route_by_classification,
            "record_evaluation": record_evaluation,
        }
    )
