"""RealWorld — surfaces that delegate to real systems.

Filesystem operations are real. Messaging and Database raise `NotImplementedError`
until the connector framework lands in Phase 2 / Week 7; tools that need them
will route through specific connectors (Slack, Postgres, etc.) at that point.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from workflow_platform.world.base import Database, Filesystem, Messaging, World


class RealFilesystem(Filesystem):
    async def read_bytes(self, path: str) -> bytes:
        return await asyncio.to_thread(Path(path).read_bytes)

    async def write_bytes(self, path: str, content: bytes) -> None:
        def _write() -> None:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)

        await asyncio.to_thread(_write)

    async def exists(self, path: str) -> bool:
        return await asyncio.to_thread(Path(path).exists)

    async def list_dir(self, path: str) -> list[str]:
        return await asyncio.to_thread(lambda: sorted(p.name for p in Path(path).iterdir()))


class _NotYetImplemented(Messaging, Database):
    """Both messaging and database land with the connector framework. Until then,
    any RealWorld code path that touches them is a programming error."""

    async def post_message(self, channel: str, text: str) -> None:
        raise NotImplementedError(
            "RealMessaging requires the connector framework (Phase 2 / Week 7). "
            "Use MockWorld for tests; defer real messaging until connectors land."
        )

    async def query(self, table: str) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "RealDatabase requires the connector framework (Phase 2 / Week 7). "
            "Use MockWorld for tests; defer real database access until connectors land."
        )

    async def insert(self, table: str, row: dict[str, Any]) -> None:
        raise NotImplementedError(
            "RealDatabase requires the connector framework (Phase 2 / Week 7). "
            "Use MockWorld for tests; defer real database access until connectors land."
        )


def real_world() -> World:
    """Construct a World wired to real I/O. Messaging + database are stubs."""
    stub = _NotYetImplemented()
    return World(fs=RealFilesystem(), messaging=stub, database=stub)
