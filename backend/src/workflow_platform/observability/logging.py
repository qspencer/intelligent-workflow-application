"""Structured logging — JSON formatter on stdlib `logging`.

Production / Fargate ships JSON to stdout (CloudWatch parses it). Local
development can fall back to plain text via `configure_logging(json_output=False)`.
No third-party deps; standard `logging.LogRecord` only.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

# Attribute names that exist on every LogRecord (so `extra={...}` keys are
# whatever's *not* in this set). See `logging.LogRecord.__init__`.
_LOGRECORD_BUILTINS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record on a single line.

    Includes timestamp (UTC, ISO-8601), level, logger name, and message. Any
    keys passed via `extra={...}` (e.g. `workflow_id`, `instance_id`) land
    alongside, so a downstream pipeline can filter by them.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _LOGRECORD_BUILTINS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO, *, json_output: bool = True) -> None:
    """Replace handlers on the root logger with a single stream handler at `level`.

    Idempotent — repeated calls swap formatters cleanly. Safe to call from
    main.py at startup, from a CLI entry point, or from a test fixture."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if json_output else logging.Formatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
