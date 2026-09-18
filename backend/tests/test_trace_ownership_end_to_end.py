"""End-to-end coverage of the round-7 findings (the reviewer's closing ask).

The round-7 defects were all found by RUNNING a workflow with safe-only
storage on and reading what actually landed — not by calling the projector.
Projector-level tests would have passed while #1 was live, because the hole
was that ownership applied at the entry point only: the stored step row was
correct and the stored instance context, written by the same run, was not.

So these drive the real engine and assert on PERSISTED state: the step row,
the instance context, the audit log and the vault.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine.executor import ToolCatalog, WorkflowEngine
from workflow_platform.engine.registry import FunctionRegistry
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.persistence.models import StepExecution
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

pytestmark = pytest.mark.asyncio

SCORES = {"faithfulness_score": 5, "category_score": 4, "relevance_score": 3}


async def _scoring_fn(config: Any, ctx: Any, world: Any) -> dict[str, Any]:
    """Stands in for `record_evaluation`: emits model-derived scores."""
    return {**SCORES, "needs_tests": True, "concern_count": 2, "summary": "SYNTHETIC text"}


async def _forging_fn(config: Any, ctx: Any, world: Any) -> dict[str, Any]:
    """Stands in for the registered `noop`: returns its config unchanged."""
    return dict(config)


def _engine(fns: dict[str, Any]) -> WorkflowEngine:
    registry = FunctionRegistry()
    for name, fn in fns.items():
        registry.register(name, fn)
    return WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=registry,
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        trace_safe_only=True,
    )


def _definition(function: str, config: dict[str, Any] | None = None) -> Any:
    return load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "s1", "type": "deterministic", "function": function, "config": config or {}}
            ],
            "edges": [],
        }
    )


async def test_business_fields_are_withheld_in_BOTH_the_step_row_and_the_instance_context() -> None:
    """R7 #1, end to end — the reviewer's exact reproduction.

    Scores were withheld from the stored step output and RETAINED in the same
    run's instance context, because withholding ran at the projection entry
    point rather than at the schema node. One run, both surfaces, one
    assertion."""
    engine = _engine({"score": _scoring_fn})
    instance = await engine.run(_definition("score"), trigger_payload={})

    steps = await engine.repositories.steps.list_by_instance(instance.id)
    persisted = await engine.repositories.instances.get(instance.id)
    step_blob = json.dumps([s.output for s in steps])
    ctx_blob = json.dumps(persisted.context if persisted else {})

    for field in SCORES:
        assert field not in step_blob, f"{field} survived in the stored step row"
        assert field not in ctx_blob, f"{field} survived in the stored instance CONTEXT"
    # and the withheld signal is present on both, so neither reads as complete
    assert "_withheld_keys" in step_blob and "_withheld_keys" in ctx_blob


async def test_a_function_cannot_forge_engine_or_projection_metadata_in_a_real_run() -> None:
    """R7 #2, end to end. A function returning its config unchanged could put
    `projector_version`, `projection_schema_version` and `parse_ok` into the
    stored output, where they read as platform metadata and `output_has_raw()`
    called the row clean."""
    forged = {
        "model": "attacker-supplied",
        "memory_hash": "sha256:deadbeefdeadbeef",
        "projector_version": "supplied_value",
        "projection_schema_version": 12345,
        "parse_ok": False,
        "kept": "ordinary output",
    }
    engine = _engine({"forge": _forging_fn})
    instance = await engine.run(_definition("forge", forged), trigger_payload={})

    steps = await engine.repositories.steps.list_by_instance(instance.id)
    stored = json.dumps([s.output for s in steps])
    for forged_field in ("attacker-supplied", "sha256:deadbeefdeadbeef", "supplied_value", "12345"):
        assert forged_field not in stored, f"forged value {forged_field!r} was persisted: {stored}"
    # `parse_ok` from a NON-parser is a claimed parse that never happened
    assert "parse_ok" not in stored, f"an unearned parse_ok was persisted: {stored}"


async def test_the_boundary_drops_the_forgery_but_still_vaults_the_real_output() -> None:
    """The boundary must not DESTROY the function's own output.

    `copied_to` is undeclared, so it is withheld below grant (R4) — but it must
    still reach the vault, or the ownership fix would have traded a disclosure
    defect for a data-loss one. The forged engine field, by contrast, is gone
    before storage entirely: it was never ours to keep, and the attempt is
    recorded in the log rather than in the trace."""
    engine = _engine({"forge": _forging_fn})
    instance = await engine.run(
        _definition("forge", {"model": "attacker-supplied", "copied_to": ["/out/a.xml"]}),
        trigger_payload={},
    )
    steps = await engine.repositories.steps.list_by_instance(instance.id)
    stored = json.dumps([s.output for s in steps])
    assert "attacker-supplied" not in stored
    assert "_withheld_keys" in stored, "the withheld signal must be present"

    vault = await engine.repositories.raw_trace_vault.list_by_instance(instance.id)
    raw_blob = json.dumps([v.model_dump(mode="json") for v in vault], default=str)
    assert "/out/a.xml" in raw_blob, (
        "the function's OWN output never reached the vault — a grant holder "
        f"could not recover it: {raw_blob[:300]}"
    )
    assert "attacker-supplied" not in raw_blob, (
        "the forged engine field was vaulted; it should not be stored at all"
    )


# --- R9 P1: every reference position ----------------------------------------


async def _emit_fn(config: Any, ctx: Any, world: Any) -> dict[str, Any]:
    return {"value": "SYNTHETIC", "output_text": '{"faithfulness_score": 5}'}


def _agentic_engine() -> WorkflowEngine:
    """An engine whose agent returns one plain text frame."""
    from tests._bedrock_fakes import text_response

    registry = FunctionRegistry()
    registry.register("emit", _emit_fn)
    return WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=registry,
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([text_response("done"), text_response("done")]),
        world=mock_world(),
        trace_safe_only=True,
    )


async def test_an_agent_inputs_reference_survives_renaming() -> None:
    """R9 P1: `inputs` on an agentic step holds CONTEXT PATHS, not step ids —
    minting treated them as ids, left them untouched, and the selected input
    resolved to null. Asserted by running the renamed definition."""
    from workflow_platform.persistence.models import WorkflowInstanceState
    from workflow_platform.scaffold import mint_platform_step_ids

    raw = {
        "id": "wf",
        "name": "wf",
        "trigger": {"type": "manual"},
        "steps": [
            {"id": "prepare", "type": "deterministic", "function": "emit", "config": {}},
            {
                "id": "act",
                "type": "agentic",
                "goal": "use it",
                "model": "claude-haiku-4-5",
                "inputs": ["steps.prepare.value"],
            },
        ],
        "edges": [{"from": "prepare", "to": "act"}],
    }
    before = await _agentic_engine().run(
        load_definition(json.loads(json.dumps(raw))), trigger_payload={}
    )
    assert before.state is WorkflowInstanceState.COMPLETED, "the ORIGINAL must complete"

    minted = mint_platform_step_ids(json.loads(json.dumps(raw)))
    assert minted["steps"][1]["inputs"] == ["steps.step_1.value"], (
        f"agent inputs were not rewritten: {minted['steps'][1]['inputs']}"
    )
    after = await _agentic_engine().run(load_definition(minted), trigger_payload={})
    assert after.state is WorkflowInstanceState.COMPLETED


async def test_a_pin_params_reference_survives_renaming() -> None:
    """R9 P1: `pin_params` maps a tool parameter to a context path and is
    FAIL-CLOSED — an unresolved pin fails the step before dispatch, so a
    dangling one is not a silent degradation."""
    from workflow_platform.scaffold import mint_platform_step_ids

    raw = {
        "id": "wf",
        "name": "wf",
        "trigger": {"type": "manual"},
        "steps": [
            {"id": "prepare", "type": "deterministic", "function": "emit", "config": {}},
            {
                "id": "act",
                "type": "agentic",
                "goal": "g",
                "model": "claude-haiku-4-5",
                "pin_params": {"path": "steps.prepare.value"},
            },
        ],
        "edges": [{"from": "prepare", "to": "act"}],
    }
    minted = mint_platform_step_ids(json.loads(json.dumps(raw)))
    assert minted["steps"][1]["pin_params"] == {"path": "steps.step_1.value"}, (
        f"a fail-closed pin still names the old step: {minted['steps'][1]['pin_params']}"
    )


async def test_a_learned_memory_reference_survives_renaming() -> None:
    """R9 P1: `query_from` / `date_from` / `ref_from` live under the
    workflow's `learned_memory` object — they were looked for in step config,
    which is not where the schema puts them, so the old ids stayed and the
    resolver returned None."""
    from workflow_platform.scaffold import mint_platform_step_ids

    raw = {
        "id": "wf",
        "name": "wf",
        "trigger": {"type": "manual"},
        "steps": [{"id": "triage", "type": "deterministic", "function": "emit", "config": {}}],
        "edges": [],
        "learned_memory": {
            "user_id": "u",
            "source_id": "test:src",
            "recall": {"query_from": "steps.triage.value"},
            "observations": [
                {
                    "text": "saw {steps.triage.value}",
                    "date_from": "steps.triage.when",
                    "ref_from": "steps.triage.id",
                }
            ],
        },
    }
    lm = mint_platform_step_ids(json.loads(json.dumps(raw)))["learned_memory"]
    assert lm["recall"]["query_from"] == "steps.step_1.value"
    assert lm["observations"][0]["date_from"] == "steps.step_1.when"
    assert lm["observations"][0]["ref_from"] == "steps.step_1.id"
    assert lm["observations"][0]["text"] == "saw {steps.step_1.value}"


async def test_an_omitted_config_still_resolves_after_renaming() -> None:
    """R9 P1: with `config` absent entirely the default was never
    materialised, so a stock `record_evaluation` workflow went from completed
    to FAILED. Run, not validated."""
    from workflow_platform.engine.functions import default_function_registry
    from workflow_platform.persistence.models import WorkflowInstanceState
    from workflow_platform.scaffold import mint_platform_step_ids

    stock = default_function_registry().get("record_evaluation")
    assert stock is not None, "record_evaluation must be a registered stock function"

    def engine() -> WorkflowEngine:
        r = FunctionRegistry()
        r.register("emit", _emit_fn)
        r.register("record_evaluation", stock)
        return WorkflowEngine(
            repositories=in_memory_repositories(),
            functions=r,
            tools=ToolCatalog([]),
            bedrock=FakeBedrock([]),
            world=mock_world(),
            trace_safe_only=True,
        )

    raw = {
        "id": "wf",
        "name": "wf",
        "trigger": {"type": "manual"},
        "steps": [
            {"id": "evaluate", "type": "deterministic", "function": "emit", "config": {}},
            {"id": "rec", "type": "deterministic", "function": "record_evaluation"},
        ],
        "edges": [{"from": "evaluate", "to": "rec"}],
    }
    before = await engine().run(load_definition(json.loads(json.dumps(raw))), trigger_payload={})
    assert before.state is WorkflowInstanceState.COMPLETED, "the ORIGINAL must complete"

    minted = mint_platform_step_ids(json.loads(json.dumps(raw)))
    assert minted["steps"][1]["config"]["evaluation_from"] == "steps.step_1.output_text"
    after = await engine().run(load_definition(minted), trigger_payload={})
    assert after.state is WorkflowInstanceState.COMPLETED, "the RENAMED definition failed"


async def test_renaming_does_not_switch_on_behaviour_the_original_lacked() -> None:
    """R9 P2: `record_email_triage` READS `route_from`, but that default lives
    in the helper `_record_codified`. Materialising it onto the caller enabled
    routing the original never had. A function gets only the defaults IT
    defines."""
    from workflow_platform.scaffold import mint_platform_step_ids

    raw = {
        "steps": [
            {"id": "precheck", "type": "deterministic", "function": "noop", "config": {}},
            {"id": "triage", "type": "deterministic", "function": "noop", "config": {}},
            {"id": "rec", "type": "deterministic", "function": "record_email_triage", "config": {}},
        ]
    }
    cfg = mint_platform_step_ids(json.loads(json.dumps(raw)))["steps"][2]["config"]
    assert "route_from" not in cfg, f"routing behaviour was invented: {cfg}"
    assert cfg.get("triage_from") == "steps.step_2.output_text", (
        f"the function's OWN default should still be materialised: {cfg}"
    )


async def test_a_recognised_key_on_another_function_stays_DATA() -> None:
    """R9 P2: rewriting by key NAME rewrote `route_from` even on `noop`, where
    it is ordinary returned data. Reference-ness is per function."""
    from workflow_platform.scaffold import mint_platform_step_ids

    raw = {
        "steps": [
            {
                "id": "precheck",
                "type": "deterministic",
                "function": "noop",
                "config": {"route_from": "steps.precheck.route"},
            }
        ]
    }
    cfg = mint_platform_step_ids(json.loads(json.dumps(raw)))["steps"][0]["config"]
    assert cfg["route_from"] == "steps.precheck.route", f"data was rewritten: {cfg}"


async def test_minting_leaves_ordinary_config_data_alone() -> None:
    """The other half of R8/R9: preserving behaviour must not come from
    rewriting everything. Config that merely LOOKS like a reference — prose,
    a path containing a step name — stays exactly as written, on the same
    function whose real reference IS rewritten."""
    from workflow_platform.scaffold import mint_platform_step_ids

    raw = {
        "steps": [
            {"id": "evaluate", "type": "deterministic", "function": "noop", "config": {}},
            {
                "id": "rec",
                "type": "deterministic",
                "function": "record_evaluation",
                "config": {
                    "evaluation_from": "steps.evaluate.output_text",
                    "note": "steps.evaluate is discussed here",
                    "dest_dir": "/out/steps.evaluate/",
                },
            },
        ]
    }
    cfg = mint_platform_step_ids(json.loads(json.dumps(raw)))["steps"][1]["config"]
    assert cfg["evaluation_from"] == "steps.step_1.output_text", "the reference must move"
    assert cfg["note"] == "steps.evaluate is discussed here", "prose was rewritten"
    assert cfg["dest_dir"] == "/out/steps.evaluate/", "a PATH was rewritten"


# --- R10: EXECUTION EQUIVALENCE before and after minting --------------------


async def _echo_marker_fn(config: Any, ctx: Any, world: Any) -> dict[str, Any]:
    """Returns its configured marker verbatim — so a downstream condition can
    compare against it and the comparison is observable in the run."""
    return {"marker": config.get("marker", "")}


async def test_a_config_literal_that_looks_like_a_template_is_not_rewritten() -> None:
    """R10 P1, by EXECUTION.

    `_rewrite_template` was applied to every string on the reasoning that
    `{...}` is a platform template form. The engine renders placeholders in
    exactly ONE place — `learned_memory.observations[].text` — so everywhere
    else `{steps.a.value}` is DATA. Rewriting it in a `noop` config changed a
    literal a downstream condition compared against: the original ran both
    steps, the renamed one silently SKIPPED the second."""
    from workflow_platform.persistence.models import WorkflowInstanceState
    from workflow_platform.scaffold import mint_platform_step_ids

    raw = {
        "id": "wf",
        "name": "wf",
        "trigger": {"type": "manual"},
        "steps": [
            {
                "id": "a",
                "type": "deterministic",
                "function": "echo_marker",
                "config": {"marker": "{steps.a.value}"},
            },
            {
                "id": "b",
                "type": "deterministic",
                "function": "echo_marker",
                "config": {"marker": "ran"},
            },
        ],
        "edges": [
            {"from": "a", "to": "b", "condition": "steps['a']['marker'] == '{steps.a.value}'"}
        ],
    }

    def engine() -> WorkflowEngine:
        r = FunctionRegistry()
        r.register("echo_marker", _echo_marker_fn)
        return WorkflowEngine(
            repositories=in_memory_repositories(),
            functions=r,
            tools=ToolCatalog([]),
            bedrock=FakeBedrock([]),
            world=mock_world(),
            trace_safe_only=True,
        )

    e1 = engine()  # ONE engine, or the "before" read hits empty repositories
    before = await e1.run(load_definition(json.loads(json.dumps(raw))), trigger_payload={})
    steps_before = await e1.repositories.steps.list_by_instance(before.id)
    assert before.state is WorkflowInstanceState.COMPLETED

    minted = mint_platform_step_ids(json.loads(json.dumps(raw)))
    assert minted["steps"][0]["config"]["marker"] == "{steps.a.value}", (
        "a config literal was rewritten"
    )

    e2 = engine()
    after = await e2.run(load_definition(minted), trigger_payload={})
    assert after.state is WorkflowInstanceState.COMPLETED
    steps_after = await e2.repositories.steps.list_by_instance(after.id)

    _assert_same_steps_ran(steps_before, steps_after, _minted_id_map(raw, minted))


#: R11 P2. The equivalence above compared `len(list_by_instance(...))`, and a
#: SKIPPED step still writes a row — so 2 rows compared equal to 2 rows whether
#: the second step ran or was skipped, which is precisely the failure the test
#: exists to catch. The reviewer proved it by substituting a mint that forced
#: the renamed branch to `False`; the test still passed. Comparing per-step
#: TERMINAL STATE, keyed by (step id, attempt) through the original->minted
#: map, is the claim the name makes. `test_the_equivalence_check_can_fail` is
#: the control that keeps this honest.
def _minted_id_map(raw: dict[str, Any], minted: dict[str, Any]) -> dict[str, str]:
    """original step id -> minted step id. Minting preserves step order."""
    return {o["id"]: m["id"] for o, m in zip(raw["steps"], minted["steps"], strict=True)}


def _states_by_attempt(rows: Sequence[StepExecution]) -> dict[tuple[str, int], str]:
    return {(r.step_id, r.attempt): r.state.value for r in rows}


def _assert_same_steps_ran(
    before_rows: Sequence[StepExecution],
    after_rows: Sequence[StepExecution],
    id_map: dict[str, str],
) -> None:
    """The renamed run reached the same terminal state for the same steps.

    Keyed by (step id, attempt) so a differing RETRY count is a difference too,
    not just a differing set of steps.
    """
    translated = {
        (id_map[sid], attempt): state
        for (sid, attempt), state in _states_by_attempt(before_rows).items()
    }
    actual = _states_by_attempt(after_rows)
    assert actual == translated, (
        "minting changed EXECUTION, not just ids.\n"
        f"  before (ids translated): {translated}\n"
        f"  after:                   {actual}"
    )


async def test_the_equivalence_check_can_fail() -> None:
    """CONTROL for `_assert_same_steps_ran` — required by R11 P2.

    Round 11 showed the previous equivalence assertion passed on a mint that
    deliberately skipped a step. A comparison that cannot fail is not evidence,
    so this drives the sabotage the reviewer used — same config literal, renamed
    branch forced to `False` — and asserts the comparison REPORTS it.
    """
    from workflow_platform.persistence.models import WorkflowInstanceState
    from workflow_platform.scaffold import mint_platform_step_ids

    raw = {
        "id": "wf",
        "name": "wf",
        "trigger": {"type": "manual"},
        "steps": [
            {"id": "a", "type": "deterministic", "function": "echo_marker", "config": {}},
            {"id": "b", "type": "deterministic", "function": "echo_marker", "config": {}},
        ],
        "edges": [{"from": "a", "to": "b", "condition": "True"}],
    }

    def engine() -> WorkflowEngine:
        r = FunctionRegistry()
        r.register("echo_marker", _echo_marker_fn)
        return WorkflowEngine(
            repositories=in_memory_repositories(),
            functions=r,
            tools=ToolCatalog([]),
            bedrock=FakeBedrock([]),
            world=mock_world(),
            trace_safe_only=True,
        )

    e1 = engine()
    before = await e1.run(load_definition(json.loads(json.dumps(raw))), trigger_payload={})
    steps_before = await e1.repositories.steps.list_by_instance(before.id)
    assert before.state is WorkflowInstanceState.COMPLETED
    assert len(steps_before) == 2

    # The sabotage: ids are minted normally, then the renamed branch is
    # switched off. Row COUNT is unchanged (a skipped step still writes a row),
    # which is exactly why counting could not see this.
    sabotaged = mint_platform_step_ids(json.loads(json.dumps(raw)))
    sabotaged["edges"][0]["condition"] = "False"

    e2 = engine()
    after = await e2.run(load_definition(sabotaged), trigger_payload={})
    steps_after = await e2.repositories.steps.list_by_instance(after.id)
    assert len(steps_after) == len(steps_before), (
        "premise of this control: the row COUNT is identical, so only a "
        "state-aware comparison can tell these runs apart"
    )

    with pytest.raises(AssertionError, match="changed EXECUTION"):
        _assert_same_steps_ran(steps_before, steps_after, _minted_id_map(raw, sabotaged))


async def test_a_non_ascii_step_id_reference_survives_renaming() -> None:
    """R10 P1: the rewriter's identifier grammar was NARROWER than execution's.
    The model and `_resolve_context_value` accept any id (the resolver just
    splits on dots), but the rewriter matched `[A-Za-z0-9_-]`, so a valid id
    like `prépare` kept a dangling reference and the consumer failed."""
    from workflow_platform.scaffold import mint_platform_step_ids

    for step_id in ("prépare", "a-b", "步骤"):
        raw = {
            "steps": [
                {"id": step_id, "type": "deterministic", "function": "noop", "config": {}},
                {
                    "id": "r",
                    "type": "deterministic",
                    "function": "record_evaluation",
                    "config": {"evaluation_from": f"steps.{step_id}.output_text"},
                },
            ]
        }
        minted = mint_platform_step_ids(json.loads(json.dumps(raw)))
        assert minted["steps"][1]["config"]["evaluation_from"] == "steps.step_1.output_text", (
            f"a reference to step id {step_id!r} was left dangling"
        )
