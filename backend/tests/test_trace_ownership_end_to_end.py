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
from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine.executor import ToolCatalog, WorkflowEngine
from workflow_platform.engine.registry import FunctionRegistry
from workflow_platform.persistence import in_memory_repositories
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
