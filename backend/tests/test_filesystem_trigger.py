"""Tests for FilesystemTrigger.

The handler-level filtering logic is unit-tested directly. One slower test
exercises the full watchdog + asyncio bridge end-to-end.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from workflow_platform.triggers import FilesystemTrigger


def test_handler_dispatches_only_on_file_creation_matching_pattern() -> None:
    from workflow_platform.triggers.filesystem import _DispatchingHandler

    received: list[str] = []
    handler = _DispatchingHandler("*.pdf", received.append)

    pdf_event = MagicMock(is_directory=False, src_path="/x/invoice.pdf")
    txt_event = MagicMock(is_directory=False, src_path="/x/notes.txt")
    dir_event = MagicMock(is_directory=True, src_path="/x/subdir")

    handler.on_created(pdf_event)
    handler.on_created(txt_event)
    handler.on_created(dir_event)

    assert received == ["/x/invoice.pdf"]


async def test_filesystem_trigger_end_to_end(tmp_path: Path) -> None:
    trigger = FilesystemTrigger(folder=tmp_path, pattern="*.pdf")
    received: list[dict[str, Any]] = []
    queued = asyncio.Event()

    async def on_event(payload: dict[str, Any]) -> None:
        received.append(payload)
        queued.set()

    await trigger.start(on_event)
    try:
        target = tmp_path / "drop.pdf"
        target.write_bytes(b"%PDF-1.7\n")
        # Wait briefly for watchdog to notice + dispatch.
        await asyncio.wait_for(queued.wait(), timeout=5.0)
    finally:
        await trigger.stop()

    assert len(received) == 1
    assert Path(received[0]["file_path"]).name == "drop.pdf"


async def test_filesystem_trigger_creates_missing_folder(tmp_path: Path) -> None:
    target = tmp_path / "watch_me"
    assert not target.exists()
    trigger = FilesystemTrigger(folder=target, pattern="*")

    async def on_event(_: dict[str, Any]) -> None:
        return None

    await trigger.start(on_event)
    try:
        assert target.is_dir()
    finally:
        await trigger.stop()


async def test_filesystem_trigger_stop_is_idempotent(tmp_path: Path) -> None:
    trigger = FilesystemTrigger(folder=tmp_path)
    await trigger.stop()  # never started

    async def on_event(_: dict[str, Any]) -> None:
        return None

    await trigger.start(on_event)
    await trigger.stop()
    await trigger.stop()  # second stop is fine
