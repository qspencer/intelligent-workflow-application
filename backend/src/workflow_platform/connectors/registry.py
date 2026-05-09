"""ConnectorRegistry — name → Connector lookup."""

from __future__ import annotations

from workflow_platform.connectors.base import Connector


class ConnectorRegistry:
    def __init__(self, connectors: dict[str, Connector] | None = None) -> None:
        self._items: dict[str, Connector] = dict(connectors or {})

    def register(self, name: str, connector: Connector) -> None:
        if name in self._items:
            raise ValueError(f"Connector {name!r} already registered")
        self._items[name] = connector

    def get(self, name: str) -> Connector | None:
        return self._items.get(name)

    def names(self) -> list[str]:
        return sorted(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items
