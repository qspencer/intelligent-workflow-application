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


#: Per-FUNCTION reference declaration (R9 P1/P2). Two separate facts, because
#: conflating them inserted behaviour a workflow never asked for:
#:
#:   `fields`   — the `*_from` config keys THIS function reads. Only these are
#:                rewritten as references; the same key name on another
#:                function (or on `noop`, where it is ordinary returned data)
#:                stays data.
#:   `defaults` — defaults defined in THIS function's own body. R9 P2:
#:                `record_email_triage` READS `route_from`, but that default
#:                lives in the helper `_record_codified`; materialising it
#:                onto the caller switched on routing behaviour the original
#:                never had, and the renamed workflow failed. A key a function
#:                reads without defaulting is left absent.
#:
#: Derived from `engine/functions.py` and pinned by
#: `test_function_reference_declaration_matches_the_source`.
FUNCTION_REFERENCES: dict[str, dict[str, Any]] = {
    "_record_codified": {
        "fields": ["route_from"],
        "defaults": {"route_from": "steps.precheck.route"},
    },
    "append_file": {"fields": ["content_from"], "defaults": {}},
    "copy_files": {"fields": ["paths_from"], "defaults": {}},
    "extract_archive": {"fields": ["paths_from"], "defaults": {}},
    "filter_rows_by_date": {"fields": ["rows_from"], "defaults": {}},
    "pdf_extract": {"fields": ["filepath_from"], "defaults": {}},
    "record_email_triage": {
        "fields": ["attention_from", "route_from", "triage_from"],
        "defaults": {"triage_from": "steps.triage.output_text"},
    },
    "record_evaluation": {
        "fields": ["evaluation_from"],
        "defaults": {"evaluation_from": "steps.evaluate.output_text"},
    },
    "record_invoice_extraction": {
        "fields": ["extraction_from"],
        "defaults": {"extraction_from": "steps.extract.output_text"},
    },
    "record_paper_triage": {
        "fields": ["triage_from"],
        "defaults": {"triage_from": "steps.triage.output_text"},
    },
    "record_pr_triage": {
        "fields": ["triage_from"],
        "defaults": {"triage_from": "steps.triage.output_text"},
    },
    "route_by_classification": {
        "fields": ["classification_from", "source_from"],
        "defaults": {"classification_from": "steps.classify.output_text"},
    },
    "route_by_value": {"fields": ["source_from", "value_from"], "defaults": {}},
    "write_csv": {"fields": ["rows_from"], "defaults": {}},
}

#: An edge's endpoints, in BOTH spellings. The wire form is `from`/`to` (what
#: the scaffold's model emits and what the YAML uses), but `Edge` stores them
#: as `source`/`target`, so a definition that has been through `model_dump()`
#: uses those. Matching only the wire form made minting silently NO-OP on a
#: dumped definition and produce one whose edges point at steps that no longer
#: exist — found by the round-trip probe over the real shipped definitions,
#: not by any unit test.
_EDGE_ENDPOINT_KEYS = frozenset({"from", "to", "source", "target"})


def _remap_step_ref(path: str, mapping: dict[str, str], *, allow_prior: bool) -> str | None:
    """Apply the id mapping to a dotted step reference. Returns None when the
    reference does not name a renamed step, so callers leave it untouched.

    THE one place that decides where a step id starts and ends. It splits the
    way the engine's resolver splits — `dotted.partition(".")`, the id is
    whatever lies between the dots — so the grammar is defined by execution
    rather than restated.

    R10 P1 fixed the context-path caller by splitting instead of pattern-
    matching a charclass. R11 P2 found the SAME defect still live in the prose
    caller, which kept its own `[A-Za-z0-9_-]+` grammar: `\u0060steps.pr\u00e9pare.value\u0060`
    in an agent goal was left naming a step that no longer existed, so the
    model read one id while the engine delivered another. Two callers with two
    grammars is the M3 class; the fix is one splitter, not a second patch.
    """
    head, sep, rest = path.partition(".")
    if not sep:
        return None
    if head == "steps":
        prefix = "steps."
    elif allow_prior and head == "prior_steps":
        prefix = "prior_steps."
    else:
        return None
    step_id, sep2, tail = rest.partition(".")
    if step_id not in mapping:
        return None
    return f"{prefix}{mapping[step_id]}{sep2}{tail}"


