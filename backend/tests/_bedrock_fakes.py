"""Test helpers for Agent tests.

`FakeBedrock` is a `BedrockClient` whose `converse` returns a pre-queued list of
responses in order, captures the requests, and never makes a real call. Used to
drive the Agent's tool-use loop deterministically.

The hash-based record/replay machinery is exercised separately in
`test_bedrock_client.py`. Agent tests focus on loop semantics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflow_platform.bedrock import BedrockClient, BedrockMode


class FakeBedrock(BedrockClient):
    """BedrockClient stand-in returning pre-queued responses."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(mode=BedrockMode.REPLAY, recordings_dir=Path("/tmp/fake-bedrock"))
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def converse(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        system: list[dict[str, Any]] | None = None,
        tool_config: dict[str, Any] | None = None,
        inference_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "model_id": model_id,
                "messages": list(messages),
                "system": system,
                "tool_config": tool_config,
                "inference_config": inference_config,
            }
        )
        if not self.responses:
            raise AssertionError("FakeBedrock ran out of queued responses")
        return self.responses.pop(0)


def text_response(text: str, *, input_tokens: int = 10, output_tokens: int = 5) -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": input_tokens + output_tokens,
        },
    }


def tool_use_response(
    *,
    tool_uses: list[tuple[str, str, dict[str, Any]]],
    text: str = "",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> dict[str, Any]:
    """Construct a `tool_use` response. Each tuple is (tool_use_id, name, input)."""
    content: list[dict[str, Any]] = []
    if text:
        content.append({"text": text})
    for tool_use_id, name, inp in tool_uses:
        content.append({"toolUse": {"toolUseId": tool_use_id, "name": name, "input": inp}})
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": "tool_use",
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": input_tokens + output_tokens,
        },
    }


def stop_response(reason: str, *, text: str = "") -> dict[str, Any]:
    """Construct a non-tool-use, non-end-turn response (max_tokens, content_filtered, ...)."""
    content = [{"text": text}] if text else []
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": reason,
        "usage": {"inputTokens": 5, "outputTokens": 0, "totalTokens": 5},
    }
