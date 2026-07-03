"""Tests for `extract_archive` and the dmarc-ingest example workflow.

The workflow is fully deterministic (no agentic steps): the email trigger
spools DMARC report attachments to disk and puts their paths on the payload;
`extract_archive` unpacks the .zip / .xml.gz files into the dmarc-viewer's
watched directory. Tests run against `mock_world()` — no Gmail, no Bedrock.
"""

from __future__ import annotations

import gzip
import io
import zipfile
from pathlib import Path

import pytest

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine import (
    ToolCatalog,
    WorkflowEngine,
    default_function_registry,
)
from workflow_platform.engine.context import WorkflowContext
from workflow_platform.engine.functions import copy_files, extract_archive
from workflow_platform.engine.registry import StepFailure
from workflow_platform.persistence import WorkflowInstanceState, in_memory_repositories
from workflow_platform.workflow import load_definition_from_yaml
from workflow_platform.world import mock_world

EXAMPLES_ROOT = Path(__file__).resolve().parent.parent.parent / "examples"


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _ctx(**trigger: object) -> WorkflowContext:
    return WorkflowContext(instance_id="i", workflow_id="w", trigger=dict(trigger))


# --- extract_archive stock function ---


async def test_extracts_zip_members_filtered_by_suffix() -> None:
    world = mock_world()
    await world.fs.write_bytes(
        "/spool/m1/report.zip",
        _zip_bytes({"agg.xml": b"<feedback/>", "readme.txt": b"ignore me"}),
    )
    ctx = _ctx(attachment_paths=["/spool/m1/report.zip"])

    out = await extract_archive(
        {"paths_from": "trigger.attachment_paths", "dest_dir": "/viewer", "member_suffix": ".xml"},
        ctx,
        world,
    )

    assert out["extracted"] == ["/viewer/agg.xml"]
    assert out["extracted_count"] == 1
    assert await world.fs.read_bytes("/viewer/agg.xml") == b"<feedback/>"
    assert any(s["reason"] == "suffix" for s in out["skipped"])


async def test_extracts_gzip_stripping_gz_suffix() -> None:
    world = mock_world()
    await world.fs.write_bytes("/spool/m1/agg.xml.gz", gzip.compress(b"<feedback/>"))
    ctx = _ctx(attachment_paths=["/spool/m1/agg.xml.gz"])

    out = await extract_archive(
        {"paths_from": "trigger.attachment_paths", "dest_dir": "/viewer", "member_suffix": ".xml"},
        ctx,
        world,
    )

    assert out["extracted"] == ["/viewer/agg.xml"]
    assert await world.fs.read_bytes("/viewer/agg.xml") == b"<feedback/>"


async def test_zip_slip_member_names_are_flattened() -> None:
    world = mock_world()
    await world.fs.write_bytes(
        "/spool/m1/evil.zip", _zip_bytes({"../../escape.xml": b"<feedback/>"})
    )
    ctx = _ctx(attachment_paths=["/spool/m1/evil.zip"])

    out = await extract_archive(
        {"paths_from": "trigger.attachment_paths", "dest_dir": "/viewer"}, ctx, world
    )

    # Written inside dest_dir under the basename — never up the tree.
    assert out["extracted"] == ["/viewer/escape.xml"]


async def test_duplicate_member_names_get_numeric_prefix() -> None:
    world = mock_world()
    await world.fs.write_bytes("/spool/a.zip", _zip_bytes({"agg.xml": b"<one/>"}))
    await world.fs.write_bytes("/spool/b.zip", _zip_bytes({"agg.xml": b"<two/>"}))
    ctx = _ctx(attachment_paths=["/spool/a.zip", "/spool/b.zip"])

    out = await extract_archive(
        {"paths_from": "trigger.attachment_paths", "dest_dir": "/viewer"}, ctx, world
    )

    assert out["extracted"] == ["/viewer/agg.xml", "/viewer/2_agg.xml"]
    assert await world.fs.read_bytes("/viewer/2_agg.xml") == b"<two/>"


async def test_non_archive_and_corrupt_inputs_are_skipped_not_fatal() -> None:
    world = mock_world()
    await world.fs.write_bytes("/spool/photo.png", b"png-bytes")
    await world.fs.write_bytes("/spool/broken.zip", b"definitely not a zip")
    ctx = _ctx(attachment_paths=["/spool/photo.png", "/spool/broken.zip", "/spool/missing.zip"])

    out = await extract_archive(
        {"paths_from": "trigger.attachment_paths", "dest_dir": "/viewer"}, ctx, world
    )

    assert out["extracted_count"] == 0
    reasons = {s["path"]: s["reason"] for s in out["skipped"]}
    assert reasons["/spool/photo.png"] == "not an archive"
    assert reasons["/spool/broken.zip"] == "not a valid zip"
    assert "unreadable" in reasons["/spool/missing.zip"]


