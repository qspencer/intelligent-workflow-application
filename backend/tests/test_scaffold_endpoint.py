"""Tests for the NL scaffold + POST /api/workflows/scaffold (C7.1)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.catalog import build_catalog
from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.scaffold import ScaffoldError, build_system_prompt, extract_json
from workflow_platform.tools import FileReadTool, FileWriteTool
from workflow_platform.world import mock_world

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_H = {"X-Dev-User": "a", "X-Dev-Groups": "admins"}

_GOOD_WORKFLOW = {
    "name": "Triage dropped PDFs",
    "description": "Extract and summarize PDFs dropped in a folder.",
    "trigger": {"type": "filesystem", "config": {"path": "/tmp/in"}},
    "steps": [
        {"id": "extract", "type": "deterministic", "function": "pdf_extract", "config": {}},
        {
            "id": "summarize",
            "type": "agentic",
            "goal": "Summarize",
            "model": MODEL,
            "tools": ["file_read"],
        },
    ],
    "edges": [{"from": "extract", "to": "summarize"}],
}


# --- pure helpers ---


def test_build_system_prompt_inlines_catalog() -> None:
    catalog = build_catalog(default_function_registry(), ToolCatalog([FileReadTool()]))
    prompt = build_system_prompt(catalog)
    assert "pdf_extract" in prompt
    assert "file_read" in prompt
    assert "filesystem" in prompt  # a trigger type


def test_extract_json_strips_code_fences() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_handles_surrounding_prose() -> None:
    assert extract_json('Sure! Here:\n{"a": 1}\nHope that helps.') == {"a": 1}


def test_extract_json_rejects_non_json() -> None:
    with pytest.raises(ScaffoldError):
        extract_json("no json here")


# --- endpoint ---


def _engine(repos: object, response_text: str) -> WorkflowEngine:
    return WorkflowEngine(
        repositories=repos,  # type: ignore[arg-type]
        functions=default_function_registry(),
        tools=ToolCatalog([FileReadTool(), FileWriteTool()]),
        bedrock=FakeBedrock([text_response(response_text)]),
        world=mock_world(),
    )


def _client(repos: object, engine: WorkflowEngine) -> TestClient:
    return TestClient(create_app(repositories=repos, engine=engine))  # type: ignore[arg-type]


def test_scaffold_creates_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    client = _client(repos, _engine(repos, json.dumps(_GOOD_WORKFLOW)))

    r = client.post("/api/workflows/scaffold", json={"description": "triage pdfs"}, headers=_H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "created"
    wf_id = body["workflow_id"]

    # Persisted + fetchable, with the scaffolded steps.
    fetched = client.get(f"/api/workflows/{wf_id}", headers=_H).json()
    # R5 F4: step ids are PLATFORM-MINTED — the model names its own steps, and
    # step ids are published as dict KEYS in the projected `context.steps`, so a
    # model-chosen id would be model-derived content on a platform-keyed path.
    #
    # R6 F1: assert the ID SET, not the absence of a substring. The previous
    # version required "extract" to vanish from the whole serialized definition,
    # which the reviewer noted REWARDED the substring-rewrite defect — it also
    # required the function `pdf_extract` to be mangled. The property is about
    # identifiers, so it is asserted on identifiers.
    assert {s["id"] for s in fetched["steps"]} == {"step_1", "step_2"}
    assert not ({"extract", "summarize"} & {s["id"] for s in fetched["steps"]}), (
        "a model-chosen step id survived as an identifier"
    )


def test_scaffold_requires_description(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    r = _client(repos, _engine(repos, "{}")).post("/api/workflows/scaffold", json={}, headers=_H)
    assert r.status_code == 400


def test_scaffold_non_json_output_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    r = _client(repos, _engine(repos, "I cannot help with that.")).post(
        "/api/workflows/scaffold", json={"description": "x"}, headers=_H
    )
    assert r.status_code == 422


def test_scaffold_malformed_definition_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    # Agentic step missing the required `model` field.
    bad = {"name": "x", "steps": [{"id": "a", "type": "agentic", "goal": "g"}], "edges": []}
    r = _client(repos, _engine(repos, json.dumps(bad))).post(
        "/api/workflows/scaffold", json={"description": "x"}, headers=_H
    )
    assert r.status_code == 422


def test_scaffold_role_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    r = _client(repos, _engine(repos, json.dumps(_GOOD_WORKFLOW))).post(
        "/api/workflows/scaffold",
        json={"description": "x"},
        headers={"X-Dev-User": "v", "X-Dev-Groups": "org-viewers"},
    )
    assert r.status_code == 403


def test_extract_json_tolerates_trailing_prose() -> None:
    """The Haiku eval run's dominant L1 failure: valid JSON followed by
    explanation text ('Extra data'). raw_decode scanning takes the first
    complete object."""
    out = extract_json('{"name": "wf", "steps": []}\n\nThis workflow watches a folder and...')
    assert out == {"name": "wf", "steps": []}


def test_extract_json_tolerates_braces_in_leading_prose() -> None:
    out = extract_json('Here is {my} answer: {"name": "wf"} hope that helps')
    assert out == {"name": "wf"}


# --- R6 F1: minting rewrites REFERENCES, and nothing else --------------------


def test_minting_rewrites_references_and_preserves_everything_else() -> None:
    """R6 F1. The first version substring-replaced every string in the draft:
    the function `pdf_extract` became `pdf_step_1` (structurally valid, so it
    persisted and failed at run time), a path `/inbox/extract/` was rewritten,
    and a condition's comparison LITERAL changed with its step reference.
    Whole-word matching would still have broken the literal."""
    from workflow_platform.scaffold import mint_platform_step_ids

    draft = {
        "steps": [
            {
                "id": "extract",
                "type": "deterministic",
                "function": "pdf_extract",
                "config": {"path": "/inbox/extract/file.pdf"},
            },
            {
                "id": "route",
                "type": "agentic",
                "inputs": ["steps.extract.output_text"],
                "goal": "read {steps.extract.output_text}",
            },
        ],
        "edges": [
            {
                "from": "extract",
                "to": "route",
                "condition": "steps['extract']['document_type'] == 'extract'",
            }
        ],
    }
    out = mint_platform_step_ids(json.loads(json.dumps(draft)))

    # references move …
    assert [s["id"] for s in out["steps"]] == ["step_1", "step_2"]
    # R9 P1: `inputs` holds CONTEXT PATHS, not bare step ids
    assert out["steps"][1]["inputs"] == ["steps.step_1.output_text"]
    assert out["edges"][0]["from"] == "step_1" and out["edges"][0]["to"] == "step_2"
    assert "steps['step_1']" in out["edges"][0]["condition"]
    assert out["steps"][1]["goal"] == "read {steps.step_1.output_text}"

    # … and nothing else does
    assert out["steps"][0]["function"] == "pdf_extract", "a FUNCTION NAME was rewritten"
    assert out["steps"][0]["config"]["path"] == "/inbox/extract/file.pdf", "a PATH was rewritten"
    assert out["edges"][0]["condition"].endswith("== 'extract'"), (
        "a comparison LITERAL was rewritten"
    )


def test_minting_rejects_duplicate_ids_before_they_are_minted_apart() -> None:
    """R6 F1: duplicates were silently minted into distinct ids, destroying the
    very collision definition validation exists to reject."""
    import pytest as _pytest

    from workflow_platform.scaffold import ScaffoldIdError, mint_platform_step_ids

    with _pytest.raises(ScaffoldIdError):
        mint_platform_step_ids({"steps": [{"id": "a"}, {"id": "a"}]})


def test_minting_touches_reference_positions_only_not_ordinary_data() -> None:
    """R7 P1: the walker treated ANY nested `from`/`to`/`inputs` as a reference,
    so a deterministic function's ordinary config was rewritten as if it named
    steps. Reference positions are identified by PATH now."""
    from workflow_platform.scaffold import mint_platform_step_ids

    draft = {
        "steps": [
            {
                "id": "a",
                "function": "copy_files",
                "config": {"from": "a", "to": "/archive/a", "inputs": ["a"]},
            },
            {"id": "b", "inputs": ["steps.a.value"]},
        ]
    }
    out = mint_platform_step_ids(json.loads(json.dumps(draft)))
    assert out["steps"][0]["config"] == {
        "from": "a",
        "to": "/archive/a",
        "inputs": ["a"],
    }, "a function's ordinary CONFIG was rewritten as if it named steps"
    # R9 P1: `inputs` holds CONTEXT PATHS, not bare step ids
    assert out["steps"][1]["inputs"] == ["steps.step_1.value"], "a real reference must still move"


def test_minting_rewrites_a_reference_but_not_a_quoted_literal() -> None:
    """R7 P1: reference-shaped text inside a quoted comparison literal was
    rewritten with the reference, flipping a condition's truth value while the
    step data was unchanged. Conditions are parsed now, so a string Constant is
    never mistaken for a reference."""
    from workflow_platform.scaffold import mint_platform_step_ids

    draft = {
        "steps": [{"id": "classify"}, {"id": "route"}],
        "edges": [
            {
                "from": "classify",
                "to": "route",
                "condition": "steps['classify']['label'] == \"steps['classify']\"",
            }
        ],
    }
    cond = mint_platform_step_ids(json.loads(json.dumps(draft)))["edges"][0]["condition"]
    assert "steps['step_1']['label']" in cond, "the reference must move"
    assert "\"steps['classify']\"" in cond, "the quoted LITERAL must not"


def test_the_scaffolded_workflow_id_is_minted_not_derived_from_model_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R7 §4.4 (CONFIG ownership): the id was `slugify(<the model's proposed
    name>)`, and `workflow_id` is published to ordinary readers as CONFIG —
    operator-approved content. Slugging preserves whatever the model wrote, so
    a model-authored string sat in a trusted position in every trace of every
    run of that workflow.

    The model's NAME still lives on the definition for display; no trace kind
    declares a workflow name, so none of it reaches a trace."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    hostile = "exfiltrate sk_live_51H8xQ2"
    drafted = {**_GOOD_WORKFLOW, "name": hostile}
    repos = in_memory_repositories()
    client = _client(repos, _engine(repos, json.dumps(drafted)))

    body = client.post("/api/workflows/scaffold", json={"description": "x"}, headers=_H).json()
    wf_id = body["workflow_id"]

    assert wf_id.startswith("wf-"), f"workflow id is not platform-minted: {wf_id}"
    assert "exfiltrate" not in wf_id and "sk_live" not in wf_id.lower(), (
        f"model-authored text survived into the workflow id: {wf_id}"
    )
    # …and the model's name is still there for display, on the definition
    fetched = client.get(f"/api/workflows/{wf_id}", headers=_H).json()
    assert fetched["name"] == hostile


def test_minting_every_real_shipped_definition_leaves_no_dangling_reference() -> None:
    """The round-trip probe, as a test (R9).

    Unit tests on hand-built drafts passed through three rounds while real
    definitions still broke. This mints EVERY shipped example and asserts two
    things: the result still loads, and no reference anywhere still names a
    pre-rename id. It is what found the edge-alias bug (`Edge` stores
    `source`/`target`, so minting silently no-opped on a dumped definition)
    and the delimited goal references."""
    import re
    from pathlib import Path

    from workflow_platform.scaffold import mint_platform_step_ids
    from workflow_platform.workflow import load_definition_from_yaml

    examples = sorted((Path(__file__).resolve().parents[2] / "examples").glob("*/workflow.yaml"))
    assert examples, "no example definitions found — the probe would pass vacuously"

    problems: list[str] = []
    for wf in examples:
        original = load_definition_from_yaml(wf.read_text()).model_dump(
            mode="json", exclude_none=True
        )
        minted = mint_platform_step_ids(json.loads(json.dumps(original)))
        blob = json.dumps(minted)
        for old_id in [s["id"] for s in original.get("steps", []) if isinstance(s.get("id"), str)]:
            # anchored, so `prior_steps.<id>` in prose is not a false positive
            if re.search(rf"(?<![A-Za-z_])steps\.{re.escape(old_id)}\b", blob):
                problems.append(f"{wf.parent.name}: dangling reference to {old_id!r}")
        try:
            load_definition_from_yaml(json.dumps(minted))
        except Exception as exc:
            problems.append(f"{wf.parent.name}: minted form no longer loads — {exc}")

    assert not problems, "minting broke real definitions:\n  " + "\n  ".join(problems)


#: How minting must treat each field of each definition model. R10 P2: the
#: previous "schema-derived" check built a HANDWRITTEN dict and enumerated
#: nothing, so adding a reference field to `AgenticStep` did not fail it and
#: the claim that a new field is caught was unsupported. This is the real
#: inventory, and `test_every_definition_field_is_classified` fails the build
#: on any field not listed.
#:
#:   ref_id    — holds a bare step id
#:   ref_path  — holds a `steps.<id>.<field>` context path
#:   template  — the engine renders `{…}` placeholders in it
#:   prose     — agent-facing text; DELIMITED references rewritten, prose not
#:   data      — never rewritten
FIELD_CLASSIFICATION: dict[str, dict[str, str]] = {
    "WorkflowDefinition": {
        "id": "data",
        "name": "data",
        "description": "data",
        "trigger": "data",
        "steps": "data",
        "edges": "data",
        "policies": "data",
        "capabilities": "data",
        "learned_memory": "data",
        "questions": "data",
    },
    "DeterministicStep": {
        "id": "ref_id",
        "type": "data",
        "function": "data",
        "config": "data",
        "outputs": "data",
        "capabilities": "data",
        "runtime": "data",
        "label": "data",
        "output_renderer": "data",
    },
    "AgenticStep": {
        "id": "ref_id",
        "type": "data",
        "goal": "prose",
        "tools": "data",
        "model": "data",
        "system_prompt": "prose",
        "inputs": "ref_path",
        "pin_params": "ref_path",
        "require_tool_call": "data",
        "policy": "data",
        "outputs": "data",
        "capabilities": "data",
        "runtime": "data",
        "label": "data",
        "output_renderer": "data",
    },
    "Edge": {
        "source": "ref_id",
        "target": "ref_id",
        "condition": "expression",
        "condition_label": "data",
        "on_error": "data",
    },
    # G12 C1. `candidate_from` is a context PATH like `recall.query_from`;
    # subject/recipient are literals in C1 and become the validated
    # platform-identity binding in C2 (ASK_THE_USER_PLAN §1b). The
    # catalog is operator-authored data whose own templates are checked
    # at load — a `{trigger.*}` placeholder in a prompt or a stored
    # assertion is refused there, not here.
    "QuestionSpec": {
        "candidate_from": "ref_path",
        "subject": "data",
        "recipient": "data",
        "catalog": "data",
    },
    "LearnedMemorySpec": {
        "user_id": "data",
        "source_id": "data",
        "observations": "data",
        "recall": "data",
    },
    "ObservationSpec": {
        "text": "template",
        "author": "data",
        "derived_from": "data",
        "event_type": "data",
        "date_from": "ref_path",
        "ref_from": "ref_path",
    },
    "RecallSpec": {"query_from": "ref_path", "token_budget": "data"},
    "TriggerSpec": {"type": "data", "config": "data", "example_payload": "data"},
    "RequireToolCall": {"name": "data", "min_success": "data"},
    "AgenticStepPolicy": {
        "max_iterations": "data",
        "max_total_tokens": "data",
        "inference_config": "data",
    },
    "StepRuntimePolicy": {"retries": "data", "timeout_seconds": "data"},
    "WorkflowPolicy": {
        "max_total_tokens": "data",
        "timeout_seconds": "data",
        "budget_action": "data",
    },
}


def _definition_models() -> dict[str, type]:
    """Every Pydantic model the definition schema is built from."""
    import inspect

    from pydantic import BaseModel

    from workflow_platform.workflow import definition as defn

    return {
        name: obj
        for name, obj in vars(defn).items()
        if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj.__module__ == defn.__name__
    }


def _classification_problems(models: dict[str, type]) -> list[str]:
    """The gate's logic, as a pure function so the CONTROL can exercise it."""
    problems: list[str] = []
    for name, model in models.items():
        declared = set(FIELD_CLASSIFICATION.get(name, {}))
        actual = set(model.model_fields)  # type: ignore[attr-defined]
        problems += [
            f"{name}.{f} is not classified (ref_id/ref_path/template/prose/expression/data)"
            for f in sorted(actual - declared)
        ]
        problems += [
            f"{name}.{f} is classified but no longer exists" for f in sorted(declared - actual)
        ]
    problems += [
        f"{n} is classified but is not a definition model"
        for n in sorted(set(FIELD_CLASSIFICATION) - set(models))
    ]
    return problems


