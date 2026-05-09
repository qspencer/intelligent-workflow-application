"""End-to-end smoke test against real Bedrock.

Three checks of increasing scope:

1. BedrockClient.converse direct call (validates SDK + auth + model access).
2. Agent.run with no tools (validates the tool-use loop minus tools).
3. WorkflowEngine running an agentic step (validates the full stack:
   engine → agent → bedrock → cost attribution).

Spends a few cents of Haiku calls. Useful exactly once after a big change to
the wrapper or a new region/account.

Usage:
    cd backend && uv run python tools/smoke_live.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

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

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


async def step_1_direct_converse() -> None:
    print("\n=== Step 1: direct BedrockClient.converse ===")
    bedrock = BedrockClient(mode=BedrockMode.LIVE, region="us-east-1")
    start = time.perf_counter()
    response = await bedrock.converse(
        model_id=MODEL,
        messages=[{"role": "user", "content": [{"text": "Reply with one word: alive."}]}],
        inference_config={"maxTokens": 20, "temperature": 0.0},
    )
    elapsed = time.perf_counter() - start
    text = response["output"]["message"]["content"][0]["text"]
    usage = response["usage"]
    print(f"  reply ({elapsed:.2f}s): {text!r}")
    print(f"  usage: input={usage['inputTokens']} output={usage['outputTokens']}")


async def step_2_agent_no_tools() -> None:
    print("\n=== Step 2: Agent.run with no tools ===")
    from workflow_platform.agent import Agent

    agent = Agent(
        system_prompt="Reply with exactly one word.",
        tools=[],
        model_id=MODEL,
        bedrock=BedrockClient(mode=BedrockMode.LIVE, region="us-east-1"),
    )
    start = time.perf_counter()
    result = await agent.run("Are you working?")
    elapsed = time.perf_counter() - start
    print(f"  output ({elapsed:.2f}s): {result.output_text!r}")
    print(f"  stop_reason: {result.stop_reason.value}")
    print(f"  usage: tokens={result.usage.total_tokens} iterations={result.usage.iterations}")


async def step_3_workflow_engine() -> None:
    print("\n=== Step 3: WorkflowEngine end-to-end ===")
    repos = in_memory_repositories()
    bedrock = BedrockClient(mode=BedrockMode.LIVE, region="us-east-1")

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
    start = time.perf_counter()
    instance = await engine.run(definition, trigger_payload={"src": "smoke-test"})
    elapsed = time.perf_counter() - start

    print(f"  instance: {instance.id}  state: {instance.state.value} ({elapsed:.2f}s)")
    if instance.state != WorkflowInstanceState.COMPLETED:
        print(f"  ERROR: {instance.error}")
        sys.exit(1)
    print(f"  total tokens: {instance.context['total_tokens']}")
    print(f"  total cost USD: {instance.context['total_cost_usd']:.6f}")

    steps = await repos.steps.list_by_instance(instance.id)
    print(f"  step output_text: {steps[0].output['output_text']!r}")  # type: ignore[index]
    audit = await repos.audit.list_by_instance(instance.id)
    print(f"  audit entries: {[e.action for e in audit]}")


async def main() -> None:
    print(f"BedrockClient → Bedrock @ us-east-1, model = {MODEL}")
    await step_1_direct_converse()
    await step_2_agent_no_tools()
    await step_3_workflow_engine()
    print("\nAll three checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
