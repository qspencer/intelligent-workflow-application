"""FilesystemTrigger — fires when a matching file appears in a watched folder."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from fnmatch import fnmatch
from pathlib import Path
from typing import ClassVar

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from workflow_platform.triggers.base import Trigger, TriggerCallback


class FilesystemTrigger(Trigger):
    type: ClassVar[str] = "file_watch"

    def __init__(self, folder: str | Path, pattern: str = "*", recursive: bool = False) -> None:
        self.folder = Path(folder)
        self.pattern = pattern
        self.recursive = recursive
        self._observer: BaseObserver | None = None
        self._on_event: TriggerCallback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self, on_event: TriggerCallback) -> None:
        if self._observer is not None:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        self._on_event = on_event
        self._loop = asyncio.get_running_loop()
        observer = Observer()
        observer.schedule(self._handler(), str(self.folder), recursive=self.recursive)
        observer.start()
        self._observer = observer

    async def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=5.0)
        self._observer = None

    def _handler(self) -> FileSystemEventHandler:
        return _DispatchingHandler(self.pattern, self._dispatch)

    def _dispatch(self, file_path: str) -> None:
        loop = self._loop
        callback = self._on_event
        if loop is None or callback is None:
            return

        async def _invoke() -> None:
            await callback({"file_path": file_path})

        asyncio.run_coroutine_threadsafe(_invoke(), loop)


class _DispatchingHandler(FileSystemEventHandler):
    def __init__(self, pattern: str, dispatch: Callable[[str], None]) -> None:
        self.pattern = pattern
        self._dispatch = dispatch

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if not fnmatch(path.name, self.pattern):
            return
        self._dispatch(str(path))


# Synchronous variant of the callback shape used in unit tests of the handler logic.
_SyncCallback = Callable[[dict[str, object]], Awaitable[None]]