def _rewrite_context_path(value: Any, mapping: dict[str, str]) -> Any:
    """Rewrite `steps.<id>…` in a value known to BE a context path.

    `prior_steps.` is NOT accepted here: the resolver takes only `trigger.`
    and `steps.` heads, so in a resolvable path that word is not a reference.
    """
    if not isinstance(value, str):
        return value
    return _remap_step_ref(value, mapping, allow_prior=False) or value


#: The ENGINE's placeholder grammar — IMPORTED, not copied. R10 follow-up:
#: this was a hand-copy of `executor._TEMPLATE_PLACEHOLDER`, identical that
#: day and free to diverge the next. Two implementations of one rule is the
#: M3 class that produced two projector-version constants and a rewriter
#: whose identifier grammar was narrower than the resolver's. Importing makes
#: divergence impossible rather than detectable.
from workflow_platform.engine.executor import (  # noqa: E402
    _TEMPLATE_PLACEHOLDER as _TEMPLATE_PLACEHOLDER,
)

#: Local alias, explicitly re-exported so the grammar-agreement test can
#: assert the two names are ONE object rather than two equal strings.
_ENGINE_PLACEHOLDER = _TEMPLATE_PLACEHOLDER


def _rewrite_condition(expr: str, mapping: dict[str, str]) -> str:
    """Rewrite step references in a condition EXPRESSION, leaving literals be.

    R6: pattern-matching reference-shaped text rewrote a comparison LITERAL
    along with its reference, changing the condition's truth value. Conditions
    are simpleeval expressions (Python syntax), so this PARSES them: a
    reference is a Subscript/Attribute on the name `steps`; a string Constant
    is a literal. An expression that does not parse is returned UNCHANGED —
    refusing to guess beats corrupting it."""

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


#: A step reference written in prose but DELIMITED by backticks, e.g.
#: "the text in `steps.extract.output_text`". Agent goals carry these: the
#: scaffold's model writes the goal AND the step ids, so minting the ids left
#: the instructions naming a step that no longer exists. Delimited, so this is
#: not the free-prose substitution that corrupted comparison literals in round
#: 6 — an UNdelimited mention is still left alone, deliberately.
#: The DELIMITED forms an agent goal uses to name a step: a backtick or an
#: angle bracket. Both are references by construction. Bare prose stays
#: untouched — that substitution is what corrupted a comparison literal in
#: round 6, and "the extract step ran" is English, not a reference.
#: `prior_steps.<id>` is accepted too: when an agentic step declares no
#: `inputs`, the engine hands the model `{"trigger": …, "prior_steps":
#: context.steps}`, so goals name steps that way. Not a resolvable path (the
#: resolver takes only `trigger.`/`steps.` heads) but it IS what the agent
#: reads, so a delimited one goes stale on rename.
#:
#: BOTH delimiters must CLOSE. Self-audit: `<` alone matched the less-than in
#: prose like "if x<steps.a.count", and an unterminated backtick matched
#: nothing meaningful — requiring the closing delimiter makes the form a
#: reference by construction rather than by luck.
#: R11 P2: the body is now "everything up to the closing delimiter that is
#: not whitespace", NOT a charclass of permitted identifier characters. The
#: delimiters already prove this is a reference; deciding where the id ends
#: is `_remap_step_ref`'s job, and doing it here too is what let the two
#: grammars diverge. Whitespace stays excluded so an unterminated delimiter
#: cannot swallow a line of prose.
_DELIMITED_REF = re.compile(
    r"`(?P<body>(?:prior_)?steps\.[^`\s]+)`"
    r"|"
    r"<(?P<body2>(?:prior_)?steps\.[^<>\s]+)>"
)