async def test_empty_or_missing_paths_is_a_clean_noop() -> None:
    world = mock_world()
    out = await extract_archive(
        {"paths_from": "trigger.attachment_paths", "dest_dir": "/viewer"}, _ctx(), world
    )
    assert out == {"dest_dir": "/viewer", "extracted": [], "extracted_count": 0, "skipped": []}


async def test_missing_dest_dir_raises() -> None:
    with pytest.raises(StepFailure):
        await extract_archive({"paths": []}, _ctx(), mock_world())


# --- copy_files stock function ---


async def test_copy_files_delivers_by_basename_with_suffix_filter() -> None:
    world = mock_world()
    await world.fs.write_bytes("/staging/agg.xml", b"<feedback/>")
    await world.fs.write_bytes("/staging/notes.txt", b"skip me")
    ctx = WorkflowContext(
        instance_id="i",
        workflow_id="w",
        steps={"extract": {"extracted": ["/staging/agg.xml", "/staging/notes.txt"]}},
    )

    out = await copy_files(
        {"paths_from": "steps.extract.extracted", "dest_dir": "/final", "suffix": ".xml"},
        ctx,
        world,
    )

    assert out["copied"] == ["/final/agg.xml"]
    assert out["copied_count"] == 1
    assert await world.fs.read_bytes("/final/agg.xml") == b"<feedback/>"
    assert any(s["reason"] == "suffix" for s in out["skipped"])


async def test_copy_files_skips_unreadable_sources() -> None:
    world = mock_world()
    out = await copy_files({"paths": ["/nope/missing.xml"], "dest_dir": "/final"}, _ctx(), world)
    assert out["copied_count"] == 0
    assert "unreadable" in out["skipped"][0]["reason"]


async def test_copy_files_requires_dest_dir() -> None:
    with pytest.raises(StepFailure):
        await copy_files({"paths": []}, _ctx(), mock_world())


async def test_copy_files_empty_paths_is_a_clean_noop() -> None:
    out = await copy_files({"paths_from": "steps.x.y", "dest_dir": "/final"}, _ctx(), mock_world())
    assert out == {"dest_dir": "/final", "copied": [], "copied_count": 0, "skipped": []}


# --- the dmarc-ingest example workflow ---


def _load_definition() -> object:
    yaml_text = (EXAMPLES_ROOT / "dmarc_ingest" / "workflow.yaml").read_text()
    return load_definition_from_yaml(yaml_text)


def test_dmarc_ingest_yaml_parses() -> None:
    definition = _load_definition()
    assert definition.id == "dmarc-ingest"  # type: ignore[attr-defined]
    # Fully deterministic: no agentic steps anywhere.
    assert all(s.type == "deterministic" for s in definition.steps)  # type: ignore[attr-defined]


async def test_dmarc_ingest_runs_end_to_end() -> None:
    """Trigger payload (as the email trigger builds it) → XMLs land in the
    dmarc-viewer watched dir. No Bedrock involved at any point."""
    definition = _load_definition()
    world = mock_world()
    await world.fs.write_bytes(
        "/tmp/dmarc-spool/m1/google.zip",
        _zip_bytes({"google.com!qs.com!1!2.xml": b"<feedback/>"}),
    )
    await world.fs.write_bytes("/tmp/dmarc-spool/m1/agg.xml.gz", gzip.compress(b"<gz/>"))

    engine = WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=default_function_registry(),
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),  # would raise if anything called Bedrock
        world=world,
    )
    instance = await engine.run(
        definition,  # type: ignore[arg-type]
        trigger_payload={
            "subject": "Report domain: qs.com",
            "attachment_paths": [
                "/tmp/dmarc-spool/m1/google.zip",
                "/tmp/dmarc-spool/m1/agg.xml.gz",
            ],
        },
    )

    assert instance.state == WorkflowInstanceState.COMPLETED
    # Step 1: archives unzipped into the staging dir…
    extract_out = instance.context["steps"]["extract_reports"]
    assert extract_out["extracted_count"] == 2
    assert extract_out["dest_dir"] == "/tmp/dmarc-extracted"
    # …step 2: XMLs delivered to the viewer's watched dir.
    deliver_out = instance.context["steps"]["deliver_reports"]
    assert deliver_out["copied_count"] == 2
    dest = "/home/ubuntu/Dev/dmarc-viewer/xml-files"
    assert await world.fs.read_bytes(f"{dest}/google.com!qs.com!1!2.xml") == b"<feedback/>"
    assert await world.fs.read_bytes(f"{dest}/agg.xml") == b"<gz/>"
