"""Tests for the World abstraction (real + mock)."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow_platform.world import (
    MockDatabase,
    MockFilesystem,
    MockMessaging,
    mock_world,
    real_world,
)

# --- MockFilesystem ---


async def test_mock_fs_round_trip() -> None:
    fs = MockFilesystem()
    await fs.write_text("/a/b.txt", "hello")
    assert await fs.read_text("/a/b.txt") == "hello"
    assert await fs.exists("/a/b.txt")


async def test_mock_fs_read_missing_raises() -> None:
    fs = MockFilesystem()
    with pytest.raises(FileNotFoundError):
        await fs.read_bytes("/nope")


async def test_mock_fs_seed_and_list_dir() -> None:
    fs = MockFilesystem(seed={"/data/a.txt": b"1", "/data/sub/b.txt": b"2", "/data/c.txt": b"3"})
    assert await fs.list_dir("/data") == ["a.txt", "c.txt", "sub"]


async def test_mock_fs_exists_treats_directories() -> None:
    fs = MockFilesystem(seed={"/data/a.txt": b"x"})
    assert await fs.exists("/data")
    assert not await fs.exists("/missing")


# --- MockMessaging + MockDatabase ---


async def test_mock_messaging_records_posts() -> None:
    m = MockMessaging()
    await m.post_message("#finance", "hello")
    await m.post_message("#finance", "world")
    assert m.messages == {"#finance": ["hello", "world"]}


async def test_mock_database_seed_query_insert() -> None:
    db = MockDatabase(seed={"vendors": [{"id": 1, "name": "Acme"}]})
    assert await db.query("vendors") == [{"id": 1, "name": "Acme"}]
    await db.insert("vendors", {"id": 2, "name": "Beta"})
    rows = await db.query("vendors")
    assert {r["id"] for r in rows} == {1, 2}


# --- mock_world / real_world factories ---


async def test_mock_world_factory_seeds_state() -> None:
    world = mock_world(files={"/x.txt": b"hi"}, tables={"t": [{"k": 1}]})
    assert await world.fs.read_bytes("/x.txt") == b"hi"
    assert await world.database.query("t") == [{"k": 1}]


async def test_real_world_filesystem_round_trip(tmp_path: Path) -> None:
    world = real_world()
    target = tmp_path / "nested" / "f.txt"
    await world.fs.write_text(str(target), "real")
    assert await world.fs.exists(str(target))
    assert await world.fs.read_text(str(target)) == "real"
    assert await world.fs.list_dir(str(tmp_path / "nested")) == ["f.txt"]


async def test_real_world_messaging_and_db_are_unimplemented() -> None:
    world = real_world()
    with pytest.raises(NotImplementedError, match="connector framework"):
        await world.messaging.post_message("#x", "y")
    with pytest.raises(NotImplementedError, match="connector framework"):
        await world.database.query("t")
