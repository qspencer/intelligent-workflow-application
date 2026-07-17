"""L1/L2 scaffold eval runner (docs/product/LLM_EVAL_FRAMEWORK.md).

Scores a model's ability to turn natural-language descriptions into valid
workflow definitions. This module implements the two automated layers:

- **L1 — structural validity**: the output parses as a `WorkflowDefinition`
  and `validate_definition` reports zero errors.
- **L2 — structural correctness**: constraint satisfaction against each test
  case's `expected` block.

Design amendments over the framework doc as written:

- **Constraints, not exact matching.** Many valid workflow shapes satisfy one
  description ("file_write *or file_move equivalent*"), so L2 criteria are
  tolerant predicates — allowed step-type sets, containment, subsequence —
  never exact structural equality.
- **`unsatisfiable`, not silent failure.** A criterion that names a catalog
  capability we haven't built (e.g. a Slack connector) can't be met by any
  model reading our catalog. Those criteria are excluded from denominators
  and reported separately, so scores measure the model, not the catalog.
- **Catalog versioning.** The scaffold model's output depends on the catalog
  it was shown; every report records `catalog_hash` so scores are only ever
  compared like-for-like.
- **Judge criteria are deferred, explicitly.** Free-text expectations
  (`must_not`, `acceptable_approaches`, ...) are marked `judge` — they belong
  to L3/L4, which (per the amendment) must be calibrated against a
  human-labeled subset before its scores are trusted.

Test cases live as ```yaml fences in `docs/product/LLM_EVAL_TEST_SUITE.md` —
that file stays the single source of truth; this module parses it directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from workflow_platform.bedrock import BedrockClient
from workflow_platform.catalog import WorkflowCatalog
from workflow_platform.scaffold import ScaffoldError, build_system_prompt, scaffold_workflow
from workflow_platform.templates import slugify
from workflow_platform.workflow import (
    AgenticStep,
    DeterministicStep,
    WorkflowDefinition,
    load_definition,
    validate_and_order,
    validate_definition,
)

# Trigger types that name the same event (docs: trigger taxonomy).
_TRIGGER_ALIASES: dict[str, set[str]] = {
    "email": {"email", "gmail_poll"},
    "gmail_poll": {"email", "gmail_poll"},
    "filesystem": {"filesystem", "file_watch"},
    "file_watch": {"filesystem", "file_watch"},
}

# Expected-block fields the automated layers cannot judge — they need L3/L4.
_JUDGE_CRITERIA = {
    "acceptable_approaches",
    "must_not",
    "should_include",
    "connectors_used_min",
    "intent_rubric",
}

CriterionStatus = Literal["pass", "fail", "unsatisfiable", "judge"]


class EvalCase(BaseModel):
    id: str
    category: str
    input: str
    expected: dict[str, Any] = Field(default_factory=dict)
    intent_rubric: str = ""


class CriterionResult(BaseModel):
    name: str
    status: CriterionStatus
    detail: str = ""


class CaseResult(BaseModel):
    case_id: str
    category: str
    l1_pass: bool
    l1_error: str | None = None
    criteria: list[CriterionResult] = Field(default_factory=list)

    @property
    def l2_scoreable(self) -> list[CriterionResult]:
        return [c for c in self.criteria if c.status in ("pass", "fail")]

    @property
    def l2_passed(self) -> int:
        return sum(1 for c in self.l2_scoreable if c.status == "pass")


def load_cases(md_path: str | Path) -> list[EvalCase]:
    """Parse the eval cases out of the test-suite Markdown's ```yaml fences."""
    text = Path(md_path).read_text()
    cases: list[EvalCase] = []
    for block in re.findall(r"```yaml\n(.*?)```", text, re.DOTALL):
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and {"id", "input", "expected"} <= data.keys():
            cases.append(EvalCase.model_validate(data))
    return cases


def catalog_hash(catalog: WorkflowCatalog) -> str:
    """Stable identity of the catalog the model was shown. Scores are only
    comparable between runs with the same hash."""
    canonical = json.dumps(catalog.model_dump(mode="json"), sort_keys=True)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _catalog_names(catalog: WorkflowCatalog) -> tuple[set[str], set[str], set[str]]:
    triggers = {t.type for t in catalog.triggers}
    functions = {f.name for f in catalog.functions}
    tools = {t.name for t in catalog.tools}
    return triggers, functions, tools


