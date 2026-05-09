"""Tests for FileReadTool and FileWriteTool against MockWorld."""

from __future__ import annotations

from workflow_platform.tools import FileReadTool, FileWriteTool, ToolContext
from workflow_platform.world import MockFilesystem, mock_world


async def test_file_read_returns_content() -> None:
    world = mock_world(files={"/a.txt": b"hello"})
    ctx = ToolContext(world=world)
    result = await FileReadTool().execute({"path": "/a.txt"}, context=ctx)
    assert result.ok
    assert result.content == {"path": "/a.txt", "text": "hello"}


async def test_file_read_missing() -> None:
    world = mock_world()
    ctx = ToolContext(world=world)
    result = await FileReadTool().execute({"path": "/nope"}, context=ctx)
    assert not result.ok
    assert result.error is not None
    assert "not found" in result.error.lower()


async def test_file_read_requires_path() -> None:
    ctx = ToolContext(world=mock_world())
    result = await FileReadTool().execute({}, context=ctx)
    assert not result.ok
    assert result.error is not None
    assert "path" in result.error.lower()


async def test_file_read_requires_world() -> None:
    result = await FileReadTool().execute({"path": "/a.txt"})
    assert not result.ok
    assert result.error is not None
    assert "world" in result.error.lower()


async def test_file_write_persists_to_world() -> None:
    world = mock_world()
    ctx = ToolContext(world=world)
    result = await FileWriteTool().execute({"path": "/x.txt", "content": "abc"}, context=ctx)
    assert result.ok
    assert result.content == {"path": "/x.txt", "bytes_written": 3}
    fs = world.fs
    assert isinstance(fs, MockFilesystem)
    assert fs.files["/x.txt"] == b"abc"


async def test_file_write_validates_inputs() -> None:
    ctx = ToolContext(world=mock_world())
    bad_path = await FileWriteTool().execute({"content": "x"}, context=ctx)
    assert not bad_path.ok
    bad_content = await FileWriteTool().execute({"path": "/a", "content": 5}, context=ctx)
    assert not bad_content.ok
