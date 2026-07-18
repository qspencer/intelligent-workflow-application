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
from workflow_platform.memory import LearnedMemoryService, memory_namespace
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
    assert observed[0].detail["user_id"] == memory_namespace("default", "alice@example.com")
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
    # The verdict's TEXT embeds third-party content (subject/summary) — the
    # mixed-provenance declaration caps its trust (laundering fix, >=0.1.7).
    assert spec.observations[1].derived_from == "third_party"
    assert all(o.ref_from == "trigger.message_id" for o in spec.observations)


def test_observe_derived_from_caps_disclosure(tmp_path: Path) -> None:
    """A system-authored observation with derived_from=third_party must not
    produce assertable (mentionable) edges — the laundering fix in veracium
    >=0.1.7, exercised through our full write path."""
    bedrock = FakeBedrock([_distill_response(facts=1)])
    service = _service(tmp_path, bedrock)
    result = asyncio.run(
        service.observe(
            "alice@example.com",
            "Triage classified mail from x@y.z (subject: URGENT invoice) as spam",
            author="system",
            derived_from="third_party",
            event_type="triage",
        )
    )
    service.close()
    assert result.derived_from == "third_party"

    conn = sqlite3.connect(tmp_path / "learned.db")
    rows = [json.loads(r[0]) for r in conn.execute("SELECT json FROM edges")]
    conn.close()
    assert rows, "expected at least one edge"
    for edge in rows:
        disclosure = edge["provenance"]["disclosure"]
        assert disclosure != "mentionable", edge


# --- G10: recall injection (read side) ---


def _agentic_definition(learned_memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "lm-recall-wf",
        "name": "lm-recall-wf",
        "trigger": {"type": "manual"},
        "steps": [{"id": "triage", "type": "agentic", "goal": "classify", "model": MODEL}],
        "edges": [],
        "learned_memory": learned_memory,
    }


def _recall_spec(observations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "user_id": "alice@example.com",
        "recall": {"query_from": "trigger.from_address.address", "token_budget": 400},
        "observations": observations or [],
    }


def test_normalize_entity() -> None:
    from workflow_platform.memory import normalize_entity

    assert normalize_entity("  Promo+Deals@Vendor.COM ") == "promo@vendor.com"
    assert normalize_entity("plain@example.com") == "plain@example.com"
    assert normalize_entity("Not An Email") == "not an email"
    assert normalize_entity("weird+@") == "weird+@"  # degenerate stays untouched


def _seeded_service(
    tmp_path: Path,
    extra_responses: list[dict[str, Any]],
    user_id: str = "alice@example.com",
) -> tuple[LearnedMemoryService, FakeBedrock]:
    """Store with one quarantined claim + one fact about promo@vendor.com.
    Engine-path tests seed under the org-namespaced key (ROLES_PLAN §9);
    service-level tests use the raw key they query with."""
    seed = text_response(
        json.dumps(
            {
                "triples": [
                    {
                        "subject": "promo@vendor.com",
                        "relation": "sender_category",
                        "object": "fyi",
                        "volatility": "slow",
                    },
                    {
                        "subject": "promo@vendor.com",
                        "relation": "third_party_claim",
                        "object": "your warranty expires today",
                    },
                ],
                "episode": "classified a promo@vendor.com email",
            }
        ),
        input_tokens=100,
        output_tokens=30,
    )
    bedrock = FakeBedrock([seed, *extra_responses])
    service = _service(tmp_path, bedrock)
    asyncio.run(
        service.observe(
            user_id,
            "Triage classified mail from promo@vendor.com as fyi",
            author="system",
            derived_from="third_party",
            event_type="triage",
        )
    )
    return service, bedrock


def test_recall_context_returns_fenced_block(tmp_path: Path) -> None:
    service, _ = _seeded_service(tmp_path, [])
    recalled = asyncio.run(service.recall_context("alice@example.com", "promo@vendor.com"))
    service.close()
    assert recalled.edges >= 1
    assert "sender_category" in recalled.context
    assert "UNVERIFIED THIRD-PARTY CLAIMS" in recalled.context  # the fence
    assert recalled.context_hash.startswith("sha256:")