def _max_out_degree(defn: WorkflowDefinition) -> int:
    counts = Counter(e.source for e in defn.edges)
    return max(counts.values(), default=0)


def _type_sequence(defn: WorkflowDefinition) -> list[str]:
    by_id = {s.id: s for s in defn.steps}
    try:
        order = validate_and_order(defn)
    except Exception:
        order = [s.id for s in defn.steps]
    return [by_id[sid].type for sid in order if sid in by_id]


def _contains_subsequence(haystack: list[str], needle: list[str]) -> bool:
    it = iter(haystack)
    return all(any(x == want for x in it) for want in needle)


def _normalize_scaffold_output(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply the same post-scaffold normalization the production endpoint does
    (api/workflows.py `scaffold_workflow_endpoint`): the model is never asked
    to invent an id, and name/description/trigger get defaults. L1 must score
    what a user would actually receive, not the raw model output."""
    raw = dict(raw)
    name = raw.get("name") if isinstance(raw.get("name"), str) and raw.get("name") else None
    new_name = name or "Scaffolded workflow"
    raw["name"] = new_name
    raw.setdefault("id", slugify(new_name))
    raw.setdefault("description", "")
    raw.setdefault("trigger", {"type": "manual", "config": {}})
    return raw


def check_case(
    raw: dict[str, Any],
    expected: dict[str, Any],
    catalog: WorkflowCatalog,
) -> tuple[bool, str | None, list[CriterionResult]]:
    """Score one scaffold output: L1 (parse + validate) then L2 constraints.

    Returns (l1_pass, l1_error, criterion_results). When L1 fails, L2 is not
    scored — a definition that doesn't parse satisfies nothing.
    """
    try:
        defn = load_definition(_normalize_scaffold_output(raw))
    except (ValidationError, ValueError) as exc:
        return False, f"does not parse: {str(exc)[:200]}", []
    errors = [f for f in validate_definition(defn) if f.level == "error"]
    if errors:
        return False, "; ".join(f"{f.code}: {f.message}" for f in errors)[:300], []

    _, functions_avail, tools_avail = _catalog_names(catalog)
    agentic = [s for s in defn.steps if isinstance(s, AgenticStep)]
    deterministic = [s for s in defn.steps if isinstance(s, DeterministicStep)]
    functions_used = {s.function for s in deterministic}
    tools_used = {t for s in agentic for t in s.tools}
    defn_dump = json.dumps(defn.model_dump(mode="json"), default=str).lower()
    results: list[CriterionResult] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        results.append(CriterionResult(name=name, status="pass" if ok else "fail", detail=detail))

    for name, want in expected.items():
        if name in _JUDGE_CRITERIA:
            results.append(CriterionResult(name=name, status="judge", detail="L3/L4 territory"))
            continue
        if name == "trigger_type":
            allowed = _TRIGGER_ALIASES.get(str(want), {str(want)})
            add(name, defn.trigger.type in allowed, f"got {defn.trigger.type!r}")
        elif name == "trigger_config_contains":
            config_dump = json.dumps(defn.trigger.config, default=str).lower()
            missing = [s for s in want if str(s).lower() not in config_dump]
            add(name, not missing, f"missing {missing!r}" if missing else "")
        elif name == "min_steps":
            add(name, len(defn.steps) >= int(want), f"got {len(defn.steps)}")
        elif name == "max_steps":
            add(name, len(defn.steps) <= int(want), f"got {len(defn.steps)}")
        elif name == "step_types":
            # Allowed-set semantics: every step's type must be in the list.
            outside = {s.type for s in defn.steps} - set(want)
            add(name, not outside, f"unexpected types {sorted(outside)!r}" if outside else "")
        elif name == "no_agentic_steps":
            add(name, (not agentic) == bool(want), f"{len(agentic)} agentic step(s)")
        elif name == "must_have_agentic":
            add(name, bool(agentic) == bool(want), f"{len(agentic)} agentic step(s)")
        elif name == "should_have_multiple_agentic":
            add(name, (len(agentic) >= 2) == bool(want), f"{len(agentic)} agentic step(s)")
        elif name == "has_conditional":
            got = any(e.condition for e in defn.edges)
            add(name, got == bool(want), f"conditional edges: {got}")
        elif name == "has_parallel":
            got = _max_out_degree(defn) >= 2
            add(name, got == bool(want), f"max out-degree {_max_out_degree(defn)}")
        elif name == "min_branches":
            add(name, _max_out_degree(defn) >= int(want), f"max out-degree {_max_out_degree(defn)}")
        elif name == "step_sequence_contains":
            ok = _contains_subsequence(_type_sequence(defn), [str(w) for w in want])
            add(name, ok, f"sequence {_type_sequence(defn)!r}")
        elif name == "functions_used":
            for fn in want:
                if fn not in functions_avail:
                    results.append(
                        CriterionResult(
                            name=f"{name}:{fn}",
                            status="unsatisfiable",
                            detail="not in catalog",
                        )
                    )
                else:
                    add(f"{name}:{fn}", fn in functions_used, "")
        elif name == "tools_used":
            for tool in want:
                if tool not in tools_avail:
                    results.append(
                        CriterionResult(
                            name=f"{name}:{tool}",
                            status="unsatisfiable",
                            detail="not in catalog",
                        )
                    )
                else:
                    add(f"{name}:{tool}", tool in tools_used, "")
        elif name == "connectors_used":
            # Tolerant containment: the suite's own comments allow equivalents
            # ("slack — or generic HTTP to Slack webhook"), so we accept the
            # connector name appearing anywhere in the definition (a webhook
            # URL, a step label, an agent goal).
            missing = [c for c in want if str(c).lower() not in defn_dump]
            add(name, not missing, f"missing {missing!r}" if missing else "")
        elif name == "tools_or_connectors":
            names = [str(w).lower() for w in want]
            ok = any(n in defn_dump for n in names)
            add(name, ok, "" if ok else f"none of {names!r} present")
        else:
            results.append(
                CriterionResult(name=name, status="judge", detail="unknown criterion; deferred")
            )
    return True, None, results


async def evaluate_model(
    bedrock: BedrockClient,
    *,
    model: str,
    cases: list[EvalCase],
    catalog: WorkflowCatalog,
    concurrency: int = 4,
) -> dict[str, Any]:
    """Run the L1/L2 eval for one model. Returns the report dict (aggregates +
    per-case results + catalog hash)."""
    sem = asyncio.Semaphore(concurrency)

    async def _run_one(case: EvalCase) -> CaseResult:
        async with sem:
            try:
                raw = await scaffold_workflow(
                    bedrock, model=model, description=case.input, catalog=catalog
                )
            except (ScaffoldError, Exception) as exc:
                return CaseResult(
                    case_id=case.id,
                    category=case.category,
                    l1_pass=False,
                    l1_error=f"scaffold call failed: {str(exc)[:200]}",
                )
        l1_pass, l1_error, criteria = check_case(raw, case.expected, catalog)
        return CaseResult(
            case_id=case.id,
            category=case.category,
            l1_pass=l1_pass,
            l1_error=l1_error,
            criteria=criteria,
        )

    results = await asyncio.gather(*(_run_one(c) for c in cases))

    l1_passes = sum(1 for r in results if r.l1_pass)
    scoreable = [c for r in results for c in r.l2_scoreable]
    passed = sum(1 for c in scoreable if c.status == "pass")
    unsatisfiable = [
        (r.case_id, c.name) for r in results for c in r.criteria if c.status == "unsatisfiable"
    ]
    judge_deferred = sum(1 for r in results for c in r.criteria if c.status == "judge")
    prompt_hash = "sha256:" + hashlib.sha256(build_system_prompt(catalog).encode()).hexdigest()[:16]
    return {
        "model": model,
        "catalog_hash": catalog_hash(catalog),
        # The system prompt shapes scores as much as the model does; compare
        # runs like-for-like on (model, catalog_hash, prompt_hash).
        "prompt_hash": prompt_hash,
        "cases": len(results),
        "l1_pass": l1_passes,
        "l1_rate": round(l1_passes / len(results), 4) if results else None,
        "l2_criteria_scored": len(scoreable),
        "l2_criteria_passed": passed,
        "l2_rate": round(passed / len(scoreable), 4) if scoreable else None,
        "unsatisfiable": unsatisfiable,
        "judge_deferred": judge_deferred,
        "results": [r.model_dump() for r in results],
    }