def _rewrite_template(text: str, mapping: dict[str, str]) -> str:
    r"""Rewrite `{steps.<id>…}` placeholders. ONLY for fields the engine
    actually renders as templates.

    R10 P1: this was applied to every string on the reasoning that `{…}` is a
    platform template form. The premise was right and the conclusion was
    backwards — the engine renders placeholders in EXACTLY ONE place,
    `learned_memory.observations[].text` (`_render_observation_template`).
    Everywhere else `{steps.a.value}` is ordinary DATA, and rewriting it
    changed a `noop` config literal so a downstream condition comparing
    against that literal stopped matching: the original ran both steps, the
    renamed one silently skipped the second.

    The placeholder grammar mirrors the engine's `\{([A-Za-z0-9_.]+)\}`
    deliberately — a placeholder the engine cannot resolve is not one we
    should rewrite.
    """

    def placeholder(m: re.Match[str]) -> str:
        path = m.group(1)
        head, sep, rest = path.partition(".")
        if head != "steps" or not sep:
            return m.group(0)
        step_id, sep2, tail = rest.partition(".")
        if step_id not in mapping:
            return m.group(0)
        return "{" + f"steps.{mapping[step_id]}{sep2}{tail}" + "}"

    return _ENGINE_PLACEHOLDER.sub(placeholder, text)


def _rewrite_prose_references(text: str, mapping: dict[str, str]) -> str:
    """Additionally rewrite DELIMITED references, for AGENT-FACING text only.

    Self-audit finding: applied to every string, this rewrote ordinary config
    data (`dest_dir: "/out/<steps.a>/"`) and trigger config, where `<…>` is
    not a template form — nothing in the platform renders it. The convention
    lives in prose the MODEL reads (`goal`, `system_prompt`), so that is where
    it is applied. Elsewhere a backticked or bracketed path is data.
    """

    def delimited(m: re.Match[str]) -> str:
        backticked = m.group("body") is not None
        body = m.group("body") if backticked else m.group("body2")
        rewritten = _remap_step_ref(body, mapping, allow_prior=True)
        if rewritten is None:
            return m.group(0)
        return f"`{rewritten}`" if backticked else f"<{rewritten}>"

    return _DELIMITED_REF.sub(delimited, _rewrite_template(text, mapping))