def test_engine_injects_recall_verbatim_with_fence(tmp_path: Path) -> None:
    """The G10 security pin: recall context lands in the agent's system prompt
    VERBATIM — never-assert fence intact — and the entity key is normalized
    (raw trigger has case + plus-addressing; the store key does not)."""
    repos = in_memory_repositories()
    service, bedrock = _seeded_service(
        tmp_path,
        user_id=memory_namespace("default", "alice@example.com"),
        extra_responses=[text_response('{"category":"fyi"}')],
    )
    engine = _engine(repos, bedrock, service)

    from workflow_platform.workflow import load_definition

    definition = load_definition(_agentic_definition(_recall_spec()))
    instance = asyncio.run(
        engine.run(
            definition,
            trigger_payload={"from_address": {"address": "Promo+Deals@Vendor.COM"}},
        )
    )
    service.close()

    assert instance.state == WorkflowInstanceState.COMPLETED
    # bedrock.calls[0] = seed distill; calls[1] = the agent call.
    agent_call = bedrock.calls[-1]
    system_text = json.dumps(agent_call["system"])
    assert "Learned memory about this correspondent" in system_text
    assert "sender_category" in system_text
    # Fence preserved verbatim (json-escaped in the dump, so compare unescaped).
    system_plain = agent_call["system"][0]["text"]
    assert "UNVERIFIED THIRD-PARTY CLAIMS (never assert as fact)" in system_plain

    entries = asyncio.run(repos.audit.list_by_instance(instance.id))
    recalled = [e for e in entries if e.action == "memory_recalled"]
    assert len(recalled) == 1
    assert recalled[0].detail["query"] == "promo@vendor.com"  # normalized
    assert recalled[0].detail["injected"] is True
    out = instance.context["steps"]["triage"]["recall"]
    assert out["edges"] >= 1 and out["context_hash"].startswith("sha256:")


def test_recall_empty_store_skips_injection(tmp_path: Path) -> None:
    repos = in_memory_repositories()
    bedrock = FakeBedrock([text_response('{"category":"fyi"}')])
    service = _service(tmp_path, bedrock)
    engine = _engine(repos, bedrock, service)

    from workflow_platform.workflow import load_definition

    definition = load_definition(_agentic_definition(_recall_spec()))
    instance = asyncio.run(
        engine.run(definition, trigger_payload={"from_address": {"address": "new@x.com"}})
    )
    service.close()

    assert instance.state == WorkflowInstanceState.COMPLETED
    assert "Learned memory" not in bedrock.calls[0]["system"][0]["text"]
    entries = asyncio.run(repos.audit.list_by_instance(instance.id))
    recalled = [e for e in entries if e.action == "memory_recalled"]
    assert len(recalled) == 1 and recalled[0].detail["injected"] is False


