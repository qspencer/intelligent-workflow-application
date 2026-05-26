"""Stock deterministic step functions.

Workflows reference these by name in `step.function`. New functions register
through `FunctionRegistry`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
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


async def append_file(
    config: dict[str, Any], context: WorkflowContext, world: World
) -> dict[str, Any]:
    """Append a string to a file via `World.fs`.

    Config:
      content_from — dotted context path to the string content (required).
      path         — destination file path (required). Created if absent.

    Each call appends `<content>\\n`, with a separator newline inserted if the
    existing file doesn't end in one. Useful for periodic log-style outputs
    written from a scheduled workflow.
    """
    content = _resolve_path(context, config.get("content_from"))
    if content is None:
        raise StepFailure("append_file requires `content_from` resolving to a string")
    path = config.get("path")
    if not isinstance(path, str) or not path:
        raise StepFailure("append_file requires `path` in config")

    existing = b""
    if await world.fs.exists(path):
        existing = await world.fs.read_bytes(path)
    separator = b"" if not existing or existing.endswith(b"\n") else b"\n"
    payload = existing + separator + content.encode() + b"\n"
    await world.fs.write_bytes(path, payload)

    return {
        "path": path,
        "appended_chars": len(content),
        "total_bytes": len(payload),
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


def _extract_pr_triage(raw: str) -> dict[str, Any] | None:
    """Pull PR-triage fields from an agent's text response.

    Expected JSON shape:
        {"category": <str>, "complexity": <str>, "needs_tests": <bool>,
         "summary": <str>, "concerns": [<str>...]}

    Each field is independently optional; the function copies through
    whatever is parseable and well-typed. Returns None when no JSON object
    is found at all."""
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
    for key in ("category", "complexity", "summary"):
        if isinstance(parsed.get(key), str):
            out[key] = parsed[key]
    if isinstance(parsed.get("needs_tests"), bool):
        out["needs_tests"] = parsed["needs_tests"]
    issues = parsed.get("concerns")
    if isinstance(issues, list):
        concerns = [str(x) for x in issues]
        out["concerns"] = concerns
        out["concern_count"] = len(concerns)
    return out or None


def _extract_paper_triage(raw: str) -> dict[str, Any] | None:
    """Pull research-paper-triage fields from an agent's text response.

    Expected JSON shape:
        {"relevance_score": 0..5,
         "relevance_bucket": <str>,
         "summary": <str>,
         "key_concepts": [<str>...],
         "tags": [<str>...]}

    Each field is independently optional; the function copies through
    whatever is parseable and well-typed. Returns None when no JSON object
    is found."""
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
    score = parsed.get("relevance_score")
    if isinstance(score, int | float) and not isinstance(score, bool):
        out["relevance_score"] = float(score)
    for key in ("relevance_bucket", "summary"):
        if isinstance(parsed.get(key), str):
            out[key] = parsed[key]
    for key in ("key_concepts", "tags"):
        value = parsed.get(key)
        if isinstance(value, list):
            out[key] = [str(x) for x in value]
    if "tags" in out:
        out["tag_count"] = len(out["tags"])
    if "key_concepts" in out:
        out["concept_count"] = len(out["key_concepts"])
    return out or None


async def record_paper_triage(
    config: dict[str, Any], context: WorkflowContext, world: World
) -> dict[str, Any]:
    """Parse a paper-triage agent's JSON output into structured fields.

    Same shape as `record_pr_triage`. Reads `output_text`, lifts
    `relevance_score` / `relevance_bucket` / `summary` / `key_concepts` /
    `tags` plus computed counts. `parse_ok=False` + `raw` on parse failure.
    """
    source = config.get("triage_from", "steps.triage.output_text")
    raw = _resolve_path(context, source)
    if not raw:
        raise StepFailure(f"record_paper_triage could not resolve {source!r}")
    triage = _extract_paper_triage(raw)
    if triage is None:
        return {"parse_ok": False, "raw": raw}
    return {"parse_ok": True, **triage}


def _extract_email_triage(raw: str) -> dict[str, Any] | None:
    """Pull email-triage fields from an agent's text response.

    Expected JSON shape:
        {"category": <str>, "confidence": 0..1, "reply_drafted": <bool>,
         "labels_applied": [<str>...], "summary": <str>}

    `confidence` is coerced to float; `labels_applied` is normalized to a
    list of strings with `label_count` computed. Returns None if no parseable
    JSON object is found. Unknown keys are dropped."""
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
    for key in ("category", "summary"):
        if isinstance(parsed.get(key), str):
            out[key] = parsed[key]
    confidence = parsed.get("confidence")
    if isinstance(confidence, int | float) and not isinstance(confidence, bool):
        out["confidence"] = float(confidence)
    if isinstance(parsed.get("reply_drafted"), bool):
        out["reply_drafted"] = parsed["reply_drafted"]
    labels = parsed.get("labels_applied")
    if isinstance(labels, list):
        out["labels_applied"] = [str(x) for x in labels]
        out["label_count"] = len(out["labels_applied"])
    return out or None


async def record_email_triage(
    config: dict[str, Any], context: WorkflowContext, world: World
) -> dict[str, Any]:
    """Parse an email-triage agent's JSON output into structured fields.

    Mirrors `record_pr_triage` / `record_paper_triage`. Reads the agent's
    `output_text`, lifts `category` / `confidence` / `reply_drafted` /
    `labels_applied` / `summary` plus a computed `label_count`.
    `parse_ok=False` + `raw` on parse failure — the workflow does not fail,
    so downstream queries (`SELECT ... WHERE parse_ok = false`) can find
    runs where the agent went off-script."""
    source = config.get("triage_from", "steps.triage.output_text")
    raw = _resolve_path(context, source)
    if not raw:
        raise StepFailure(f"record_email_triage could not resolve {source!r}")
    triage = _extract_email_triage(raw)
    if triage is None:
        return {"parse_ok": False, "raw": raw}
    return {"parse_ok": True, **triage}


def _extract_invoice_fields(raw: str) -> dict[str, Any] | None:
    """Pull invoice-extraction fields from an agent's text response.

    Expected JSON shape (every field independently optional — copy through
    whatever is well-typed):
        {"invoice_number": <str>, "customer_name": <str>,
         "invoice_date": <ISO str>, "ship_mode": <str>,
         "ship_to_city": <str>, "ship_to_country": <str>,
         "order_id": <str>,
         "subtotal": <float>, "shipping": <float>, "total": <float>,
         "line_items": [{"item": <str>, "category": <str>,
                         "quantity": <int>, "rate": <float>, "amount": <float>}]}

    Returns None when no JSON object is found at all."""
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
    for key in (
        "invoice_number",
        "customer_name",
        "invoice_date",
        "ship_mode",
        "ship_to_city",
        "ship_to_country",
        "order_id",
    ):
        if isinstance(parsed.get(key), str):
            out[key] = parsed[key]
    for key in ("subtotal", "discount", "shipping", "total"):
        value = parsed.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            out[key] = float(value)
    items_raw = parsed.get("line_items")
    if isinstance(items_raw, list):
        items: list[dict[str, Any]] = []
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            clean: dict[str, Any] = {}
            for k in ("item", "category"):
                if isinstance(item.get(k), str):
                    clean[k] = item[k]
            if isinstance(item.get("quantity"), int | float) and not isinstance(
                item.get("quantity"), bool
            ):
                clean["quantity"] = float(item["quantity"])
            for k in ("rate", "amount"):
                v = item.get(k)
                if isinstance(v, int | float) and not isinstance(v, bool):
                    clean[k] = float(v)
            if clean:
                items.append(clean)
        out["line_items"] = items
        out["line_item_count"] = len(items)
    return out or None


async def record_invoice_extraction(
    config: dict[str, Any], context: WorkflowContext, world: World
) -> dict[str, Any]:
    """Parse an invoice-extraction agent's JSON output into structured fields
    + invariant checks.

    Mirrors the other `record_*` functions: reads the agent's `output_text`
    (default `"steps.extract.output_text"`), lifts the documented fields,
    and computes three invariant checks the SuperStore-template invoices
    should always satisfy:

      - `total_balanced` — `subtotal - discount + shipping == total`
        (within 0.01 to absorb float rounding). Discount defaults to 0.0
        when absent — most invoices don't have a Discount line.
      - `line_items_sum_matches_subtotal` — `sum(line_item.amount) == subtotal`
        within 0.01. Line-item amounts are pre-discount.
      - `invoice_date_iso` — `True` if `invoice_date` parses as ISO date.

    `parse_ok=False` + `raw` on parse failure. Invariant checks are best-
    effort: missing fields → the corresponding `*_balanced`/`*_matches_*`
    is `null`, not an error.
    """
    source = config.get("extraction_from", "steps.extract.output_text")
    raw = _resolve_path(context, source)
    if not raw:
        raise StepFailure(f"record_invoice_extraction could not resolve {source!r}")
    fields = _extract_invoice_fields(raw)
    if fields is None:
        return {"parse_ok": False, "raw": raw}

    # Invariant checks (best-effort; missing fields → null).
    invariants: dict[str, Any] = {}
    subtotal = fields.get("subtotal")
    shipping = fields.get("shipping")
    total = fields.get("total")
    # Discount is optional: agents emit 0.0 when no Discount line is present.
    # If the field is missing entirely, treat as 0.0 for the invariant.
    discount = fields.get("discount", 0.0)
    if all(isinstance(x, float) for x in (subtotal, shipping, total)):
        computed_total = (
            float(subtotal) - float(discount) + float(shipping)  # type: ignore[arg-type]
        )
        invariants["computed_total"] = computed_total
        invariants["total_balanced"] = (
            abs(computed_total - float(total)) < 0.01  # type: ignore[arg-type]
        )
    else:
        invariants["total_balanced"] = None

    items = fields.get("line_items") or []
    if items and isinstance(subtotal, float):
        items_sum = sum(item.get("amount", 0.0) for item in items if isinstance(item, dict))
        invariants["line_items_sum"] = items_sum
        invariants["line_items_sum_matches_subtotal"] = abs(items_sum - subtotal) < 0.01
    else:
        invariants["line_items_sum_matches_subtotal"] = None

    invoice_date = fields.get("invoice_date")
    if isinstance(invoice_date, str):
        try:
            datetime.fromisoformat(invoice_date)
            invariants["invoice_date_iso"] = True
        except (ValueError, TypeError):
            invariants["invoice_date_iso"] = False
    else:
        invariants["invoice_date_iso"] = None

    return {"parse_ok": True, **fields, **invariants}


async def route_by_value(
    config: dict[str, Any], context: WorkflowContext, world: World
) -> dict[str, Any]:
    """Copy a file into a subfolder named by a structured value pulled from context.

    Distinct from `route_by_classification`: the value here comes from a
    *structured* prior step output (e.g. `record_invoice_extraction`'s
    `ship_to_country`), not from text+JSON parsing.

    Config:
      source_from   — dotted context path to the source file (default `"trigger.file_path"`).
      value_from    — dotted context path to the routing value (required, must be a string).
      output_root   — destination root directory (required).
      valid_values  — optional allow-list; values outside fall through to `fallback`.
      fallback      — used when value is missing or not in `valid_values` (default `"unknown"`).

    The value is filesystem-sanitized (lowercased, non-alphanumerics → `_`)
    before being used as a directory name.
    """
    source = _resolve_path(context, config.get("source_from", "trigger.file_path"))
    if not source:
        raise StepFailure("route_by_value could not resolve source file path")

    value_path = config.get("value_from")
    if not isinstance(value_path, str) or not value_path:
        raise StepFailure("route_by_value requires `value_from` in config")
    value = _resolve_path(context, value_path)

    fallback = str(config.get("fallback", "unknown"))
    valid_values = config.get("valid_values")
    if not value or (isinstance(valid_values, list) and value not in valid_values):
        value = fallback

    safe_value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.lower()).strip("_") or fallback

    output_root = config.get("output_root")
    if not isinstance(output_root, str) or not output_root:
        raise StepFailure("route_by_value requires `output_root` in config")

    filename = PurePosixPath(source).name
    destination = str(PurePosixPath(output_root) / safe_value / filename)

    payload = await world.fs.read_bytes(source)
    await world.fs.write_bytes(destination, payload)

    return {
        "source": source,
        "destination": destination,
        "value": value,
        "safe_value": safe_value,
        "bytes_copied": len(payload),
    }


async def record_pr_triage(
    config: dict[str, Any], context: WorkflowContext, world: World
) -> dict[str, Any]:
    """Parse a PR-triage agent's JSON output into structured fields.

    Mirrors `record_evaluation` for the eval loop: reads the agent's
    `output_text`, lifts `category` / `complexity` / `needs_tests` /
    `summary` / `concerns` / `concern_count` into the step's own output.
    `parse_ok=False` + `raw` on parse failures — the workflow does not
    fail, so downstream queries (`SELECT ... WHERE parse_ok = false`)
    can find runs where the agent went off-script."""
    source = config.get("triage_from", "steps.triage.output_text")
    raw = _resolve_path(context, source)
    if not raw:
        raise StepFailure(f"record_pr_triage could not resolve {source!r}")
    triage = _extract_pr_triage(raw)
    if triage is None:
        return {"parse_ok": False, "raw": raw}
    return {"parse_ok": True, **triage}


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
            "record_pr_triage": record_pr_triage,
            "record_paper_triage": record_paper_triage,
            "record_email_triage": record_email_triage,
            "record_invoice_extraction": record_invoice_extraction,
            "route_by_value": route_by_value,
            "append_file": append_file,
        }
    )
