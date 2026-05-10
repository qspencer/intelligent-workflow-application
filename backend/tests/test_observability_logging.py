"""Tests for the JSON log formatter."""

from __future__ import annotations

import json
import logging
from typing import Any

from workflow_platform.observability import JsonFormatter, configure_logging


def _build_record(
    msg: str = "hello",
    *,
    level: int = logging.INFO,
    name: str = "test.logger",
    args: tuple[Any, ...] | None = None,
    extra: dict[str, Any] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            record.__dict__[k] = v
    return record


def _format(record: logging.LogRecord) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(JsonFormatter().format(record))
    return parsed


def test_basic_record_has_required_fields() -> None:
    payload = _format(_build_record(msg="hello world", name="my.logger"))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "my.logger"
    assert payload["msg"] == "hello world"
    assert isinstance(payload["ts"], str) and payload["ts"].endswith("+00:00")


def test_extra_keys_land_at_top_level() -> None:
    payload = _format(
        _build_record(
            msg="processed",
            extra={"workflow_id": "wf-123", "instance_id": "inst-9", "duration_seconds": 0.42},
        )
    )
    assert payload["workflow_id"] == "wf-123"
    assert payload["instance_id"] == "inst-9"
    assert payload["duration_seconds"] == 0.42


def test_format_serializes_non_string_args() -> None:
    payload = _format(_build_record(msg="x=%s", args=(42,)))
    assert payload["msg"] == "x=42"


def test_configure_logging_replaces_handlers() -> None:
    root = logging.getLogger()
    configure_logging(level=logging.WARNING)
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler.formatter, JsonFormatter)
    assert root.level == logging.WARNING
    # Idempotent — second call replaces, doesn't append.
    configure_logging(level=logging.INFO, json_output=False)
    assert len(root.handlers) == 1
    assert not isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.INFO
