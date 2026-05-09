"""Agent-callable tools for connectors.

`connector_send` and `connector_query` give agents access to any connector
registered in the bound `ConnectorRegistry`. The tool's capability allowlist
gates *which agents* can use connectors at all; per-connector authorization
(only-finance-can-send-to-finance-slack) requires per-connector tool names —
deferred until a workload demands that granularity.
"""

from __future__ import annotations

from typing import Any, ClassVar

from workflow_platform.connectors import ConnectorRegistry
from workflow_platform.tools.base import Tool, ToolContext, ToolResult


class ConnectorSendTool(Tool):
    name: ClassVar[str] = "connector_send"
    description: ClassVar[str] = (
        "Send a payload to a registered connector (e.g. webhook POST, S3 PutObject). "
        "Provide `connector_id` (the registry name) and `payload` (connector-specific shape)."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "connector_id": {"type": "string"},
            "payload": {"type": "object"},
        },
        "required": ["connector_id", "payload"],
    }

    def __init__(self, registry: ConnectorRegistry) -> None:
        self.registry = registry

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        connector_id = params.get("connector_id")
        if not isinstance(connector_id, str) or not connector_id:
            return ToolResult(error="connector_id is required")
        connector = self.registry.get(connector_id)
        if connector is None:
            return ToolResult(error=f"Unknown connector: {connector_id!r}")
        payload = params.get("payload")
        if not isinstance(payload, dict):
            return ToolResult(error="payload must be an object")
        try:
            result = await connector.send(payload)
        except NotImplementedError as exc:
            return ToolResult(error=f"Connector {connector_id!r} does not support send: {exc}")
        except Exception as exc:
            return ToolResult(error=f"Connector send failed: {exc}")
        return ToolResult(content=result)


class ConnectorQueryTool(Tool):
    name: ClassVar[str] = "connector_query"
    description: ClassVar[str] = (
        "Query a registered connector for data (e.g. webhook GET, S3 ListObjects/GetObject). "
        "Provide `connector_id` and `params` (connector-specific shape)."
    )
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "connector_id": {"type": "string"},
            "params": {"type": "object"},
        },
        "required": ["connector_id"],
    }

    def __init__(self, registry: ConnectorRegistry) -> None:
        self.registry = registry

    async def execute(
        self, params: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        connector_id = params.get("connector_id")
        if not isinstance(connector_id, str) or not connector_id:
            return ToolResult(error="connector_id is required")
        connector = self.registry.get(connector_id)
        if connector is None:
            return ToolResult(error=f"Unknown connector: {connector_id!r}")
        query_params = params.get("params") or {}
        if not isinstance(query_params, dict):
            return ToolResult(error="params must be an object")
        try:
            result = await connector.query(query_params)
        except NotImplementedError as exc:
            return ToolResult(error=f"Connector {connector_id!r} does not support query: {exc}")
        except Exception as exc:
            return ToolResult(error=f"Connector query failed: {exc}")
        return ToolResult(content=result if isinstance(result, dict) else {"value": result})
