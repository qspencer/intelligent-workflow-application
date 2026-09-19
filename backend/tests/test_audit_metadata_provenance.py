"""Round-14 finding 1: a declared field whose SOURCE is the input.

The v9 widening declared 19 fields by SHAPE — `_TOKEN` / `_COUNT` /
`_AMOUNT` — and the round-14 sidecar claimed content could not survive
those validators. The reviewer disproved it: `evidence_ref` is resolved
from the workflow CONTEXT (`ref_from`, a YAML-configured dotted path), so
whatever the trigger carries at that path becomes a token-shaped value that
v9 released to a reader with no raw-trace grant — while the same value was
withheld in the stored trigger.

Shape bounds DAMAGE. It does not establish PROVENANCE. These tests pin the
distinction per field, and the first one walks the whole path the reviewer
walked: workflow -> learned-memory service -> engine -> audit storage ->
HTTP response.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine.executor import ToolCatalog, WorkflowEngine
from workflow_platform.engine.functions import default_function_registry
from workflow_platform.main import create_app
from workflow_platform.memory.learned import LearnedMemoryService
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.persistence.models import User
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

#: Token-shaped, so every `_TOKEN` validator accepts it — and taken from the
#: trigger, so it is input-derived. That combination is the whole finding.
SYNTHETIC_REF = "SYNTHETIC-input-derived-reference-0001"

_ADMIN = {"X-Dev-User": "root", "X-Dev-Groups": "admins"}


def _distill(facts: int = 1, quarantined: int = 0) -> Any:
    from tests._bedrock_fakes import text_response

    return text_response(
        '{"facts": '
        + "["
        + ", ".join(['{"text": "f"}'] * facts)
        + "]"
        + ', "quarantined": '
        + "["
        + ", ".join(['{"text": "q"}'] * quarantined)
        + "]"
        + "}"
    )


def _definition() -> dict[str, Any]:
    return {
        "id": "wf",
        "name": "wf",
        "trigger": {"type": "manual"},
        "steps": [
            {"id": "a", "type": "deterministic", "function": "noop", "config": {}},
        ],
        "edges": [],
        "learned_memory": {
            "user_id": "alice@example.com",
            "source_id": "gmail:alice@example.com",
            "observations": [
                {
                    "text": "An event happened",
                    "author": "third_party",
                    "event_type": "email",
                    # THE PATH: the reference is read out of the trigger.
                    "ref_from": "trigger.reference",
                }
            ],
        },
    }


def test_an_input_derived_reference_is_NOT_released_without_a_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's reproduction, end to end."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    repos = in_memory_repositories()
    bedrock = FakeBedrock([_distill()])
    service = LearnedMemoryService(db_path=tmp_path / "learned.db", bedrock=bedrock)
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog([]),
        bedrock=bedrock,
        world=mock_world(),
        learned_memory=service,
        trace_safe_only=True,
    )

    async def go() -> str:
        instance = await engine.run(
            load_definition(_definition()),
            trigger_payload={"reference": SYNTHETIC_REF, "subject": "s"},
        )
        await repos.users.save(
            User(iss="dev", sub="root", org_id="default", roles=["Administrator"])
        )
        return instance.id

    instance_id = asyncio.run(go())
    client = TestClient(create_app(repositories=repos, engine=engine))
    body = client.get(f"/api/workflow-instances/{instance_id}/audit", headers=_ADMIN).text

    assert SYNTHETIC_REF not in body, (
        "an input-derived reference reached a reader with NO raw-trace grant. "
        "It passes `_TOKEN`, but shape does not establish that the value is "
        "appropriate for that reader — round-14 finding 1."
    )


def test_engine_computed_and_canonical_fields_DO_survive() -> None:
    """The widening is not withdrawn wholesale: fields whose source is
    established — canonical ids from records, hashes and counts computed by
    the component that emits them — still reach an ordinary reader. Without
    this the fix would just be a revert."""
    from workflow_platform.trace_projection import project_audit_detail_at_rest as at_rest

    kept = at_rest(
        "memory_observed",
        {
            "workflow_id": "dmarc-ingest",
            "instance_id": "i-1",
            "facts": 3,
            "cost_usd": 0.01,
            "text_hash": "sha256:abc123",
        },
    )
    for field in ("workflow_id", "instance_id", "facts", "cost_usd", "text_hash"):
        assert field in kept and kept[field] != "[redacted — raw-trace grant required]", (
            f"{field} has an established source and should still be released; got {kept}"
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        # Resolved from the workflow context, so the input controls it.
        ("evidence_ref", SYNTHETIC_REF),
        # A free-form YAML string, which a SCAFFOLDED workflow means the model
        # wrote. Not a closed set, so not releasable on shape alone.
        ("event_type", "SYNTHETIC-author-controlled"),
    ],
)
def test_fields_without_an_established_source_stay_withheld(field: str, value: str) -> None:
    from workflow_platform.trace_projection import project_audit_detail_at_rest as at_rest

    out = at_rest("memory_observed", {field: value})
    assert value not in str(out), f"{field} is input- or author-controlled and must not be released"


@pytest.mark.parametrize("author", ["user", "third_party", "system"])
def test_closed_enum_classifications_survive_their_own_values(author: str) -> None:
    """`author` and `derived_from` are `Literal["user","third_party","system"]`,
    so they are releasable as CLOSED ENUMS — validated against the set, not
    accepted because they happen to be short tokens."""
    from workflow_platform.trace_projection import project_audit_detail_at_rest as at_rest

    out = at_rest("memory_observed", {"author": author, "derived_from": author})
    assert out.get("author") == author
    assert out.get("derived_from") == author


def test_a_forged_classification_outside_the_enum_is_refused() -> None:
    from workflow_platform.trace_projection import project_audit_detail_at_rest as at_rest

    out = at_rest("memory_observed", {"author": "SYNTHETIC-not-an-author"})
    assert out.get("author") != "SYNTHETIC-not-an-author"
