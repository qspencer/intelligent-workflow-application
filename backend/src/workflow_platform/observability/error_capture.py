"""Dev-only in-process capture of ERROR-level log records.

A build-time aid: a ``logging.Handler`` intercepts records at ``>= ERROR``,
deduplicates them by fingerprint, and keeps the most recent N in a bounded,
in-memory ring buffer. The dashboard header polls these so backend errors
(tracebacks, connector failures, trigger problems) are visible without tailing
the terminal.

Not for production: it holds formatted tracebacks in memory and is wired only
when ``AUTH_MODE=dev`` (see ``main.create_app``). Process-local; resets on
restart. Thread-safe because ``emit`` can run on worker threads (blocking SDK
calls go through ``asyncio.to_thread``).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any


def _fingerprint(logger: str, level: str, message: str, traceback: str | None) -> str:
    # The traceback's last line (exception type + message) discriminates
    # same-message errors with different causes; the full traceback would
    # over-split on volatile line numbers / addresses.
    exc_tail = traceback.strip().splitlines()[-1] if traceback else ""
    raw = f"{logger}|{level}|{message}|{exc_tail}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


class ErrorBuffer:
    """Bounded, deduplicated store of recent error records.

    Keyed by fingerprint and ordered by last-seen (most recent last). Repeated
    occurrences increment a count instead of adding rows, so a once-a-minute
    failure shows as one entry with a rising count rather than endless noise.
    """

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def record(
        self,
        *,
        level: str,
        logger: str,
        message: str,
        traceback: str | None,
        when: datetime,
    ) -> None:
        fp = _fingerprint(logger, level, message, traceback)
        ts = when.isoformat()
        with self._lock:
            existing = self._entries.get(fp)
            if existing is not None:
                existing["count"] += 1
                existing["last_seen"] = ts
                self._entries.move_to_end(fp)
                return
            self._entries[fp] = {
                "fingerprint": fp,
                "level": level,
                "logger": logger,
                "message": message,
                "traceback": traceback,
                "count": 1,
                "first_seen": ts,
                "last_seen": ts,
            }
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)

    def snapshot(self) -> list[dict[str, Any]]:
        """Distinct errors, most-recent-first."""
        with self._lock:
            return [dict(e) for e in reversed(self._entries.values())]

    def total(self) -> int:
        """Total occurrences across all distinct errors."""
        with self._lock:
            return int(sum(e["count"] for e in self._entries.values()))

    def distinct(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class ErrorCaptureHandler(logging.Handler):
    """Routes records at ``>= level`` into an :class:`ErrorBuffer`. Attach to
    the root logger at ``ERROR``."""

    def __init__(self, buffer: ErrorBuffer, level: int = logging.ERROR) -> None:
        super().__init__(level=level)
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            traceback = (
                logging.Formatter().formatException(record.exc_info) if record.exc_info else None
            )
            self._buffer.record(
                level=record.levelname,
                logger=record.name,
                message=record.getMessage(),
                traceback=traceback,
                when=datetime.fromtimestamp(record.created, tz=UTC),
            )
        except Exception:
            # A logging handler must never raise — fall back to stderr.
            self.handleError(record)
