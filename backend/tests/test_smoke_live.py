"""Live-Bedrock smoke test, opt-in via `BEDROCK_LIVE=1`.

Mirrors the three checks in `backend/tools/smoke_live.py`. Costs a few cents
of Haiku 4.5 calls per run; never runs in CI by default.

Skipped automatically unless the env var is set — same pattern as the
Postgres-gated integration tests in `test_postgres_repositories.py`.

Usage:
    BEDROCK_LIVE=1 uv run pytest -m live
"""

from __future__ import annotations

import os

import pytest

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

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("BEDROCK_LIVE") != "1",
        reason="BEDROCK_LIVE not set; skipping live Bedrock smoke tests",
    ),
]


@pytest.fixture()
def live_bedrock() -> BedrockClient:
    return BedrockClient(mode=BedrockMode.LIVE, region=REGION)


async def test_bedrock_converse_direct(live_bedrock: BedrockClient) -> None:
    response = await live_bedrock.converse(
        model_id=MODEL,
        messages=[{"role": "user", "content": [{"text": "Reply with one word: alive."}]}],
        inference_config={"maxTokens": 20, "temperature": 0.0},
    )
    text = response["output"]["message"]["content"][0]["text"]
    usage = response["usage"]
    assert text.strip()
    assert usage["inputTokens"] > 0
    assert usage["outputTokens"] > 0


async def test_agent_run_no_tools(live_bedrock: BedrockClient) -> None:
    agent = Agent(
        system_prompt="Reply with exactly one word.",
        tools=[],
        model_id=MODEL,
        bedrock=live_bedrock,
    )
    result = await agent.run("Are you working?")
    assert result.output_text.strip()
    assert result.usage.total_tokens > 0
    assert result.usage.iterations >= 1


async def test_workflow_engine_end_to_end(live_bedrock: BedrockClient) -> None:
    repos = in_memory_repositories()
    definition = load_definition(
        {
            "id": "live-smoke",
            "name": "Live Smoke",
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
        bedrock=live_bedrock,
        world=mock_world(),
    )
    instance = await engine.run(definition, trigger_payload={"src": "live-smoke-test"})

    assert instance.state == WorkflowInstanceState.COMPLETED, instance.error
    assert instance.context["total_tokens"] > 0
    # Cost is > 0 once Claude 4.5 Haiku pricing is in MODEL_PRICING.
    assert instance.context["total_cost_usd"] > 0

    steps = await repos.steps.list_by_instance(instance.id)
    assert steps[0].output is not None
    assert steps[0].output.get("output_text", "").strip()