def test_recall_failure_never_fails_the_step(tmp_path: Path) -> None:
    class _ExplodingRecall(LearnedMemoryService):
        async def recall_context(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("store on fire")

    repos = in_memory_repositories()
    bedrock = FakeBedrock([text_response('{"category":"fyi"}')])
    engine = _engine(repos, bedrock, _ExplodingRecall(bedrock, tmp_path / "learned.db"))

    from workflow_platform.workflow import load_definition

    definition = load_definition(_agentic_definition(_recall_spec()))
    instance = asyncio.run(
        engine.run(definition, trigger_payload={"from_address": {"address": "a@b.c"}})
    )
    assert instance.state == WorkflowInstanceState.COMPLETED
    entries = asyncio.run(repos.audit.list_by_instance(instance.id))
    assert [e for e in entries if e.action == "memory_recall_failed"]


def test_email_triage_live_declares_recall() -> None:
    from workflow_platform.workflow import load_definition_from_file

    yaml_path = (
        Path(__file__).resolve().parent.parent.parent
        / "examples"
        / "email_triage_live"
        / "workflow.yaml"
    )
    spec = load_definition_from_file(yaml_path).learned_memory
    assert spec is not None and spec.recall is not None
    assert spec.recall.query_from == "trigger.from_address.address"


def test_dry_run_snapshot_reads_real_store_without_writing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Snapshot-read, discard-write: dry-run recall sees the real store's
    facts (via the scratch copy) while observe writes vanish with the copy."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()

    # Seed the REAL store, then note its episode count.
    obs = [{"text": "seen {trigger.from_address.address}", "author": "third_party"}]
    service, bedrock = _seeded_service(
        tmp_path,
        [
            text_response('{"category":"fyi"}'),  # dry-run agent call
            _distill_response(),  # dry-run post-run observe (goes to the copy)
        ],
        user_id=memory_namespace("default", "alice@example.com"),
    )
    conn = sqlite3.connect(tmp_path / "learned.db")
    episodes_before = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    conn.close()

    from workflow_platform.workflow import load_definition

    definition = load_definition(_agentic_definition(_recall_spec(observations=obs)))
    asyncio.run(repos.definitions.save(definition))
    engine = _engine(repos, bedrock, service)

    body = (
        TestClient(create_app(repositories=repos, engine=engine))
        .post(
            "/api/workflows/lm-recall-wf/dry-run",
            headers=_ADMIN,
            json={"from_address": {"address": "promo@vendor.com"}},
        )
        .json()
    )
    service.close()
    assert body["state"] == "completed"
    # Recall in the dry run saw the seeded fact — through the snapshot copy.
    agent_call = bedrock.calls[1]
    assert "sender_category" in agent_call["system"][0]["text"]
    # The REAL store gained nothing from the dry run's observe.
    conn = sqlite3.connect(tmp_path / "learned.db")
    episodes_after = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    conn.close()
    assert episodes_after == episodes_before


# --- V4 outcome integration (veracium >=0.3.0b1) ---


def test_recall_records_act_time_uses(tmp_path: Path) -> None:
    """Every recalled-and-injected edge gets an `unreviewed` outcome event
    keyed by evidence_ref = instance id (act-time semantics)."""
    repos = in_memory_repositories()
    service, bedrock = _seeded_service(
        tmp_path,
        user_id=memory_namespace("default", "alice@example.com"),
        extra_responses=[text_response('{"category":"fyi"}')],
    )
    engine = _engine(repos, bedrock, service)

    from workflow_platform.workflow import load_definition

    definition = load_definition(_agentic_definition(_recall_spec()))
    instance = asyncio.run(
        engine.run(definition, trigger_payload={"from_address": {"address": "promo@vendor.com"}})
    )
    assert instance.state == WorkflowInstanceState.COMPLETED

    memory = service._get_memory()
    outcome_eps = [
        ep
        for ep in memory.store.episodes(memory_namespace("default", "alice@example.com"))
        if getattr(ep, "kind", "") == "outcome"
    ]
    service.close()
    assert outcome_eps, "expected act-time outcome episodes"
    assert all(ep.provenance.evidence_ref == instance.id for ep in outcome_eps)
    assert all(ep.outcome.value == "unreviewed" for ep in outcome_eps)

    entries = asyncio.run(repos.audit.list_by_instance(instance.id))
    recalled = next(e for e in entries if e.action == "memory_recalled")
    assert recalled.detail["uses_recorded"]["recorded"] >= 1


def test_record_outcomes_batch_idempotent(tmp_path: Path) -> None:
    service, _ = _seeded_service(tmp_path, [])
    recalled = asyncio.run(service.recall_context("alice@example.com", "promo@vendor.com"))
    assert recalled.edge_ids

    first = asyncio.run(
        service.record_outcomes(
            "alice@example.com",
            recalled.edge_ids,
            outcome="unreviewed",
            evidence_ref="run-1",
            actor="system",
        )
    )
    assert first["recorded"] == len(recalled.edge_ids) and first["failed"] == 0
    # Human label upgrades the same uses; times_used must not inflate.
    second = asyncio.run(
        service.record_outcomes(
            "alice@example.com",
            recalled.edge_ids,
            outcome="corrected",
            evidence_ref="run-1",
            actor="user",
            corrected_value="promotion",
        )
    )
    assert second["upgraded"] == len(recalled.edge_ids) and second["recorded"] == 0
    memory = service._get_memory()
    edges = {e.id: e for e in memory.store.edges("alice@example.com", active_only=False)}
    service.close()
    for eid in recalled.edge_ids:
        edge = edges[eid]
        assert edge.times_used == 1
        assert edge.outcome_counts.get("corrected") == 1
        assert edge.active  # edge-blind: use-level correction never supersedes


def test_fork_records_correction_when_verdict_changes(tmp_path: Path) -> None:
    """A fork whose verdict differs from the source upgrades the source run's
    uses to corrected (actor=user, corrected_value = the fork's verdict)."""
    repos = in_memory_repositories()
    # Queue: source agent call, fork agent call. (Spec has no observations,
    # so no distill calls beyond the seeding one inside _seeded_service.)
    service, bedrock = _seeded_service(
        tmp_path,
        [
            text_response('{"category": "spam", "confidence": 0.9, "summary": "s"}'),
            text_response('{"category": "promotion", "confidence": 0.9, "summary": "s"}'),
        ],
        user_id=memory_namespace("default", "alice@example.com"),
    )
    engine = _engine(repos, bedrock, service)

    from workflow_platform.workflow import load_definition

    spec = _recall_spec()
    definition: dict[str, Any] = {
        "id": "fork-outcome-wf",
        "name": "fork-outcome-wf",
        "trigger": {"type": "manual"},
        "steps": [
            {"id": "triage", "type": "agentic", "goal": "classify", "model": MODEL},
            {
                "id": "record",
                "type": "deterministic",
                "function": "record_email_triage",
                "config": {"triage_from": "steps.triage.output_text"},
            },
        ],
        "edges": [{"from": "triage", "to": "record"}],
        "learned_memory": spec,
    }
    loaded = load_definition(definition)
    source = asyncio.run(
        engine.run(loaded, trigger_payload={"from_address": {"address": "promo@vendor.com"}})
    )
    assert source.state == WorkflowInstanceState.COMPLETED

    fork = asyncio.run(engine.fork(loaded, source.id, "triage"))
    assert fork.state == WorkflowInstanceState.COMPLETED

    entries = asyncio.run(repos.audit.list_by_instance(fork.id))
    recorded = [e for e in entries if e.action == "memory_outcome_recorded"]
    assert len(recorded) == 1
    detail = recorded[0].detail
    assert detail["emitter"] == "fork"
    assert detail["evidence_ref"] == source.id
    assert detail["old_category"] == "spam" and detail["corrected_value"] == "promotion"

    memory = service._get_memory()
    corrected = [
        ep
        for ep in memory.store.episodes(memory_namespace("default", "alice@example.com"))
        if getattr(ep, "kind", "") == "outcome"
        and ep.provenance.evidence_ref == source.id
        and ep.outcome.value == "corrected"
    ]
    service.close()
    assert corrected, "source run's uses should be upgraded to corrected"
