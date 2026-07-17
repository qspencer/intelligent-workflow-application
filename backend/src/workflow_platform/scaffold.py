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

OUTPUT CONTRACT — violating it wastes the entire call:
- Respond with EXACTLY ONE JSON object. The first character of your response
  is `{{` and the last is `}}`. No prose before it, no explanation after it,
  no markdown code fences.
- Always produce a workflow, even for vague or ambiguous requests: make the
  most reasonable assumption and record it in the "description" field.

Shape:
{{
  "name": "<short title>",
  "description": "<one sentence; note any assumptions here>",
  "trigger": {{"type": "<trigger type>", "config": {{}}}},
  "steps": [
    {{"id": "<snake_case_id>", "type": "deterministic", "function": "<function>", "config": {{}}}},
    {{"id": "<snake_case_id>", "type": "agentic", "goal": "<instructions>",
      "model": "{DEFAULT_SCAFFOLD_MODEL}", "tools": ["<tool>"]}}
  ],
  "edges": [
    {{"from": "<step id>", "to": "<step id>"}},
    {{"from": "<step id>", "to": "<step id>", "condition": "<python expression>"}}
  ]
}}

Choosing the step type — this is a cost decision, get it right:
- MECHANICAL work (move, copy, rename, save, forward, extract text, post a
  fixed message, append to a file, call a fixed URL) MUST be a deterministic
  step running a catalog function. Deterministic steps are free and instant.
- An agentic step is allowed ONLY where the task needs judgment on content:
  classify, summarize, decide, draft, analyze, interpret. If you can write
  the behavior as a fixed rule, it is NOT agentic.
- A request like "when X happens, do Y with the file" is usually 1-2
  deterministic steps and zero agents.

Branching and routing:
- An edge may carry "condition": a Python expression over prior step outputs
  (e.g. "steps['classify']['output_text'] == 'invoice'" or
  "'urgent' in steps['triage']['output_text']"). The target runs only when
  the condition is true; a step whose incoming conditions are all false is
  skipped.
- Route to DIFFERENT next steps with multiple conditional edges from the same
  source — e.g. classify → file_invoice (condition: invoice), classify →
  file_receipt (condition: receipt). Requests that say "if/otherwise/route/
  depending on" need conditional edges, not a single linear chain.
- Two unconditional edges from one source run both targets in parallel.

Rules:
- Use ONLY the trigger types, functions, and tools listed below. Never invent names.
- Steps must form a DAG (no cycles); the first step has no incoming edge.
- Give each agent step only the tools it needs (often none: "tools": []).
- Keep it minimal — the smallest workflow that satisfies the request.

Available triggers:
{triggers or "(none)"}

Available functions (deterministic steps):
{functions or "(none)"}

Available tools (for agent steps):
{tools or "(none)"}
"""


def extract_json(text: str) -> dict[str, Any]:
    """Parse the model's JSON, tolerating code fences and surrounding prose.

    Scans for the first parseable JSON object: `raw_decode` at each `{`
    position, so leading prose, trailing prose ("Extra data"), and braces
    inside the surrounding chatter all survive. The first complete object
    wins — the scaffold prompt demands exactly one.
    """
    s = text.strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None
    pos = s.find("{")
    while pos != -1:
        try:
            data, _ = decoder.raw_decode(s, pos)
        except json.JSONDecodeError as exc:
            last_error = exc
            pos = s.find("{", pos + 1)
            continue
        if isinstance(data, dict):
            return data
        pos = s.find("{", pos + 1)
    if last_error is not None:
        raise ScaffoldError(f"Model output was not valid JSON: {last_error}") from last_error
    raise ScaffoldError("No JSON object found in model output")


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
