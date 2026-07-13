"""Learned per-entity memory (veracium, write-only slice).

Pins the adoption conditions from docs/SEMANTICS.md ("veracium — adopted"):

- The ENGINE writes observations after a successful run; there is no agent
  memory-write tool, and workflows without a `learned_memory` block trigger
  zero memory activity.
- veracium's LLM calls route through the platform BedrockClient (here:
  FakeBedrock — proving record/replay compatibility) and are metered into
  the instance's token/cost totals.
- Every write lands a `memory_observed` audit entry with a content hash and
  provenance counts; third-party claims are quarantined, not stored as facts.
- A failed observation never fails the workflow.
- Dry runs write to an ephemeral scratch DB, never the real store.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.engine import (
    ToolCatalog,
    WorkflowEngine,
    default_function_registry,
)
from workflow_platform.engine.context import WorkflowContext
from workflow_platform.engine.executor import _render_observation_template
from workflow_platform.main import create_app
from workflow_platform.memory import LearnedMemoryService
from workflow_platform.persistence import (
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.world import mock_world

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_ADMIN = {"X-Dev-User": "a", "X-Dev-Groups": "admins"}


def _distill_response(
    *, facts: int = 1, quarantined: int = 0, episode: str = "an event happened"
) -> dict[str, Any]:
    """A converse response shaped like veracium's EXTRACT_SCHEMA output.
    `third_party_claim` is veracium's structural quarantine relation."""
    triples = [
        {"subject": "user", "relation": "likes", "object": f"thing-{i}", "volatility": "durable"}
        for i in range(facts)
    ] + [
        {"subject": f"sender-{i}", "relation": "third_party_claim", "object": "you owe $900"}
        for i in range(quarantined)
    ]
    return text_response(
        json.dumps({"triples": triples, "episode": episode}),
        input_tokens=200,
        output_tokens=50,
    )


def _service(tmp_path: Path, bedrock: FakeBedrock) -> LearnedMemoryService:
    return LearnedMemoryService(bedrock, tmp_path / "learned.db", model_id=MODEL)


def _definition(learned_memory: dict[str, Any] | None = None) -> dict[str, Any]:
    wf: dict[str, Any] = {
        "id": "lm-wf",
        "name": "lm-wf",
        "trigger": {"type": "manual"},
        "steps": [{"id": "s1", "type": "deterministic", "function": "noop"}],
        "edges": [],
    }
    if learned_memory is not None:
        wf["learned_memory"] = learned_memory
    return wf


def _engine(
    repos: Any, bedrock: FakeBedrock, service: LearnedMemoryService | None
) -> WorkflowEngine:
    return WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog([]),
        bedrock=bedrock,
        world=mock_world(),
        learned_memory=service,
    )


# --- LearnedMemoryService ---


def test_observe_writes_episode_and_meters_usage(tmp_path: Path) -> None:
    bedrock = FakeBedrock([_distill_response(facts=1, quarantined=1)])
    service = _service(tmp_path, bedrock)

    result = asyncio.run(
        service.observe(
            "alice@example.com",
            "From billing@x: you owe $900.",
            author="third_party",
            event_type="email",
            date="2026-07-11",
            evidence_ref="msg-123",
        )
    )
    service.close()

    assert result.facts == 1
    assert result.quarantined == 1
    assert result.text_hash.startswith("sha256:")
    assert result.evidence_ref == "msg-123"
    assert result.input_tokens == 200
    assert result.output_tokens == 50
    assert result.cost_usd > 0  # Haiku 4.5 is a priced model
    assert result.model == MODEL

    # The store of record: one episode + the quarantined claim edge.
    conn = sqlite3.connect(tmp_path / "learned.db")
    episodes = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    claims = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE relation = 'third_party_claim'"
    ).fetchone()[0]
    conn.close()
    assert episodes == 1
    assert claims == 1


def test_observe_routes_llm_calls_through_bedrock_client(tmp_path: Path) -> None:
    bedrock = FakeBedrock([_distill_response()])
    service = _service(tmp_path, bedrock)
    asyncio.run(service.observe("alice", "USER: I like tea.", author="user"))
    service.close()

    assert len(bedrock.calls) == 1
    assert bedrock.calls[0]["model_id"] == MODEL
    # veracium's distill prompt carries the event text.
    assert "I like tea." in json.dumps(bedrock.calls[0]["messages"])


# --- engine hook ---


def test_engine_observes_after_completed_run(tmp_path: Path) -> None:
    repos = in_memory_repositories()
    bedrock = FakeBedrock([_distill_response(), _distill_response()])
    service = _service(tmp_path, bedrock)
    engine = _engine(repos, bedrock, service)

    from workflow_platform.workflow import load_definition

    definition = load_definition(
        _definition(
            {
                "user_id": "alice@example.com",
                "observations": [
                    {
                        "text": "Received email about {trigger.subject}",
                        "author": "third_party",
                        "event_type": "email",
                        "ref_from": "trigger.message_id",
                    },
                    {
                        "text": "Classified {trigger.subject} as spam",
                        "author": "system",
                        "event_type": "triage",
                    },
                ],
            }
        )
    )
    instance = asyncio.run(
        engine.run(definition, trigger_payload={"subject": "hello", "message_id": "m-1"})
    )
    service.close()

    assert instance.state == WorkflowInstanceState.COMPLETED
    entries = asyncio.run(repos.audit.list_by_instance(instance.id))
    observed = [e for e in entries if e.action == "memory_observed"]
    assert len(observed) == 2
    assert observed[0].actor_type == "engine"
    assert observed[0].detail["user_id"] == "alice@example.com"
    assert observed[0].detail["evidence_ref"] == "m-1"
    assert observed[0].detail["text_hash"].startswith("sha256:")
    assert observed[1].detail["evidence_ref"] is None
    # Memory spend lands in the instance totals (metered like any Bedrock call).
    assert instance.context["total_tokens"] == 2 * 250
    assert instance.context["total_cost_usd"] > 0


