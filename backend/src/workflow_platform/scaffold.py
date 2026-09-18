"""Natural-language → workflow scaffold (C7.1).

A single Bedrock call turns a plain-English description into a draft workflow
definition. The model is handed the live authoring catalog (the same one the
canvas pickers use) so it can only reference real triggers / functions / tools,
and is told to emit JSON in the WorkflowDefinition shape. The endpoint then
coerces, ids, structurally validates, and persists it as an editable draft.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any

from workflow_platform.bedrock import BedrockClient
from workflow_platform.catalog import WorkflowCatalog

# Cheap-first default (VISION anti-goal #3). Override per deployment with
# WORKFLOW_PLATFORM_SCAFFOLD_MODEL when a stronger model earns its cost.
DEFAULT_SCAFFOLD_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


logger = logging.getLogger(__name__)


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


#: Config keys whose VALUE is a dotted context path (`steps.<id>.<field>`),
#: derived from `engine/functions.py` and pinned against drift by
#: `test_context_path_keys_match_the_functions_that_read_them`. R8 P1: minting
#: renamed the steps and left these pointing at the old id, so a renamed
#: workflow PERSISTED and then FAILED at run time. They are rewritten as
#: references; every other config value stays data.
_CONTEXT_PATH_KEYS: frozenset[str] = frozenset(
    {
        "attention_from",
        "classification_from",
        "content_from",
        "evaluation_from",
        "extraction_from",
        "filepath_from",
        "paths_from",
        "route_from",
        "rows_from",
        "source_from",
        "triage_from",
        "value_from",
    }
)

#: The learned-memory spec's own reference fields (same shape, different home).
_LEARNED_MEMORY_PATH_KEYS: frozenset[str] = frozenset({"query_from", "date_from", "ref_from"})

#: A config key left UNSET still resolves — through a default that names a
#: step by id. Rename that step and the default dangles, so omitting the key
#: fails just as surely as setting it. Where a default names a step being
#: renamed, minting MATERIALISES it with the new id.
_STEP_PATH_DEFAULTS: dict[str, str] = {
    "classification_from": "steps.classify.output_text",
    "evaluation_from": "steps.evaluate.output_text",
    "extraction_from": "steps.extract.output_text",
    "route_from": "steps.precheck.route",
    "triage_from": "steps.triage.output_text",
}

#: A dotted step reference inside a context path: `steps.<id>` at the start.
_DOTTED_REF = re.compile(r"^(?P<prefix>steps\.)(?P<id>[A-Za-z0-9_\-]+)")


def _reads_config_key(function: Any, key: str) -> bool:
    """Whether a default for `key` should be materialised onto this function.

    FAIL-SAFE by design: True unless the function is KNOWN and provably does
    not read the key. Writing a config key a function ignores is cosmetic
    noise; omitting one it needs produces a definition that parses and then
    FAILS at run time, which is the defect this whole path exists to prevent
    (R8 P1). An unregistered or custom function is therefore treated as a
    reader, not as a non-reader.

    Only the five keys in `_STEP_PATH_DEFAULTS` reach here, and only when the
    step their default names is actually being renamed, so the noise is
    bounded to references that would otherwise dangle.
    """
    if not isinstance(function, str):
        return True
    from workflow_platform.engine.functions import default_function_registry

    fn = default_function_registry().get(function)
    if fn is None:
        return True  # unknown function → assume it reads it
    import inspect

    try:
        return f'"{key}"' in (inspect.getsource(fn) or "")
    except (OSError, TypeError):
        return True


def _rewrite_context_path(value: Any, mapping: dict[str, str]) -> Any:
    """Rewrite `steps.<id>…` in a value known to BE a context path."""
    if not isinstance(value, str):
        return value

    def sub(m: re.Match[str]) -> str:
        sid = m.group("id")
        return m.group(0) if sid not in mapping else f"steps.{mapping[sid]}"

    return _DOTTED_REF.sub(sub, value, count=1)


#: A `{steps.<id>...}` placeholder in a TEMPLATE string (goals, text). Anchored
#: on the opening brace so it cannot match prose that merely mentions a step.
_TEMPLATE_REF = re.compile(r"(?P<open>\{)(?P<prefix>steps\.)(?P<id>[A-Za-z0-9_\-]+)")


def _rewrite_condition(expr: str, mapping: dict[str, str]) -> str:
    """Rewrite step references in a condition EXPRESSION, leaving literals alone.

    R7 P1: the previous version pattern-matched reference-shaped text anywhere
    in the string, so `steps['classify']['label'] == "steps['classify']"`
    had its comparison LITERAL rewritten along with its reference — the
    condition's truth value changed while the step data did not. No amount of
    regex fixes that: `steps['x']` inside quotes is indistinguishable from
    `steps['x']` outside them by pattern.

    Conditions are simpleeval expressions, i.e. Python syntax, so this PARSES
    them. A reference is a Subscript/Attribute on the name `steps`; a string
    Constant is a literal and is never touched. If the expression does not
    parse, it is returned UNCHANGED — refusing to guess beats corrupting it.
    """

    class _Rewriter(ast.NodeTransformer):
        def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
            self.generic_visit(node)
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "steps"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
                and node.slice.value in mapping
            ):
                return ast.Subscript(
                    value=node.value,
                    slice=ast.Constant(value=mapping[node.slice.value]),
                    ctx=node.ctx,
                )
            return node

        def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
            self.generic_visit(node)
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "steps"
                and node.attr in mapping
            ):
                return ast.Attribute(value=node.value, attr=mapping[node.attr], ctx=node.ctx)
            return node

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        logger.warning("condition did not parse; left unchanged rather than rewritten")
        return expr
    return ast.unparse(_Rewriter().visit(tree))


def _rewrite_template(text: str, mapping: dict[str, str]) -> str:
    """Rewrite `{steps.<id>...}` placeholders only — never bare prose."""

    def sub(m: re.Match[str]) -> str:
        sid = m.group("id")
        return m.group(0) if sid not in mapping else f"{{steps.{mapping[sid]}"

    return _TEMPLATE_REF.sub(sub, text)


def mint_platform_step_ids(raw: dict[str, Any]) -> dict[str, Any]:
    """Replace MODEL-chosen step ids with platform-minted ones, in place.

    R5 F4. A scaffolded definition is drafted by an LLM and persisted without a
    human reading it, so its step ids are model-chosen strings — and the trace
    projection publishes step ids as dictionary KEYS in `context.steps`. The
    keys cannot simply be withheld: the grant-holder rehydration path walks
    `context.steps` BY step id, so dropping them breaks raw recovery for the
    people entitled to it.

    R7 P1 — reference positions are identified by their FULL PATH, not by field
    name. The previous version rewrote any nested key called `from`, `to` or
    `inputs`, so a deterministic function's ordinary config
    (`copy_files` with `from`/`to` paths) was rewritten as if it named steps.
    Only these positions are references:

        steps[i].id        edges[i].from      edges[i].to      steps[i].inputs[*]

    Everything else is data. Free text is rewritten only where a reference is
    unambiguous: a parsed expression in `edges[i].condition`, and `{steps.x}`
    placeholders elsewhere.
    """
    steps = raw.get("steps")
    if not isinstance(steps, list):
        return raw

    originals = [
        s["id"] for s in steps if isinstance(s, dict) and isinstance(s.get("id"), str) and s["id"]
    ]
    if len(set(originals)) != len(originals):
        dupes = sorted({i for i in originals if originals.count(i) > 1})
        raise ScaffoldIdError(f"draft repeats step id(s): {', '.join(dupes)}")

    mapping = {old: f"step_{i}" for i, old in enumerate(originals, 1)}
    if all(o == n for o, n in mapping.items()):
        return raw

    def rewrite_text(node: Any, *, is_condition: bool) -> Any:
        if not isinstance(node, str):
            return node
        return (
            _rewrite_condition(node, mapping) if is_condition else _rewrite_template(node, mapping)
        )

    def _rewrite_config(cfg: dict[str, Any], function: Any, m: dict[str, str]) -> dict[str, Any]:
        """steps[i].config: rewrite CONTEXT PATHS, leave every other value alone.

        R8 P1. `evaluation_from: "steps.evaluate.output_text"` is a reference,
        and minting left it pointing at a step that no longer exists — the
        definition persisted and then FAILED at run time. A config value is a
        reference only when its KEY says so; everything else stays data, the
        rule R6/R7 established for `from`/`to`/`inputs`.
        """
        out_cfg: dict[str, Any] = {}
        for k, v in cfg.items():
            if k in _CONTEXT_PATH_KEYS or k in _LEARNED_MEMORY_PATH_KEYS:
                out_cfg[k] = _rewrite_context_path(v, m)
            else:
                out_cfg[k] = walk_value(v)
        # An UNSET key still resolves, through a default that names a step. If
        # that step is renamed the default dangles, so write it out explicitly.
        for key, default in _STEP_PATH_DEFAULTS.items():
            if key in out_cfg:
                continue
            rewritten = _rewrite_context_path(default, m)
            if rewritten != default and _reads_config_key(function, key):
                out_cfg[key] = rewritten
        return out_cfg

    def walk_value(node: Any) -> Any:
        """Ordinary DATA: only `{steps.x}` placeholders move inside it."""
        if isinstance(node, str):
            return _rewrite_template(node, mapping)
        if isinstance(node, list):
            return [walk_value(v) for v in node]
        if isinstance(node, dict):
            return {k: walk_value(v) for k, v in node.items()}
        return node

    out = dict(raw)

    new_steps = []
    for s in steps:
        if not isinstance(s, dict):
            new_steps.append(walk_value(s))
            continue
        ns: dict[str, Any] = {}
        for k, v in s.items():
            if k == "id" and isinstance(v, str):
                ns[k] = mapping.get(v, v)  # steps[i].id
            elif k == "inputs" and isinstance(v, list):  # steps[i].inputs[*]
                ns[k] = [mapping.get(x, x) if isinstance(x, str) else walk_value(x) for x in v]
            elif k == "config" and isinstance(v, dict):  # steps[i].config.*_from
                ns[k] = _rewrite_config(v, s.get("function"), mapping)
            else:
                ns[k] = walk_value(v)
        new_steps.append(ns)
    out["steps"] = new_steps

    edges = raw.get("edges")
    if isinstance(edges, list):
        new_edges = []
        for e in edges:
            if not isinstance(e, dict):
                new_edges.append(walk_value(e))
                continue
            ne: dict[str, Any] = {}
            for k, v in e.items():
                if k in ("from", "to") and isinstance(v, str):  # edges[i].from/to
                    ne[k] = mapping.get(v, v)
                elif k == "condition":
                    ne[k] = rewrite_text(v, is_condition=True)
                else:
                    ne[k] = walk_value(v)
            new_edges.append(ne)
        out["edges"] = new_edges

    for k, v in raw.items():
        if k not in ("steps", "edges"):
            out[k] = walk_value(v)
    return out