def test_every_definition_field_is_classified() -> None:
    """R10 P2: ENUMERATE THE MODELS.

    The previous version of this gate built a handwritten dict and enumerated
    nothing, so a reference field added to `AgenticStep` did not fail it — the
    reviewer demonstrated exactly that. This reads `model_fields` off every
    definition model, and an unclassified field fails the build."""
    problems = _classification_problems(_definition_models())
    assert not problems, (
        "definition schema and its classification have diverged:\n  " + "\n  ".join(problems)
    )


def test_the_classification_gate_fails_when_a_field_is_added() -> None:
    """THE CONTROL the reviewer asked for.

    A gate never shown to fire is what made the previous one worthless. This
    adds a field to a real definition model and asserts the gate FAILS."""
    from pydantic import create_model

    from workflow_platform.workflow import definition as defn

    augmented = create_model(  # a real AgenticStep plus one unclassified field
        "AgenticStep",
        __base__=defn.AgenticStep,
        summary_from=(str | None, None),
    )
    models = {**_definition_models(), "AgenticStep": augmented}

    problems = _classification_problems(models)
    assert any("AgenticStep.summary_from is not classified" in p for p in problems), (
        "the gate did NOT fire on a newly added schema field — it is not "
        f"reading the models. problems={problems}"
    )
    # …and it is quiet on the real schema, so the control proves detection,
    # not a permanently-red gate.
    assert not _classification_problems(_definition_models())


def test_minting_is_correct_when_a_draft_already_contains_a_minted_id() -> None:
    """A model may name a step `step_1` itself. Minting maps from the ORIGINAL
    ids in one pass, so the collision cannot double-apply: `b` becomes step_1
    while the original `step_1` becomes step_2, and a reference to the
    original resolves to step_2."""
    from workflow_platform.scaffold import mint_platform_step_ids

    draft = {
        "steps": [{"id": "b", "function": "noop"}, {"id": "step_1", "function": "noop"}],
        "edges": [{"from": "b", "to": "step_1"}],
        "learned_memory": {"user_id": "u", "recall": {"query_from": "steps.step_1.value"}},
    }
    out = mint_platform_step_ids(json.loads(json.dumps(draft)))
    assert [s["id"] for s in out["steps"]] == ["step_1", "step_2"]
    assert out["edges"][0] == {"from": "step_1", "to": "step_2"}
    assert out["learned_memory"]["recall"]["query_from"] == "steps.step_2.value", (
        "a reference to the ORIGINAL step_1 must follow it to step_2"
    )
