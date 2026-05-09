"""Tests for the Agent tool-use loop.

Uses `FakeBedrock` to drive deterministic responses. The hash/replay machinery
is exercised separately in `test_bedrock_client.py`.
"""

from __future__ import annotations

from tests._bedrock_fakes import FakeBedrock, stop_response, text_response, tool_use_response
from workflow_platform.agent import Agent, AgentPolicy, StopReason
from workflow_platform.tools import FileWriteTool, ToolContext
from workflow_platform.world import MockFilesystem, mock_world

MODEL = "anthropic.claude-3-haiku-20240307-v1:0"


async def test_no_tools_single_turn() -> None:
    fake = FakeBedrock([text_response("Hello, world!", input_tokens=12, output_tokens=4)])
    agent = Agent(system_prompt="You are helpful", tools=[], model_id=MODEL, bedrock=fake)

    result = await agent.run("Hi")

    assert result.output_text == "Hello, world!"
    assert result.stop_reason == StopReason.END_TURN
    assert result.usage.iterations == 1
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 4
    assert result.usage.total_tokens == 16
    assert result.usage.tool_calls == 0
    assert len(fake.calls) == 1
    assert fake.calls[0]["tool_config"] is None


async def test_with_tool_single_call_then_finish() -> None:
    fake = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[("call_1", "file_write", {"path": "/out.txt", "content": "abc"})],
                input_tokens=20,
                output_tokens=10,
            ),
            text_response("Done.", input_tokens=30, output_tokens=2),
        ]
    )
    world = mock_world()
    agent = Agent(
        system_prompt="You are helpful",
        tools=[FileWriteTool()],
        model_id=MODEL,
        bedrock=fake,
    )

    result = await agent.run("Write abc to /out.txt", context=ToolContext(world=world))

    assert result.stop_reason == StopReason.END_TURN
    assert result.output_text == "Done."
    assert result.usage.iterations == 2
    assert result.usage.tool_calls == 1
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "file_write"
    assert result.tool_calls[0].result["error"] is None
    fs = world.fs
    assert isinstance(fs, MockFilesystem)
    assert fs.files["/out.txt"] == b"abc"

    # The second converse call must include the tool result as a user message.
    second_call = fake.calls[1]
    assert second_call["messages"][-1]["role"] == "user"
    assert "toolResult" in second_call["messages"][-1]["content"][0]


async def test_multiple_tool_calls_in_one_turn() -> None:
    fake = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[
                    ("call_1", "file_write", {"path": "/a.txt", "content": "1"}),
                    ("call_2", "file_write", {"path": "/b.txt", "content": "2"}),
                ],
            ),
            text_response("Wrote both."),
        ]
    )
    world = mock_world()
    agent = Agent(
        system_prompt="You are helpful",
        tools=[FileWriteTool()],
        model_id=MODEL,
        bedrock=fake,
    )

    result = await agent.run("Write a and b", context=ToolContext(world=world))

    assert result.stop_reason == StopReason.END_TURN
    assert result.usage.tool_calls == 2
    fs = world.fs
    assert isinstance(fs, MockFilesystem)
    assert fs.files == {"/a.txt": b"1", "/b.txt": b"2"}


async def test_unknown_tool_returns_error_to_model() -> None:
    fake = FakeBedrock(
        [
            tool_use_response(tool_uses=[("call_1", "no_such_tool", {})]),
            text_response("Sorry, I couldn't do that."),
        ]
    )
    agent = Agent(system_prompt="x", tools=[FileWriteTool()], model_id=MODEL, bedrock=fake)

    result = await agent.run("go", context=ToolContext(world=mock_world()))

    assert result.stop_reason == StopReason.END_TURN
    assert result.tool_calls[0].result["error"] is not None
    assert "Unknown tool" in result.tool_calls[0].result["error"]
    second_call = fake.calls[1]
    tool_result_block = second_call["messages"][-1]["content"][0]["toolResult"]
    assert tool_result_block["status"] == "error"


async def test_max_iterations_exceeded() -> None:
    # Always returns tool_use → agent loops until max_iterations cap
    fake = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[("c", "file_write", {"path": f"/{i}.txt", "content": "x"})],
            )
            for i in range(20)
        ]
    )
    agent = Agent(
        system_prompt="x",
        tools=[FileWriteTool()],
        model_id=MODEL,
        bedrock=fake,
        policy=AgentPolicy(max_iterations=3),
    )

    result = await agent.run("loop", context=ToolContext(world=mock_world()))

    assert result.stop_reason == StopReason.MAX_ITERATIONS
    assert result.usage.iterations == 3


async def test_budget_exhausted_stops_loop() -> None:
    # Two tool_use turns burn 200k tokens together. Budget 150k → before turn 3 the
    # pre-call budget check fires, so the third response is never requested.
    fake = FakeBedrock(
        [
            tool_use_response(
                tool_uses=[("c1", "file_write", {"path": "/a.txt", "content": "x"})],
                input_tokens=80_000,
                output_tokens=20_000,
            ),
            tool_use_response(
                tool_uses=[("c2", "file_write", {"path": "/b.txt", "content": "y"})],
                input_tokens=80_000,
                output_tokens=20_000,
            ),
            text_response("never reached"),
        ]
    )
    agent = Agent(
        system_prompt="x",
        tools=[FileWriteTool()],
        model_id=MODEL,
        bedrock=fake,
        policy=AgentPolicy(max_total_tokens=150_000),
    )

    result = await agent.run("go", context=ToolContext(world=mock_world()))

    assert result.stop_reason == StopReason.BUDGET_EXHAUSTED
    assert len(fake.calls) == 2
    assert result.usage.total_tokens == 200_000


async def test_upstream_max_tokens_surfaces_as_stop() -> None:
    fake = FakeBedrock([stop_response("max_tokens", text="partial answer")])
    agent = Agent(system_prompt="x", tools=[], model_id=MODEL, bedrock=fake)

    result = await agent.run("go")

    assert result.stop_reason == StopReason.UPSTREAM_MAX_TOKENS
    assert result.output_text == "partial answer"


async def test_unknown_stop_reason_becomes_error() -> None:
    fake = FakeBedrock([stop_response("something_invented_by_aws_tomorrow")])
    agent = Agent(system_prompt="x", tools=[], model_id=MODEL, bedrock=fake)

    result = await agent.run("go")

    assert result.stop_reason == StopReason.ERROR


async def test_request_includes_system_and_tool_config() -> None:
    fake = FakeBedrock([text_response("hi")])
    agent = Agent(
        system_prompt="be brief",
        tools=[FileWriteTool()],
        model_id=MODEL,
        bedrock=fake,
        policy=AgentPolicy(inference_config={"temperature": 0.2}),
    )

    await agent.run("hello")

    call = fake.calls[0]
    assert call["system"] == [{"text": "be brief"}]
    assert call["tool_config"] is not None
    assert call["tool_config"]["tools"][0]["toolSpec"]["name"] == "file_write"
    assert call["inference_config"] == {"temperature": 0.2}
