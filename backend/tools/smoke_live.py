"""End-to-end smoke test against real Bedrock.

Three checks of increasing scope:

1. BedrockClient.converse direct call (validates SDK + auth + model access).
2. Agent.run with no tools (validates the tool-use loop minus tools).
3. WorkflowEngine running an agentic step (validates the full stack:
   engine → agent → bedrock → cost attribution).

On failure, classifies the error against the gates in docs/BEDROCK_SETUP.md
and stops — later steps would fail the same way.

Spends a few cents of Haiku calls. Useful exactly once after a big change to
the wrapper or a new region/account.

Usage:
    cd backend && uv run python tools/smoke_live.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from workflow_platform.agent import Agent
from workflow_platform.bedrock import BedrockClient, BedrockMode
from workflow_platform.engine import (
    FunctionRegistry,
    ToolCatalog,
    WorkflowEngine,
)
from workflow_platform.persistence import (
    WorkflowInstanceState,
    in_memory_repositories,
)
from workflow_platform.workflow import load_definition
from workflow_platform.world import mock_world

REGION = "us-east-1"
MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

BAR = "=" * 64
INDENT = "      "


def _diagnose(exc: BaseException) -> tuple[str, str] | None:
    """Map a known AWS/Bedrock error to (cause, action). None if unknown."""
    if isinstance(exc, NoCredentialsError):
        return (
            "No AWS credentials in the environment.",
            "Configure credentials (env vars, ~/.aws/credentials, or instance role). "
            "`aws sts get-caller-identity` should succeed first.",
        )
    if isinstance(exc, EndpointConnectionError):
        return (
            f"Could not reach Bedrock at region {REGION}.",
            "Check network egress and that the region is correct.",
        )
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        code = err.get("Code", "")
        msg = err.get("Message", "")
        text = f"{code}: {msg}".lower()
        if "use case details" in text:
            return (
                "Gate 3 — Anthropic use case details form not on file (or not yet propagated).",
                "Submit the form (AWS Console → Bedrock → Bedrock configurations) and "
                "wait ~15 minutes. See docs/BEDROCK_SETUP.md.",
            )
        if "inference profile" in text or "on-demand throughput" in text:
            return (
                "Gate 2 — Claude 4.x cannot be invoked on-demand; it needs an inference profile.",
                f"Use a profile id (e.g. {MODEL}) as the model id. See docs/BEDROCK_SETUP.md.",
            )
        if "legacy" in text:
            return (
                "Gate 1 — model is marked Legacy (auto-deactivated after non-use).",
                "Switch to an active Claude 4.x model. "
                "`aws bedrock list-foundation-models --by-provider Anthropic` lists current options.",
            )
        if code == "AccessDeniedException":
            return (
                "IAM denied the InvokeModel/Converse call.",
                "Check the calling principal has bedrock:InvokeModel on the model ARN. "
                "See infra/iam.tf for the production policy shape.",
            )
        if code == "ThrottlingException":
            return (
                "Gate 4 — Bedrock service quota exceeded.",
                "Service Quotas → Amazon Bedrock → request an increase for this model.",
            )
        return (f"{code or 'Bedrock error'}: {msg}", "See docs/BEDROCK_SETUP.md for known gates.")
    return None


def _row(label: str, value: str) -> None:
    print(f"{INDENT}{label:<9} {value}")


async def step_1_direct_converse() -> None:
    bedrock = BedrockClient(mode=BedrockMode.LIVE, region=REGION)
    response = await bedrock.converse(
        model_id=MODEL,
        messages=[{"role": "user", "content": [{"text": "Reply with one word: alive."}]}],
        inference_config={"maxTokens": 20, "temperature": 0.0},
    )
    text = response["output"]["message"]["content"][0]["text"]
    usage = response["usage"]
    _row("reply:", repr(text))
    _row("tokens:", f"input={usage['inputTokens']} output={usage['outputTokens']}")


async def step_2_agent_no_tools() -> None:
    agent = Agent(
        system_prompt="Reply with exactly one word.",
        tools=[],
        model_id=MODEL,
        bedrock=BedrockClient(mode=BedrockMode.LIVE, region=REGION),
    )
    result = await agent.run("Are you working?")
    _row("output:", repr(result.output_text))
    _row("stop:", result.stop_reason.value)
    _row("tokens:", f"total={result.usage.total_tokens} iterations={result.usage.iterations}")


async def step_3_workflow_engine() -> None:
    repos = in_memory_repositories()
    bedrock = BedrockClient(mode=BedrockMode.LIVE, region=REGION)

    definition = load_definition(
        {
            "id": "smoke",
            "name": "Smoke",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "act",
                    "type": "agentic",
                    "goal": "Reply with exactly one short sentence acknowledging you ran.",
                    "model": MODEL,
                    "tools": [],
                    "policy": {"max_iterations": 2, "max_total_tokens": 1000},
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=FunctionRegistry(),
        tools=ToolCatalog(),
        bedrock=bedrock,
        world=mock_world(),
    )
    instance = await engine.run(definition, trigger_payload={"src": "smoke-test"})

    if instance.state != WorkflowInstanceState.COMPLETED:
        raise RuntimeError(
            f"workflow ended in state {instance.state.value}: {instance.error or '<no error>'}"
        )

    steps = await repos.steps.list_by_instance(instance.id)
    audit = await repos.audit.list_by_instance(instance.id)
    short_id = instance.id.split("-")[0] if "-" in instance.id else instance.id[:8]
    _row("instance:", f"{short_id}  state={instance.state.value}")
    _row("tokens:", str(instance.context["total_tokens"]))
    _row("cost:", f"${instance.context['total_cost_usd']:.6f}")
    _row("reply:", repr(steps[0].output["output_text"]))  # type: ignore[index]
    _row("audit:", f"{len(audit)} entries")


async def main() -> int:
    print(BAR)
    print("Bedrock smoke test")
    _row("region:", REGION)
    _row("model:", MODEL)
    print(BAR)
    print()

    steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
        ("Direct BedrockClient.converse", step_1_direct_converse),
        ("Agent.run with no tools", step_2_agent_no_tools),
        ("WorkflowEngine end-to-end", step_3_workflow_engine),
    ]

    for idx, (label, fn) in enumerate(steps, 1):
        print(f"[{idx}/{len(steps)}] {label}")
        start = time.perf_counter()
        try:
            await fn()
        except Exception as exc:
            elapsed = time.perf_counter() - start
            print(f"{INDENT}FAIL ({elapsed:.2f}s)")
            print()
            diag = _diagnose(exc)
            if diag is not None:
                cause, action = diag
                _row("cause:", cause)
                _row("action:", action)
            else:
                _row("error:", f"{type(exc).__name__}: {exc}")
            print()
            print(BAR)
            print(
                f"FAILED at step {idx}/{len(steps)}. Skipping the rest — they would fail the same way."
            )
            print(BAR)
            return 1
        elapsed = time.perf_counter() - start
        print(f"{INDENT}OK ({elapsed:.2f}s)")
        print()

    print(BAR)
    print("All checks passed. Bedrock + Agent + WorkflowEngine are wired correctly.")
    print(BAR)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