def mint_platform_step_ids(raw: dict[str, Any]) -> dict[str, Any]:
    """Replace MODEL-chosen step ids with platform-minted ones, in place.

    R5 F4: a scaffolded definition is drafted by an LLM and persisted unread,
    and the trace projection publishes step ids as dictionary KEYS. The keys
    cannot be withheld instead — the grant-holder rehydration path walks
    `context.steps` BY step id — so the origin is fixed at the source.

    Rewriting is BY SCHEMA LOCATION, and the two kinds of reference are kept
    apart, because conflating them is what kept breaking workflows:

      STEP IDs (a bare id)        edges[i].from, edges[i].to
      CONTEXT PATHS (`steps.x.y`) steps[i].inputs[*]  (agentic: R9 P1, these
                                    are PATHS, not ids — treating them as ids
                                    left them untouched and the input resolved
                                    to null)
                                  steps[i].pin_params[*]  (fail-closed: an
                                    unresolved pin FAILS the step)
                                  steps[i].config[k] for the keys THIS
                                    function reads
                                  learned_memory.recall.query_from
                                  learned_memory.observations[*].date_from
                                  learned_memory.observations[*].ref_from
      EXPRESSIONS                 edges[i].condition (parsed; a string
                                    Constant is never a reference)
      TEMPLATES                   any other string: `{steps.x…}` only

    Everything else is DATA and is left exactly as written.
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

    def data(node: Any) -> Any:
        """DATA is returned UNCHANGED (R10 P1).

        Nothing outside a declared reference position, an agent-facing goal
        and the one template field the engine renders is a reference. A
        literal that merely LOOKS like one — `{steps.a.value}` in a config, a
        path containing a step name, prose — is the workflow's own data, and
        rewriting it changed behaviour."""
        return node

    def path(value: Any) -> Any:
        return _rewrite_context_path(value, mapping)

    def rewrite_step(s: Any) -> Any:
        if not isinstance(s, dict):
            return data(s)
        decl = FUNCTION_REFERENCES.get(str(s.get("function")), {"fields": [], "defaults": {}})
        ref_fields = set(decl["fields"])
        out_s: dict[str, Any] = {}
        for k, v in s.items():
            if k == "id" and isinstance(v, str):
                out_s[k] = mapping.get(v, v)
            elif k == "inputs" and isinstance(v, list):
                out_s[k] = [path(x) for x in v]  # CONTEXT PATHS
            elif k == "pin_params" and isinstance(v, dict):
                out_s[k] = {pk: path(pv) for pk, pv in v.items()}
            elif k == "config" and isinstance(v, dict):
                out_s[k] = {
                    ck: (path(cv) if ck in ref_fields else data(cv)) for ck, cv in v.items()
                }
            elif k in ("goal", "system_prompt") and isinstance(v, str):
                out_s[k] = _rewrite_prose_references(v, mapping)  # AGENT-FACING
            else:
                out_s[k] = data(v)
        # A default names a step by id, so renaming that step dangles it. Only
        # defaults THIS function defines, and only when the named step moved.
        # R9 P1: materialise even when `config` was absent entirely.
        pending = {
            k: path(d)
            for k, d in decl["defaults"].items()
            if k not in (out_s.get("config") or {}) and path(d) != d
        }
        if pending:
            out_s["config"] = {**(out_s.get("config") or {}), **pending}
        return out_s

    out = dict(raw)
    out["steps"] = [rewrite_step(s) for s in steps]

    edges = raw.get("edges")
    if isinstance(edges, list):
        new_edges = []
        for e in edges:
            if not isinstance(e, dict):
                new_edges.append(data(e))
                continue
            ne: dict[str, Any] = {}
            for k, v in e.items():
                if k in _EDGE_ENDPOINT_KEYS and isinstance(v, str):
                    ne[k] = mapping.get(v, v)  # STEP IDs
                elif k == "condition":
                    ne[k] = _rewrite_condition(v, mapping) if isinstance(v, str) else data(v)
                else:
                    ne[k] = data(v)
            new_edges.append(ne)
        out["edges"] = new_edges

    lm = raw.get("learned_memory")
    if isinstance(lm, dict):
        new_lm: dict[str, Any] = {}
        for k, v in lm.items():
            if k == "recall" and isinstance(v, dict):
                new_lm[k] = {
                    rk: (path(rv) if rk == "query_from" else data(rv)) for rk, rv in v.items()
                }
            elif k == "observations" and isinstance(v, list):
                new_lm[k] = [
                    {
                        ok: (
                            path(ov)
                            if ok in ("date_from", "ref_from")
                            else (
                                _rewrite_template(ov, mapping)
                                if ok == "text" and isinstance(ov, str)
                                else data(ov)
                            )
                        )
                        for ok, ov in o.items()
                    }
                    if isinstance(o, dict)
                    else data(o)
                    for o in v
                ]
            else:
                new_lm[k] = data(v)
        out["learned_memory"] = new_lm

    # G12 C1: `questions.candidate_from` is a context PATH into a step's
    # output, exactly like `recall.query_from`, so minting must rewrite
    # it or the reference dangles when step ids change. Caught by
    # `test_minting_every_real_shipped_definition_leaves_no_dangling_
    # reference` the moment the block was added to a shipped workflow —
    # which is the counterpart this detector exists for.
    qs = raw.get("questions")
    if isinstance(qs, dict):
        out["questions"] = {
            k: (path(v) if k == "candidate_from" else data(v)) for k, v in qs.items()
        }

    for k, v in raw.items():
        # The blocks handled above own their own rewriting; anything else
        # is opaque data. `questions` joined this list when it gained a
        # context path (G12 C1) — omitting it here re-copied the raw
        # block over the rewritten one, which is the same shape of bug
        # the dangling-reference detector had just caught.
        if k not in ("steps", "edges", "learned_memory", "questions"):
            out[k] = data(v)
    return out
