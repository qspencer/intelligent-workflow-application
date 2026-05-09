"""MockWorld — in-memory surfaces for tests.

Agents reasoning against a MockWorld run real LLM calls (or replayed ones) but
all side effects land in dicts. Tests assert on world state after a workflow
completes: did the file get filed? was the message posted? was the row inserted?
"""

from __future__ import annotations

from typing import Any

from workflow_platform.world.base import Database, Filesystem, Messaging, World


class MockFilesystem(Filesystem):
    def __init__(self, seed: dict[str, bytes] | None = None) -> None:
        self.files: dict[str, bytes] = dict(seed) if seed else {}

    async def read_bytes(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_bytes(self, path: str, content: bytes) -> None:
        self.files[path] = content

    async def exists(self, path: str) -> bool:
        if path in self.files:
            return True
        prefix = path.rstrip("/") + "/"
        return any(p.startswith(prefix) for p in self.files)

    async def list_dir(self, path: str) -> list[str]:
        prefix = path.rstrip("/") + "/"
        names: set[str] = set()
        for p in self.files:
            if not p.startswith(prefix):
                continue
            rest = p[len(prefix) :]
            names.add(rest.split("/", 1)[0])
        return sorted(names)


class MockMessaging(Messaging):
    def __init__(self) -> None:
        self.messages: dict[str, list[str]] = {}

    async def post_message(self, channel: str, text: str) -> None:
        self.messages.setdefault(channel, []).append(text)


class MockDatabase(Database):
    def __init__(self, seed: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = (
            {k: list(v) for k, v in seed.items()} if seed else {}
        )

    async def query(self, table: str) -> list[dict[str, Any]]:
        return list(self.tables.get(table, []))

    async def insert(self, table: str, row: dict[str, Any]) -> None:
        self.tables.setdefault(table, []).append(dict(row))


def mock_world(
    *,
    files: dict[str, bytes] | None = None,
    tables: dict[str, list[dict[str, Any]]] | None = None,
) -> World:
    """Construct a MockWorld with optional seeded filesystem + database state."""
    return World(
        fs=MockFilesystem(seed=files),
        messaging=MockMessaging(),
        database=MockDatabase(seed=tables),
    )
