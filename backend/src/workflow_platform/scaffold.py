"""Natural-language → workflow scaffold (C7.1).

A single Bedrock call turns a plain-English description into a draft workflow
definition. The model is handed the live authoring catalog (the same one the
canvas pickers use) so it can only reference real triggers / functions / tools,
and is told to emit JSON in the WorkflowDefinition shape. The endpoint then
coerces, ids, structurally validates, and persists it as an editable draft.
"""

from __future__ import annotations

import json
from typing import Any

from workflow_platform.bedrock import BedrockClient
from workflow_platform.catalog import WorkflowCatalog

# Cheap-first default (VISION anti-goal #3). Override per deployment with
# WORKFLOW_PLATFORM_SCAFFOLD_MODEL when a stronger model earns its cost.
DEFAULT_SCAFFOLD_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


class ScaffoldError(ValueError):
    """The model's output couldn't be turned into a workflow definition."""


def build_system_prompt(catalog: WorkflowCatalog) -> str:
    """System prompt for the scaffold call, with the catalog inlined so the
    model can only reference building blocks that actually exist."""
    triggers = "\n".join(
        f"- {t.type}: {t.description}"
        + (f" (config: {', '.join(f.name for f in t.config_fields)})" if t.config_fields else "")
        for t in catalog.triggers
    )
    functions = "\n".join(f"- {f.name}: {f.description}" for f in catalog.functions)
    tools = "\n".join(f"- {t.name} ({t.category}): {t.description}" for t in catalog.tools)
    return f"""You design automation workflows for a workflow engine. Given a \
plain-English description, output a single workflow definition.

Output ONLY a JSON object — no prose, no markdown code fences — with this shape:
{{
  "name": "<short title>",
  "description": "<one sentence>",
  "trigger": {{"type": "<trigger type>", "config": {{}}}},
  "steps": [
    {{"id": "<snake_case_id>", "type": "deterministic", "function": "<function>", "config": {{}}}},
    {{"id": "<snake_case_id>", "type": "agentic", "goal": "<instructions>",
      "model": "{DEFAULT_SCAFFOLD_MODEL}", "tools": ["<tool>"]}}
  ],
  "edges": [{{"from": "<step id>", "to": "<step id>"}}]
}}

Rules:
- Use ONLY the trigger types, functions, and tools listed below. Never invent names.
- Steps must form a DAG (no cycles); the first step has no incoming edge.
- Prefer a deterministic function over an agent step whenever one fits (cost-aware).
- Give each agent step only the tools it needs.
- Keep it minimal — the smallest workflow that satisfies the request.

Available triggers:
{triggers or "(none)"}

Available functions (deterministic steps):
{functions or "(none)"}

Available tools (for agent steps):
{tools or "(none)"}
"""


def extract_json(text: str) -> dict[str, Any]:
    """Parse the model's JSON, tolerating code fences and surrounding prose."""
    s = text.strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    if not s.startswith("{"):
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ScaffoldError("No JSON object found in model output")
        s = s[start : end + 1]
    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        raise ScaffoldError(f"Model output was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ScaffoldError("Model output JSON was not an object")
    return data


async def scaffold_workflow(
    bedrock: BedrockClient,
    *,
    model: str,
    description: str,
    catalog: WorkflowCatalog,
) -> dict[str, Any]:
    """One-shot scaffold call → the raw definition spec the model produced."""
    response = await bedrock.converse(
        model_id=model,
        messages=[{"role": "user", "content": [{"text": description}]}],
        system=[{"text": build_system_prompt(catalog)}],
        inference_config={"maxTokens": 2000, "temperature": 0.0},
    )
    message = response.get("output", {}).get("message", {})
    parts = [c["text"] for c in message.get("content", []) if isinstance(c, dict) and "text" in c]
    text = "\n".join(parts)
    if not text.strip():
        raise ScaffoldError("Model returned no text")
    return extract_json(text)
