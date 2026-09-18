"""Natural-language → workflow scaffold (C7.1).

A single Bedrock call turns a plain-English description into a draft workflow
definition. The model is handed the live authoring catalog (the same one the
canvas pickers use) so it can only reference real triggers / functions / tools,
and is told to emit JSON in the WorkflowDefinition shape. The endpoint then
coerces, ids, structurally validates, and persists it as an editable draft.
"""

from __future__ import annotations

import json
import re
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


class ScaffoldIdError(ScaffoldError):
    """The draft's step ids are not usable as a mapping source."""


#: Step references the model actually writes, in the syntaxes the engine reads:
#: `steps['x']`, `steps["x"]`, `steps.x`, `{steps.x.field}`. Anything else in a
#: string is NOT a step reference — a function name (`pdf_extract`), a path
#: (`/inbox/extract/`) or a comparison literal (`== 'extract'`) must survive
#: untouched, and R6 showed substring replacement mangling all three.
_STEP_REF = re.compile(
    r"""(?P<prefix>steps\s*)
        (?:
            (?P<br>\[\s*(?P<q>['"]))(?P<qid>[A-Za-z0-9_\-]+)(?P=q)\s*\]
          | (?P<dot>\.)(?P<did>[A-Za-z0-9_\-]+)
        )""",
    re.VERBOSE,
)


def mint_platform_step_ids(raw: dict[str, Any]) -> dict[str, Any]:
    """Replace MODEL-chosen step ids with platform-minted ones, in place.

    R5 F4. A scaffolded definition is drafted by an LLM and persisted without a
    human reading it, so its step ids are model-chosen strings — and the trace
    projection publishes step ids as dictionary KEYS in `context.steps`. The
    keys cannot simply be withheld: the grant-holder rehydration path walks
    `context.steps` BY step id to merge raw back, so dropping them would break
    raw recovery for the people entitled to it.

    R6 F1 — this rewrites DECLARED REFERENCE POSITIONS ONLY. The first version
    did a substring replacement over every string in the draft, which turned
    the function `pdf_extract` into `pdf_step_1` (structurally valid, so it
    persisted and failed at run time), rewrote `/inbox/extract/` inside a path,
    and changed a condition's comparison LITERAL along with its step reference.
    Whole-word matching would still have broken the literal. So: ids, edge
    `from`/`to` and `inputs` are rewritten as fields, and inside free text only
    `steps['x']` / `steps["x"]` / `steps.x` references are rewritten.

    Original ids are validated BEFORE the mapping is built — duplicates used to
    be silently minted apart, destroying the very collision that definition
    validation exists to reject.
    """
    steps = raw.get("steps")
    if not isinstance(steps, list):
        return raw

    originals: list[str] = []
    for step in steps:
        if isinstance(step, dict) and isinstance(step.get("id"), str) and step["id"]:
            originals.append(step["id"])
    if len(set(originals)) != len(originals):
        dupes = sorted({i for i in originals if originals.count(i) > 1})
        raise ScaffoldIdError(f"draft repeats step id(s): {', '.join(dupes)}")

    mapping = {old: f"step_{i}" for i, old in enumerate(originals, 1)}
    if all(old == new for old, new in mapping.items()):
        return raw

    def rewrite_text(s: str) -> str:
        def sub(m: re.Match[str]) -> str:
            ref = m.group("qid") or m.group("did")
            if ref not in mapping:
                return m.group(0)
            if m.group("br"):
                return f"{m.group('prefix')}[{m.group('q')}{mapping[ref]}{m.group('q')}]"
            return f"{m.group('prefix')}.{mapping[ref]}"

        return _STEP_REF.sub(sub, s)

    def walk(node: Any, *, field: str | None = None) -> Any:
        # Declared REFERENCE positions are rewritten as whole values…
        if field in ("from", "to") and isinstance(node, str):
            return mapping.get(node, node)
        if field == "inputs" and isinstance(node, list):
            return [mapping.get(v, v) if isinstance(v, str) else walk(v) for v in node]
        # …every other string is free text: only step REFERENCES inside it move.
        if isinstance(node, str):
            return rewrite_text(node)
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, dict):
            return {k: (v if k == "id" else walk(v, field=k)) for k, v in node.items()}
        return node

    for step in steps:
        if isinstance(step, dict) and isinstance(step.get("id"), str):
            step["id"] = mapping.get(step["id"], step["id"])
    out = walk(raw)
    return out if isinstance(out, dict) else raw
