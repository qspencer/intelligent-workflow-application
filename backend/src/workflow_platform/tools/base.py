"""Tool interface — the contract every agent-callable capability implements.

Tools have a JSON-schema description so they can be passed to Bedrock `converse`
as `toolConfig`. The Agent reads tool metadata, lets the LLM choose tools, and
dispatches `execute(params, context)`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from workflow_platform.world import World


class ToolContext(BaseModel):
    """Per-invocation context handed to a tool.

    `world` is the I/O surface (real for production, mock for tests).
    `agent_id` lets tools attribute their work in audit logs (Phase 1).
    `workflow_instance_id` ties a tool call to the workflow that triggered it.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    world: World | None = None
    agent_id: str | None = None
    workflow_instance_id: str | None = None


class ToolResult(BaseModel):
    """Outcome of a tool invocation.

    `content` carries the success payload. `error` is set instead when the tool
    could not produce a result.
    """

    content: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Tool(ABC):
    """Base class for all tools."""

    name: ClassVar[str]
    description: ClassVar[str]
    parameters_schema: ClassVar[dict[str, Any]]

    @abstractmethod
    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
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
