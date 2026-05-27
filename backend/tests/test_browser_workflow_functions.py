"""Tests for `filter_rows_by_date` + `write_csv` stock step functions.

Both are deterministic step functions used by the RPA Challenge OCR
workflow. They run without any LLM dependency and can be exercised
directly against a synthetic `WorkflowContext`.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from workflow_platform.engine import StepFailure, WorkflowContext
from workflow_platform.engine.functions import filter_rows_by_date, write_csv
from workflow_platform.world import mock_world


def _ctx(steps: dict[str, dict[str, Any]] | None = None) -> WorkflowContext:
    return WorkflowContext(
        instance_id="i",
        workflow_id="w",
        trigger={},
        steps=dict(steps or {}),
    )


# ---------- filter_rows_by_date ----------


async def test_filter_rows_by_date_keeps_on_or_before_today() -> None:
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    ctx = _ctx(
        {
            "read_table": {
                "output_text": (
                    f'[{{"id":"a","d":"{yesterday}"}}, '
                    f'{{"id":"b","d":"{today}"}}, '
                    f'{{"id":"c","d":"{tomorrow}"}}]'
                )
            }
        }
    )
    out = await filter_rows_by_date(
        {
            "rows_from": "steps.read_table.output_text",
            "date_field": "d",
            "cutoff": "today",
            "comparison": "on_or_before",
        },
        ctx,
        mock_world(),
    )
    assert out["kept_count"] == 2
    assert [r["id"] for r in out["kept_rows"]] == ["a", "b"]
    assert [r["id"] for r in out["dropped_rows"]] == ["c"]
    assert out["unparseable_count"] == 0


async def test_filter_rows_by_date_supports_iso_cutoff() -> None:
    ctx = _ctx(
        {
            "table": {
                "output_text": (
                    '[{"id":"a","d":"2024-01-01"},'
                    ' {"id":"b","d":"2024-06-15"},'
                    ' {"id":"c","d":"2024-12-31"}]'
                )
            }
        }
    )
    out = await filter_rows_by_date(
        {
            "rows_from": "steps.table.output_text",
            "date_field": "d",
            "cutoff": "2024-06-15",
            "comparison": "before",
        },
        ctx,
        mock_world(),
    )
    assert [r["id"] for r in out["kept_rows"]] == ["a"]


async def test_filter_rows_by_date_after_inclusive_variants() -> None:
    ctx = _ctx(
        {
            "t": {
                "output_text": (
                    '[{"id":"a","d":"2024-01-01"},'
                    ' {"id":"b","d":"2024-06-15"},'
                    ' {"id":"c","d":"2024-12-31"}]'
                )
            }
        }
    )
    after = await filter_rows_by_date(
        {
            "rows_from": "steps.t.output_text",
            "date_field": "d",
            "cutoff": "2024-06-15",
            "comparison": "after",
        },
        ctx,
        mock_world(),
    )
    on_or_after = await filter_rows_by_date(
        {
            "rows_from": "steps.t.output_text",
            "date_field": "d",
            "cutoff": "2024-06-15",
            "comparison": "on_or_after",
        },
        ctx,
        mock_world(),
    )
    assert [r["id"] for r in after["kept_rows"]] == ["c"]
    assert [r["id"] for r in on_or_after["kept_rows"]] == ["b", "c"]


async def test_filter_rows_by_date_handles_unparseable_dates() -> None:
    ctx = _ctx(
        {
            "t": {
                "output_text": (
                    '[{"id":"a","d":"2024-01-01"}, {"id":"b","d":"not a date"}, {"id":"c","d":""}]'
                )
            }
        }
    )
    out = await filter_rows_by_date(
        {
            "rows_from": "steps.t.output_text",
            "date_field": "d",
            "cutoff": "2030-01-01",
            "comparison": "on_or_before",
        },
        ctx,
        mock_world(),
    )
    assert out["kept_count"] == 1
    assert out["unparseable_count"] == 2


async def test_filter_rows_by_date_supports_inline_list() -> None:
    ctx = _ctx()
    out = await filter_rows_by_date(
        {
            "rows_from": [
                {"id": "x", "d": "2024-01-01"},
                {"id": "y", "d": "2024-12-31"},
            ],
            "date_field": "d",
            "cutoff": "2024-06-01",
            "comparison": "on_or_before",
        },
        ctx,
        mock_world(),
    )
    assert [r["id"] for r in out["kept_rows"]] == ["x"]


async def test_filter_rows_by_date_strips_json_code_fences() -> None:
    """Agents sometimes wrap JSON in ```json ... ``` despite the rubric.
    The helper should peel that off rather than fail to parse."""
    ctx = _ctx(
        {
            "t": {"output_text": '```json\n[{"id":"a","d":"2024-01-01"}]\n```'},
        }
    )
    out = await filter_rows_by_date(
        {
            "rows_from": "steps.t.output_text",
            "date_field": "d",
            "cutoff": "2030-01-01",
            "comparison": "on_or_before",
        },
        ctx,
        mock_world(),
    )
    assert out["kept_count"] == 1


async def test_filter_rows_by_date_supports_multiple_date_formats() -> None:
    ctx = _ctx(
        {
            "t": {
                "output_text": (
                    '[{"id":"iso","d":"2024-01-01"},'
                    ' {"id":"slash","d":"01/01/2024"},'
                    ' {"id":"month","d":"Jan 1, 2024"}]'
                )
            }
        }
    )
    out = await filter_rows_by_date(
        {
            "rows_from": "steps.t.output_text",
            "date_field": "d",
            "cutoff": "2024-12-31",
            "comparison": "on_or_before",
        },
        ctx,
        mock_world(),
    )
    assert out["kept_count"] == 3


async def test_filter_rows_by_date_rejects_invalid_cutoff() -> None:
    with pytest.raises(StepFailure, match="cutoff"):
        await filter_rows_by_date(
            {
                "rows_from": "steps.t.output_text",
                "date_field": "d",
                "cutoff": "yesterday-ish",
                "comparison": "before",
            },
            _ctx({"t": {"output_text": "[]"}}),
            mock_world(),
        )


async def test_filter_rows_by_date_rejects_invalid_comparison() -> None:
    with pytest.raises(StepFailure, match="comparison"):
        await filter_rows_by_date(
            {
                "rows_from": [],
                "date_field": "d",
                "cutoff": "today",
                "comparison": "kinda-on-or-before",
            },
            _ctx(),
            mock_world(),
        )


# ---------- write_csv ----------


async def test_write_csv_emits_header_and_rows(tmp_path: Path) -> None:
    world = mock_world()
    ctx = _ctx({"extract": {"output_text": ('[{"a":"1","b":"two"}, {"a":"3","b":"four"}]')}})
    out_path = str(tmp_path / "out.csv")
    out = await write_csv(
        {
            "rows_from": "steps.extract.output_text",
            "path": out_path,
            "columns": ["a", "b"],
        },
        ctx,
        world,
    )
    assert out == {"path": out_path, "row_count": 2, "column_count": 2}

    written = await world.fs.read_bytes(out_path)
    parsed = list(csv.reader(written.decode().splitlines()))
    assert parsed == [["a", "b"], ["1", "two"], ["3", "four"]]


async def test_write_csv_normalizes_missing_keys_to_empty_strings(tmp_path: Path) -> None:
    world = mock_world()
    ctx = _ctx(
        {
            "extract": {"output_text": '[{"a":"1"},{"b":"two"}]'},
        }
    )
    out_path = str(tmp_path / "out.csv")
    await write_csv(
        {
            "rows_from": "steps.extract.output_text",
            "path": out_path,
            "columns": ["a", "b"],
        },
        ctx,
        world,
    )
    text = (await world.fs.read_bytes(out_path)).decode()
    rows = list(csv.reader(text.splitlines()))
    assert rows == [["a", "b"], ["1", ""], ["", "two"]]


async def test_write_csv_ignores_extra_keys_in_rows(tmp_path: Path) -> None:
    world = mock_world()
    ctx = _ctx({"e": {"output_text": '[{"a":"1","b":"2","z":"ignored"}]'}})
    out_path = str(tmp_path / "out.csv")
    await write_csv(
        {
            "rows_from": "steps.e.output_text",
            "path": out_path,
            "columns": ["a", "b"],
        },
        ctx,
        world,
    )
    text = (await world.fs.read_bytes(out_path)).decode()
    assert "ignored" not in text


async def test_write_csv_requires_path_and_columns() -> None:
    with pytest.raises(StepFailure, match="path"):
        await write_csv(
            {"rows_from": [], "columns": ["a"]},
            _ctx(),
            mock_world(),
        )
    with pytest.raises(StepFailure, match="columns"):
        await write_csv(
            {"rows_from": [], "path": "/tmp/x.csv"},
            _ctx(),
            mock_world(),
        )


async def test_write_csv_empty_rows_writes_header_only(tmp_path: Path) -> None:
    world = mock_world()
    out_path = str(tmp_path / "empty.csv")
    out = await write_csv(
        {
            "rows_from": [],
            "path": out_path,
            "columns": ["a", "b", "c"],
        },
        _ctx(),
        world,
    )
    assert out == {"path": out_path, "row_count": 0, "column_count": 3}
    text = (await world.fs.read_bytes(out_path)).decode()
    assert text.strip() == "a,b,c"


# ---------- rows_from resolution edge cases ----------


async def test_filter_rows_by_date_fails_clearly_on_missing_path() -> None:
    with pytest.raises(StepFailure, match="not present"):
        await filter_rows_by_date(
            {
                "rows_from": "steps.never_ran.output_text",
                "date_field": "d",
                "cutoff": "today",
            },
            _ctx(),
            mock_world(),
        )


async def test_filter_rows_by_date_fails_clearly_on_unparseable_string() -> None:
    """A context value that's a string but not valid JSON surfaces clearly."""
    with pytest.raises(StepFailure, match="not valid JSON"):
        await filter_rows_by_date(
            {
                "rows_from": "trigger.x",
                "date_field": "d",
                "cutoff": "today",
            },
            WorkflowContext(instance_id="i", workflow_id="w", trigger={"x": "not a list"}),
            mock_world(),
        )


async def test_filter_rows_by_date_fails_on_non_list_json() -> None:
    """JSON string that parses but isn't a list (e.g. a dict)."""
    with pytest.raises(StepFailure, match="want list"):
        await filter_rows_by_date(
            {
                "rows_from": "trigger.x",
                "date_field": "d",
                "cutoff": "today",
            },
            WorkflowContext(instance_id="i", workflow_id="w", trigger={"x": '{"not": "a list"}'}),
            mock_world(),
        )


