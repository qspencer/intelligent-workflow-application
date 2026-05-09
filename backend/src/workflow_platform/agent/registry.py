"""ToolRegistry — name-keyed lookup of `Tool` instances.

The Agent uses a registry to resolve a tool name from a `toolUse` block back to
the `Tool` instance to dispatch. The registry also produces the Bedrock
`toolConfig` payload that's attached to every `converse` request.
"""

from __future__ import annotations

from typing import Any

from workflow_platform.tools import Tool


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool name {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def to_bedrock_tool_config(self) -> dict[str, Any] | None:
        """Return the `toolConfig` payload for Bedrock `converse`, or None if empty."""
        if not self._tools:
            return None
        return {"tools": [t.to_bedrock_tool_spec() for t in self._tools.values()]}
