"""Agent — the tool-use loop primitive.

An Agent has a system prompt, a set of tools, a model id, a `BedrockClient`,
and a policy (token budget, max iterations). `run(user_message)` enters the
tool-use loop: send the conversation to Bedrock, dispatch any tool calls,
append results, repeat until the model says `end_turn` or the policy stops it.

Token usage is tracked per call and cumulatively. Tool calls are logged for
inspection. The full conversation (`messages`) is returned with the result so
callers can audit reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from workflow_platform.agent.registry import ToolRegistry
from workflow_platform.bedrock import BedrockClient
from workflow_platform.tools import Tool, ToolContext, ToolResult


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"  # internal — never surfaced as a final stop
    MAX_ITERATIONS = "max_iterations"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UPSTREAM_MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    CONTENT_FILTERED = "content_filtered"
    GUARDRAIL_INTERVENED = "guardrail_intervened"
    ERROR = "error"


@dataclass
class AgentPolicy:
    max_iterations: int = 10
    max_total_tokens: int = 200_000
    inference_config: dict[str, Any] | None = None


class AgentUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    iterations: int = 0
    tool_calls: int = 0


class ToolCallRecord(BaseModel):
    name: str
    input: dict[str, Any]
    result: dict[str, Any]


class AgentResult(BaseModel):
    output_text: str
    stop_reason: StopReason
    usage: AgentUsage
    messages: list[dict[str, Any]]
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)


@dataclass
class Agent:
    system_prompt: str
    tools: list[Tool] | ToolRegistry
    model_id: str
    bedrock: BedrockClient
    policy: AgentPolicy = field(default_factory=AgentPolicy)

    def __post_init__(self) -> None:
        self.registry: ToolRegistry = (
            self.tools if isinstance(self.tools, ToolRegistry) else ToolRegistry(self.tools)
        )

    async def run(
        self,
        user_message: str,
        context: ToolContext | None = None,
    ) -> AgentResult:
        messages: list[dict[str, Any]] = [{"role": "user", "content": [{"text": user_message}]}]
        usage = AgentUsage()
        tool_calls: list[ToolCallRecord] = []
        last_text = ""

        for _ in range(self.policy.max_iterations):
            if usage.total_tokens > self.policy.max_total_tokens:
                return AgentResult(
                    output_text=last_text,
                    stop_reason=StopReason.BUDGET_EXHAUSTED,
                    usage=usage,
                    messages=messages,
                    tool_calls=tool_calls,
                )

            response = await self.bedrock.converse(
                model_id=self.model_id,
                messages=messages,
                system=[{"text": self.system_prompt}],
                tool_config=self.registry.to_bedrock_tool_config(),
                inference_config=self.policy.inference_config,
            )

            usage.iterations += 1
            r_usage = response.get("usage", {})
            usage.input_tokens += int(r_usage.get("inputTokens", 0))
            usage.output_tokens += int(r_usage.get("outputTokens", 0))
            usage.total_tokens = usage.input_tokens + usage.output_tokens

            assistant_message = response["output"]["message"]
            messages.append(assistant_message)
            last_text = _extract_text(assistant_message) or last_text

            raw_stop = str(response.get("stopReason", "end_turn"))

            if raw_stop == "end_turn":
                return AgentResult(
                    output_text=last_text,
                    stop_reason=StopReason.END_TURN,
                    usage=usage,
                    messages=messages,
                    tool_calls=tool_calls,
                )

            if raw_stop == "tool_use":
                tool_results = await self._execute_tool_calls(
                    assistant_message, context, tool_calls, usage
                )
                messages.append({"role": "user", "content": tool_results})
                continue

            return AgentResult(
                output_text=last_text,
                stop_reason=_coerce_stop_reason(raw_stop),
                usage=usage,
                messages=messages,
                tool_calls=tool_calls,
            )

        return AgentResult(
            output_text=last_text,
            stop_reason=StopReason.MAX_ITERATIONS,
            usage=usage,
            messages=messages,
            tool_calls=tool_calls,
        )

    async def _execute_tool_calls(
        self,
        assistant_message: dict[str, Any],
        context: ToolContext | None,
        tool_calls_log: list[ToolCallRecord],
        usage: AgentUsage,
    ) -> list[dict[str, Any]]:
        tool_results: list[dict[str, Any]] = []
        for block in assistant_message.get("content", []):
            if "toolUse" not in block:
                continue
            tu = block["toolUse"]
            tool_name = str(tu["name"])
            tool_input = dict(tu.get("input") or {})
            tool_use_id = str(tu["toolUseId"])

            result = await self._dispatch(tool_name, tool_input, context)
            usage.tool_calls += 1
            tool_calls_log.append(
                ToolCallRecord(name=tool_name, input=tool_input, result=result.model_dump())
            )

            tool_results.append(
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"json": result.model_dump()}],
                        "status": "success" if result.ok else "error",
                    }
                }
            )
        return tool_results

    async def _dispatch(
        self, name: str, params: dict[str, Any], context: ToolContext | None
    ) -> ToolResult:
        if (
            context is not None
            and context.capabilities is not None
            and not context.capabilities.tool_allowed(name)
        ):
            return ToolResult(error=f"Capability denied: tool {name!r} is not in the allowlist")
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(error=f"Unknown tool: {name}")
        try:
            return await tool.execute(params, context=context)
        except Exception as exc:
            return ToolResult(error=f"Tool execution failed: {exc}")


def _extract_text(message: dict[str, Any]) -> str:
    parts = [c["text"] for c in message.get("content", []) if isinstance(c, dict) and "text" in c]
    return "\n".join(parts)


def _coerce_stop_reason(raw: str) -> StopReason:
    try:
        return StopReason(raw)
    except ValueError:
        return StopReason.ERROR