def test_workflow_without_spec_makes_no_memory_calls(tmp_path: Path) -> None:
    repos = in_memory_repositories()
    bedrock = FakeBedrock([])  # any converse call would raise
    service = _service(tmp_path, bedrock)
    engine = _engine(repos, bedrock, service)

    from workflow_platform.workflow import load_definition

    instance = asyncio.run(engine.run(load_definition(_definition())))
    service.close()

    assert instance.state == WorkflowInstanceState.COMPLETED
    entries = asyncio.run(repos.audit.list_by_instance(instance.id))
    assert not [e for e in entries if e.action.startswith("memory_observe")]
    assert not (tmp_path / "learned.db").exists()


def test_spec_without_service_audits_skip() -> None:
    repos = in_memory_repositories()
    engine = _engine(repos, FakeBedrock([]), service=None)

    from workflow_platform.workflow import load_definition

    definition = load_definition(_definition({"user_id": "alice", "observations": [{"text": "x"}]}))
    instance = asyncio.run(engine.run(definition))

    assert instance.state == WorkflowInstanceState.COMPLETED
    entries = asyncio.run(repos.audit.list_by_instance(instance.id))
    skipped = [e for e in entries if e.action == "memory_observe_skipped"]
    assert len(skipped) == 1
    assert "no learned-memory service" in skipped[0].detail["reason"]


def test_observe_failure_does_not_fail_the_run(tmp_path: Path) -> None:
    class _ExplodingService(LearnedMemoryService):
        async def observe(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("distill exploded")

    repos = in_memory_repositories()
    bedrock = FakeBedrock([])
    engine = _engine(repos, bedrock, _ExplodingService(bedrock, tmp_path / "learned.db"))

    from workflow_platform.workflow import load_definition

    definition = load_definition(_definition({"user_id": "alice", "observations": [{"text": "x"}]}))
    instance = asyncio.run(engine.run(definition))

    assert instance.state == WorkflowInstanceState.COMPLETED
    entries = asyncio.run(repos.audit.list_by_instance(instance.id))
    failed = [e for e in entries if e.action == "memory_observe_failed"]
    assert len(failed) == 1
    assert "distill exploded" in failed[0].detail["error"]


def test_empty_rendered_observation_is_skipped(tmp_path: Path) -> None:
    repos = in_memory_repositories()
    bedrock = FakeBedrock([])  # no converse call should happen
    service = _service(tmp_path, bedrock)
    engine = _engine(repos, bedrock, service)

    from workflow_platform.workflow import load_definition

    definition = load_definition(
        _definition({"user_id": "alice", "observations": [{"text": "{trigger.missing}"}]})
    )
    instance = asyncio.run(engine.run(definition))
    service.close()

    assert instance.state == WorkflowInstanceState.COMPLETED
    entries = asyncio.run(repos.audit.list_by_instance(instance.id))
    skipped = [e for e in entries if e.action == "memory_observe_skipped"]
    assert len(skipped) == 1
    assert skipped[0].detail["reason"] == "observation rendered empty"


# --- template rendering ---


def test_render_observation_template() -> None:
    context = WorkflowContext(
        instance_id="i",
        workflow_id="w",
        trigger={"subject": "hi", "from_address": {"address": "a@b.c"}},
    )
    context.record_step_output("record", {"category": "spam", "confidence": 0.9})

    rendered = _render_observation_template(
        "From {trigger.from_address.address}: {trigger.subject} -> "
        "{steps.record.category} ({steps.record.confidence}) {trigger.nope}",
        context,
    )
    assert rendered == "From a@b.c: hi -> spam (0.9) "
    # Non-scalar values render empty rather than dumping JSON into memory.
    assert _render_observation_template("{trigger.from_address}", context) == ""


# --- dry-run isolation ---


def test_dry_run_uses_ephemeral_scratch_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()

    from workflow_platform.workflow import load_definition

    definition = load_definition(
        _definition({"user_id": "alice", "observations": [{"text": "note: {trigger.subject}"}]})
    )
    asyncio.run(repos.definitions.save(definition))

    real_db = tmp_path / "learned.db"
    bedrock = FakeBedrock([_distill_response()])
    engine = _engine(repos, bedrock, LearnedMemoryService(bedrock, real_db, model_id=MODEL))

    body = (
        TestClient(create_app(repositories=repos, engine=engine))
        .post("/api/workflows/lm-wf/dry-run", headers=_ADMIN, json={"subject": "hi"})
        .json()
    )
    assert body["state"] == "completed"
    # The observe path ran (one distill call) but the real store was never touched.
    assert len(bedrock.calls) == 1
    assert not real_db.exists()

    entries = asyncio.run(repos.audit.list_by_instance(body["instance_id"]))
    assert [e for e in entries if e.action == "memory_observed"]


# --- example workflow opt-in ---


def test_email_triage_live_declares_learned_memory() -> None:
    from workflow_platform.workflow import load_definition_from_file

    yaml_path = (
        Path(__file__).resolve().parent.parent.parent
        / "examples"
        / "email_triage_live"
        / "workflow.yaml"
    )
    definition = load_definition_from_file(yaml_path)
    spec = definition.learned_memory
    assert spec is not None
    assert spec.user_id == "qspencer@gmail.com"
    authors = [o.author for o in spec.observations]
    # The mail itself is quarantined third-party content; the verdict is ours.
    assert authors == ["third_party", "system"]
    assert all(o.ref_from == "trigger.message_id" for o in spec.observations)
