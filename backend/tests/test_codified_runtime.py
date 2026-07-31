"""CODIFY_PLAN v2 runtime: codified_sender_check routing + record composition
+ the disable overlay (fail-open everywhere; MockWorld filesystem)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from workflow_platform.engine.context import WorkflowContext
from workflow_platform.engine.functions import (
    codified_sender_check,
    record_email_triage,
)
from workflow_platform.engine.registry import StepFailure
from workflow_platform.world import World, mock_world

RULES_REL = "default/q@x.com/wf.json"
NOW = datetime.now(UTC)


def _artifact(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format_version": 1,
        "triage_schema_version": 2,
        "rubric_hash": "sha256:abc",
        "codifier_version": 1,
        "senders": {
            "news@vendor.com": {
                "category": "newsletter",
                "status": "active",
                "distinct_messages": 9,
                "current_schema_messages": 9,
                "last_evidence_at": (NOW - timedelta(days=1)).isoformat(),
                "expires_at": (NOW + timedelta(days=30)).isoformat(),
            }
        },
    }
    base.update(over)
    return base


def _context(sender: str = "news@vendor.com", auth: bool = True) -> WorkflowContext:
    return WorkflowContext(
        instance_id="i-1",
        workflow_id="wf",
        trigger={
            "from_address": {"address": sender},
            "message_id": "19f0000000000001",
            "auth_pass": auth,
        },
    )


def _world(artifact: dict[str, Any] | None, overlay: dict[str, Any] | None = None) -> World:
    world = mock_world()
    if artifact is not None:
        asyncio.run(world.fs.write_text(f".memory/codified/{RULES_REL}", json.dumps(artifact)))
    if overlay is not None:
        asyncio.run(
            world.fs.write_text(f".memory/codified/{RULES_REL}.disabled", json.dumps(overlay))
        )
    return world


CONFIG = {"rules_path": RULES_REL, "account": "q@x.com", "sample_one_in": 1_000_000}


def _check(world: World, context: WorkflowContext, **config_over: Any) -> dict[str, Any]:
    return asyncio.run(codified_sender_check({**CONFIG, **config_over}, context, world))


def test_listed_authenticated_routes_codified() -> None:
    out = _check(_world(_artifact()), _context())
    assert out["route"] == "codified"
    assert out["listed"] and out["authenticated"] and out["rule_compatible"]
    assert out["rule_category"] == "newsletter"


def test_unlisted_and_missing_file_fail_open() -> None:
    assert _check(_world(_artifact()), _context("other@x.com"))["route"] == "full"
    assert _check(_world(None), _context())["route"] == "full"
    world = mock_world()
    asyncio.run(world.fs.write_text(f".memory/codified/{RULES_REL}", "not json"))
    assert _check(world, _context())["route"] == "full"


def test_unauthenticated_listed_sender_gets_judgment() -> None:
    out = _check(_world(_artifact()), _context(auth=False))
    assert out["route"] == "full"
    assert out["listed"] is True and out["authenticated"] is False


def test_schema_mismatch_expiry_inactivity_and_overlay_disable() -> None:
    assert _check(_world(_artifact(triage_schema_version=3)), _context())["route"] == "full"

    stale = _artifact()
    stale["senders"]["news@vendor.com"]["expires_at"] = (NOW - timedelta(days=1)).isoformat()
    assert _check(_world(stale), _context())["route"] == "full"

    inactive = _artifact()
    inactive["senders"]["news@vendor.com"]["last_evidence_at"] = (
        NOW - timedelta(days=45)
    ).isoformat()
    assert _check(_world(inactive), _context())["route"] == "full"

    overlay = {"disabled_senders": {"news@vendor.com": {"reason": "correction"}}}
    assert _check(_world(_artifact(), overlay), _context())["route"] == "full"


def test_sampling_routes_full_and_carries_rule_category() -> None:
    out = _check(_world(_artifact()), _context(), sample_one_in=1)
    assert out["sampled"] is True and out["route"] == "full"
    assert out["rule_category"] == "newsletter"


# --- record composition per route ---


def _record(
    context: WorkflowContext,
    world: World,
    *,
    triage: str | None,
    attention: str | None,
    precheck: dict[str, Any],
) -> dict[str, Any]:
    context.record_step_output("precheck", precheck)
    if triage is not None:
        context.record_step_output("triage", {"output_text": triage})
    if attention is not None:
        context.record_step_output("classify_attention", {"output_text": attention})
    config = {
        **CONFIG,
        "triage_from": "steps.triage.output_text",
        "attention_from": "steps.classify_attention.output_text",
        "route_from": "steps.precheck.route",
    }
    return asyncio.run(record_email_triage(config, context, world))


PRECHECK_CODIFIED = {
    "route": "codified",
    "sampled": False,
    "rule_category": "newsletter",
    "rule_evidence": {"distinct_messages": 9},
}
PRECHECK_SAMPLED = {"route": "full", "sampled": True, "rule_category": "newsletter"}


def test_codified_route_composition() -> None:
    world = _world(_artifact())
    out = _record(
        _context(),
        world,
        triage=None,
        attention='{"attention": [], "decision_note": "boring"}',
        precheck=PRECHECK_CODIFIED,
    )
    assert out["decision_source"] == "codified_sender_rule"
    assert out["category"] == "newsletter" and out["category_valid"]
    assert out["model_confidence"] is None
    assert out["apply_labels"] == ["wf/newsletter"]
    assert out["rule_evidence"] == {"distinct_messages": 9}


def test_codified_attention_detected_disables_rule() -> None:
    world = _world(_artifact())
    out = _record(
        _context(),
        world,
        triage=None,
        attention='{"attention": ["review"]}',
        precheck=PRECHECK_CODIFIED,
    )
    assert out["apply_labels"] == ["wf/newsletter", "wf-attn/review"]
    overlay = json.loads(asyncio.run(world.fs.read_text(f".memory/codified/{RULES_REL}.disabled")))
    assert overlay["disabled_senders"]["news@vendor.com"]["reason"] == "attention_detected"


def test_sampled_mismatch_disables_rule() -> None:
    world = _world(_artifact())
    out = _record(
        _context(),
        world,
        triage='{"category": "promotion", "attention": [], "category_confidence": 0.9}',
        attention=None,
        precheck=PRECHECK_SAMPLED,
    )
    assert out["decision_source"] == "classifier"
    assert out["codified_mismatch"] is True
    overlay = json.loads(asyncio.run(world.fs.read_text(f".memory/codified/{RULES_REL}.disabled")))
    assert overlay["disabled_senders"]["news@vendor.com"]["reason"] == "sampled_category_mismatch"


def test_sampled_agreement_no_disable() -> None:
    world = _world(_artifact())
    out = _record(
        _context(),
        world,
        triage='{"category": "newsletter", "attention": [], "category_confidence": 0.9}',
        attention=None,
        precheck=PRECHECK_SAMPLED,
    )
    assert "codified_mismatch" not in out
    assert not asyncio.run(world.fs.exists(f".memory/codified/{RULES_REL}.disabled"))


def test_exactly_one_classifier_output_enforced() -> None:
    world = _world(_artifact())
    with pytest.raises(StepFailure):
        _record(
            _context(),
            world,
            triage='{"category": "newsletter"}',
            attention='{"attention": []}',
            precheck=PRECHECK_CODIFIED,
        )
    with pytest.raises(StepFailure):
        _record(_context(), world, triage=None, attention=None, precheck=PRECHECK_CODIFIED)


def test_record_preserves_full_attention_set_despite_label_dominance() -> None:
    """External review finding 8: urgent-subsumes-review is a LABEL policy,
    not an erasure — the record keeps both judgments."""
    context = _context()
    context.record_step_output(
        "triage",
        {
            "output_text": '{"category": "notification", "attention": ["urgent", "review"], "category_confidence": 0.9}'
        },
    )
    out = asyncio.run(
        record_email_triage({"triage_from": "steps.triage.output_text"}, context, mock_world())
    )
    assert out["attention"] == ["urgent", "review"]  # both preserved in the record
    assert out["apply_labels"] == ["wf/notification", "wf-attn/urgent"]  # review label dropped


def test_disable_overlay_concurrent_writes_dont_lose_entries() -> None:
    """External review finding 17: two apply steps disabling different
    senders concurrently must not lose one another's entry."""
    world = _world(_artifact())

    async def _disable_two() -> None:
        from workflow_platform.engine.functions import _disable_codified_sender

        async def one(sender: str) -> None:
            ctx = WorkflowContext(
                instance_id=f"i-{sender}",
                workflow_id="wf",
                trigger={"from_address": {"address": sender}, "message_id": "m"},
            )
            await _disable_codified_sender({**CONFIG}, ctx, world, reason="attention_detected")

        await asyncio.gather(one("a@x.com"), one("b@x.com"), one("c@x.com"))

    asyncio.run(_disable_two())
    overlay = json.loads(asyncio.run(world.fs.read_text(f".memory/codified/{RULES_REL}.disabled")))
    assert set(overlay["disabled_senders"]) == {"a@x.com", "b@x.com", "c@x.com"}
