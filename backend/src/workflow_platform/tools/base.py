"""Tool interface — the contract every agent-callable capability implements.

Tools have a JSON-schema description so they can be passed to Bedrock `converse`
as `toolConfig`. The agent loop (built next week) reads tool metadata, lets the
LLM choose tools, and dispatches `execute(params)`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel


class ToolResult(BaseModel):
    """Outcome of a tool invocation.

    `content` carries the success payload (any JSON-serializable value).
    `error` is set instead when the tool could not produce a result.
    """

    content: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Tool(ABC):
    """Base class for all tools.

    Subclasses set the three class attributes and implement `execute`.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    parameters_schema: ClassVar[dict[str, Any]]

    @abstractmethod
    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Run the tool with validated parameters."""

    def to_bedrock_tool_spec(self) -> dict[str, Any]:
        """Render in the shape Bedrock `converse` expects under `toolConfig.tools`."""
        return {
            "toolSpec": {
                "name": self.name,
                "description": self.description,
                "inputSchema": {"json": self.parameters_schema},
            }
        }
