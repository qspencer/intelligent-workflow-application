"""Filesystem tools — read and write files via the World abstraction.

These are intentionally thin: they exist so an Agent can be exercised end-to-end
against a `MockWorld` in tests. Production workflows will route filesystem
access through connectors (S3, SharePoint, Drive) once those land in Phase 2.
"""

from __future__ import annotations

from typing import Any, ClassVar

from workflow_platform.tools.base import Tool, ToolContext, ToolResult


class FileReadTool(Tool):
    name: ClassVar[str] = "file_read"
    description: ClassVar[str] = (
        "Read a UTF-8 text file from the agent's world. Returns the file contents "
        "as a string. Errors if the file does not exist."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the file to read."}},
        "required": ["path"],
    }

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        path = params.get("path")
        if not isinstance(path, str) or not path:
            return ToolResult(error="path is required")
        if context is None or context.world is None:
            return ToolResult(error="world unavailable")
        try:
            text = await context.world.fs.read_text(path)
        except FileNotFoundError:
            return ToolResult(error=f"File not found: {path}")
        except Exception as exc:
            return ToolResult(error=f"Read failed: {exc}")
        return ToolResult(content={"path": path, "text": text})


class FileWriteTool(Tool):
    name: ClassVar[str] = "file_write"
    description: ClassVar[str] = (
        "Write UTF-8 text to a file in the agent's world. Overwrites existing content."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to write to."},
            "content": {"type": "string", "description": "Text content to write."},
        },
        "required": ["path", "content"],
    }

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        path = params.get("path")
        content = params.get("content")
        if not isinstance(path, str) or not path:
            return ToolResult(error="path is required")
        if not isinstance(content, str):
            return ToolResult(error="content is required and must be a string")
        if context is None or context.world is None:
            return ToolResult(error="world unavailable")
        try:
            await context.world.fs.write_text(path, content)
        except Exception as exc:
            return ToolResult(error=f"Write failed: {exc}")
        return ToolResult(content={"path": path, "bytes_written": len(content.encode("utf-8"))})
