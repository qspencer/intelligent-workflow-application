"""Per-org envelope encryption for the vault (Contract B1 / TG3d-1,
docs/TRACE_GOVERNANCE_PLAN.md §0.1). A DB dump holds ciphertext only; the
AEAD binds each payload to its (org, instance, attempt, kind) so a
substituted / edited / wrong-org row fails to open."""

from __future__ import annotations

import base64
from typing import Any, ClassVar

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
from workflow_platform.engine import FunctionRegistry, ToolCatalog, WorkflowEngine
from workflow_platform.persistence import WorkflowInstanceState, in_memory_repositories
from workflow_platform.tools import Tool, ToolContext, ToolResult
from workflow_platform.trace_cipher import ENV_MASTER_KEY, TraceCipher, TraceCipherError
from workflow_platform.trace_rehydrate import RawTraceRehydrator
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

SECRET = "CIPHER-SECRET"
_AAD: dict[str, Any] = {
    "org_id": "orgA",
    "instance_id": "i1",
    "step_attempt_id": "s1",
    "kind": "tool_calls",
    "schema_version": 1,
}


def _cipher() -> TraceCipher:
    return TraceCipher(b"m" * 32)


def test_seal_open_roundtrips_and_hides_plaintext() -> None:
    c = _cipher()
    sealed = c.seal({"body": SECRET}, **_AAD)
    assert sealed["alg"] == "AESGCM-HKDF-1" and sealed["key_id"] == "orgA"
    assert SECRET not in str(sealed)  # ciphertext at rest
    assert c.open(sealed, **_AAD) == {"body": SECRET}


def test_aad_binding_and_tamper_and_org_isolation() -> None:
    c = _cipher()
    sealed = c.seal({"body": SECRET}, **_AAD)
    # wrong instance (AAD mismatch) → fails
    with pytest.raises(TraceCipherError):
        c.open(sealed, **{**_AAD, "instance_id": "OTHER"})
    # wrong org (different derived key + AAD) → fails
    with pytest.raises(TraceCipherError):
        c.open(sealed, **{**_AAD, "org_id": "orgB"})
    # tampered ciphertext → fails
    tampered = {**sealed, "ct": base64.b64encode(b"garbage" * 8).decode()}
    with pytest.raises(TraceCipherError):
        c.open(tampered, **_AAD)


def test_per_org_keys_differ() -> None:
    c = _cipher()
    a = c.seal({"x": 1}, **{**_AAD, "org_id": "orgA"})
    b = c.seal({"x": 1}, **{**_AAD, "org_id": "orgB"})
    # different derived keys → different ciphertext (also different nonce)
    assert a["ct"] != b["ct"]


class _SecretTool(Tool):
    name = "leaky_tool"
    description = "returns sensitive content"
    parameters_schema: ClassVar[dict[str, Any]] = {"type": "object"}
    effect = "read_only"

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        return ToolResult(content={"text": SECRET})


async def test_vault_is_encrypted_at_rest_and_decrypts_on_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        ENV_MASTER_KEY, base64.b64encode(b"master-key-32-bytes-exactly!!!!!").decode()
    )
    repos = in_memory_repositories()
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog([_SecretTool()]),
        bedrock=FakeBedrock(
            [
                tool_use_response(tool_uses=[("t1", "leaky_tool", {"body": SECRET})]),
                text_response(f"Saw {SECRET}"),
            ]
        ),
        world=mock_world(),
        trace_safe_only=True,
    )
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "call the tool",
                    "model": "claude-haiku-4-5",
                    "tools": ["leaky_tool"],
                }
            ],
            "edges": [],
        }
    )
    instance = await engine.run(definition, trigger_payload={"body": SECRET})
    assert instance.state == WorkflowInstanceState.COMPLETED

    # Vault rows are SEALED — a DB dump reveals no plaintext.
    vault = await repos.raw_trace_vault.list_by_instance(instance.id)
    assert vault
    for v in vault:
        assert isinstance(v.payload, dict) and v.payload.get("alg") == "AESGCM-HKDF-1"
    assert SECRET not in str([v.payload for v in vault])

    # A grant-holder read decrypts it back (rehydrator shares the env key).
    steps = await repos.steps.list_by_instance(instance.id)
    act = next(s for s in steps if s.step_id == "act")
    assert act.output is not None
    merged = await RawTraceRehydrator(repos).merge_output(
        org_id=instance.org_id,
        instance_id=instance.id,
        step_attempt_id=act.id,
        safe_output=act.output,
    )
    assert SECRET in str(merged)  # decrypted from the vault
