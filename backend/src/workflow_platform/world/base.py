"""World abstraction — the I/O surface tools and connectors operate on.

Production code uses a `RealWorld` whose surfaces delegate to real systems
(filesystem, message bus, database). Tests use a `MockWorld` that holds the
same surfaces over in-memory state. Tools and connectors are written against
the interfaces and never know which they got.

For Week 2, Filesystem has the substantive operations — it's what the first
agent-callable tools use. Messaging and Database are present as stubs so the
shape is established before connectors arrive in Phase 2 / Week 7. Their real
implementations raise `NotImplementedError` until then.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class Filesystem(ABC):
    @abstractmethod
    async def read_bytes(self, path: str) -> bytes:
        """Read the full contents of `path` as bytes."""

    @abstractmethod
    async def write_bytes(self, path: str, content: bytes) -> None:
        """Write `content` to `path`, creating parent directories as needed."""

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Whether `path` refers to an existing file or directory."""

    @abstractmethod
    async def list_dir(self, path: str) -> list[str]:
        """Names of entries directly under `path`."""

    async def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return (await self.read_bytes(path)).decode(encoding)

    async def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        await self.write_bytes(path, content.encode(encoding))


class Messaging(ABC):
    @abstractmethod
    async def post_message(self, channel: str, text: str) -> None:
        """Post a message to a named channel (e.g. Slack #finance)."""


class Database(ABC):
    @abstractmethod
    async def query(self, table: str) -> list[dict[str, Any]]:
        """Return all rows from `table`."""

    @abstractmethod
    async def insert(self, table: str, row: dict[str, Any]) -> None:
        """Append `row` to `table`."""


@dataclass
class World:
    """Bundle of I/O surfaces a tool or connector might use."""

    fs: Filesystem
    messaging: Messaging
    database: Database
