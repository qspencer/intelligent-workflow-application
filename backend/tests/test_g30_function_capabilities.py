"""G30 phase 2 — deterministic file functions consult capabilities.

Until 2026-10-29 only `pdf_extract` did. `copy_files`, `extract_archive`,
`append_file`, `route_by_classification`, `route_by_value` and `write_csv`
called `world.fs` directly, so a scaffolded deterministic step could read or
write any path its config named, whatever the layers said. The external
reviewer named this the completeness boundary: capability enforcement may not be
claimed complete while it is open.

Three properties, plus the population that keeps them from rotting:

- a denied read or write RAISES before any I/O, and is never a "skip" — the
  silent-delivery failure mode is exactly what a quiet denial would recreate;
- the two LIVE deterministic workflows still work under the SHIPPED layer, run
  end to end at their real paths;
- every stock function that touches the filesystem consults capabilities —
  derived from source, so a new one fails here until it does.
"""

from __future__ import annotations

import ast
import io
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock, text_response
from workflow_platform.engine import ToolCatalog, WorkflowEngine, default_function_registry
from workflow_platform.engine.context import WorkflowContext
from workflow_platform.engine.functions import (
    append_file,
    copy_files,
    extract_archive,
    route_by_classification,
    route_by_value,
    write_csv,
)
from workflow_platform.engine.registry import StepFailure
from workflow_platform.persistence import WorkflowInstanceState, in_memory_repositories
from workflow_platform.security.capabilities import (
    CapabilityPolicy,
    load_system_capabilities,
    resolve_capabilities,
)
from workflow_platform.workflow import load_definition_from_yaml
from workflow_platform.world import World, mock_world
from workflow_platform.world.mock import MockFilesystem

REPO = Path(__file__).resolve().parents[2]
SHIPPED = REPO / "config" / "system_capabilities.yaml"
FUNCTIONS = REPO / "backend" / "src" / "workflow_platform" / "engine" / "functions.py"

READ_ALL_WRITE_NONE = CapabilityPolicy(file_read=["/**"], file_write=[])
READ_NONE_WRITE_ALL = CapabilityPolicy(file_read=[], file_write=["/**"])


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _ctx(policy: CapabilityPolicy | None, **fields: Any) -> WorkflowContext:
    ctx = WorkflowContext(instance_id="i", workflow_id="w", **fields)
    ctx.capabilities = resolve_capabilities(policy) if policy is not None else None
    return ctx


async def _seeded() -> World:
    world = mock_world()
    # TWO of everything a function iterates over. With one, a function that
    # checked only its first file passed every test here — the second-seat
    # re-check (2026-10-29) killed nothing with an `if not copied:` mutant.
    await world.fs.write_bytes(
        "/in/report.zip", _zip({"agg.xml": b"<feedback/>", "agg2.xml": b"<feedback2/>"})
    )
    await world.fs.write_bytes("/in/doc.pdf", b"%PDF")
    await world.fs.write_bytes("/in/agg.xml", b"<feedback/>")
    await world.fs.write_bytes("/in/agg2.xml", b"<feedback2/>")
    return world


Call = Callable[[WorkflowContext, World], Awaitable[Any]]

#: Each function, called with config that makes it read under /in and write
#: under /out (or only write, for write_csv).
_CALLS: dict[str, tuple[Call, dict[str, Any], bool]] = {
    "copy_files": (
        lambda c, w: copy_files(
            {"paths": ["/in/agg.xml", "/in/agg2.xml"], "dest_dir": "/out"}, c, w
        ),
        {},
        True,
    ),
    "extract_archive": (
        lambda c, w: extract_archive({"paths": ["/in/report.zip"], "dest_dir": "/out"}, c, w),
        {},
        True,
    ),
    "append_file": (
        lambda c, w: append_file({"path": "/out/log.txt", "content_from": "trigger.line"}, c, w),
        {"trigger": {"line": "hello"}},
        True,
    ),
    "route_by_classification": (
        lambda c, w: route_by_classification(
            {"source_from": "trigger.file_path", "output_root": "/out"}, c, w
        ),
        {
            "trigger": {"file_path": "/in/doc.pdf"},
            "steps": {"classify": {"output_text": '{"document_type": "invoice"}'}},
        },
        True,
    ),
    "route_by_value": (
        lambda c, w: route_by_value(
            {
                "source_from": "trigger.file_path",
                "value_from": "trigger.kind",
                "output_root": "/out",
            },
            c,
            w,
        ),
        {"trigger": {"file_path": "/in/doc.pdf", "kind": "invoice"}},
        True,
    ),
    "write_csv": (
        lambda c, w: write_csv(
            {"path": "/out/rows.csv", "columns": ["a"], "rows_from": "trigger.rows"}, c, w
        ),
        {"trigger": {"rows": [{"a": "1"}]}},
        False,
    ),
}


async def _files_under(world: World, root: str) -> list[str]:
    """No default on a missing attribute: an earlier version read a private
    name that did not exist, got an empty dict, and made every "nothing was
    written" assertion pass vacuously. The positive tests caught it."""
    assert isinstance(world.fs, MockFilesystem), "these checks need the mock filesystem"
    return sorted(p for p in world.fs.files if p.startswith(root))


@pytest.mark.parametrize("name", sorted(_CALLS))
async def test_a_denied_write_raises_before_anything_is_written(name: str) -> None:
    call, fields, _ = _CALLS[name]
    world = await _seeded()
    with pytest.raises(StepFailure, match="Capability denied: write"):
        await call(_ctx(READ_ALL_WRITE_NONE, **fields), world)
    assert await _files_under(world, "/out") == []


@pytest.mark.parametrize("name", sorted(n for n, (_, _, reads) in _CALLS.items() if reads))
async def test_a_denied_read_raises_and_is_never_a_skip(name: str) -> None:
    """A skip would return normally with a zero count — a delivery of nothing
    reported as success. A denial must stop the step instead."""
    call, fields, _ = _CALLS[name]
    world = await _seeded()
    with pytest.raises(StepFailure, match="Capability denied: read"):
        await call(_ctx(READ_NONE_WRITE_ALL, **fields), world)
    assert await _files_under(world, "/out") == []


@pytest.mark.parametrize("name", sorted(_CALLS))
async def test_the_paths_it_needs_are_enough(name: str) -> None:
    call, fields, _ = _CALLS[name]
    world = await _seeded()
    allowed = CapabilityPolicy(file_read=["/in/*", "/out/**"], file_write=["/out/**"])
    await call(_ctx(allowed, **fields), world)
    assert await _files_under(world, "/out") != []


@pytest.mark.parametrize("name", sorted(_CALLS))
async def test_no_capabilities_in_context_means_no_restriction(name: str) -> None:
    call, fields, _ = _CALLS[name]
    world = await _seeded()
    await call(_ctx(None, **fields), world)
    assert await _files_under(world, "/out") != []


async def test_a_traversal_out_of_an_allowed_tree_is_refused() -> None:
    """The matcher used to admit `<allowed>/../x`; phase 2 depends on it not."""
    world = await _seeded()
    await world.fs.write_bytes("/secret.xml", b"SECRET")
    ctx = _ctx(CapabilityPolicy(file_read=["/in/*/*"], file_write=["/out/*"]))
    with pytest.raises(StepFailure, match="Capability denied: read"):
        await copy_files({"paths": ["/in/../secret.xml"], "dest_dir": "/out"}, ctx, world)
    assert await _files_under(world, "/out") == []


# --- the population ------------------------------------------------------------


def _fs_touching_functions() -> dict[str, set[str]]:
    """Every top-level function in functions.py, mapped to the `world.fs`
    operations it calls directly."""
    tree = ast.parse(FUNCTIONS.read_text())
    out: dict[str, set[str]] = {}
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        ops = {
            n.func.attr
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Attribute)
            and n.func.value.attr == "fs"
        }
        if ops:
            out[fn.name] = ops
    return out


def _consults_capabilities(fn: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and (
            getattr(n.func, "id", None) == "_require_path"
            or (
                getattr(n.func, "id", None) == "ToolContext"
                and any(k.arg == "capabilities" for k in n.keywords)
            )
        )
        for n in ast.walk(fn)
    )


def test_every_function_that_touches_the_filesystem_consults_capabilities() -> None:
    tree = ast.parse(FUNCTIONS.read_text())
    by_name = {
        n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    touching = _fs_touching_functions()
    assert touching, "the derivation found no filesystem access at all — it is broken"
    unchecked = sorted(name for name in touching if not _consults_capabilities(by_name[name]))
    assert not unchecked, (
        f"function(s) {unchecked} call world.fs without consulting capabilities — "
        f"check each path with `_require_path` before the access"
    )


def test_every_registered_file_function_is_covered_by_the_behaviour_tests() -> None:
    registered = {fn.__name__ for fn in default_function_registry()._fns.values()}
    assert (set(_fs_touching_functions()) & registered) == set(_CALLS)


# --- the live deterministic workflows, under the SHIPPED layer, at real paths ---

SPOOL = "/home/ubuntu/.local/share/workflow-platform/dmarc/spool"
VIEWER = "/home/ubuntu/Dev/dmarc-viewer/xml-files"


def _engine(world: World, **kw: Any) -> WorkflowEngine:
    return WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=default_function_registry(),
        tools=ToolCatalog([]),
        bedrock=kw.pop("bedrock", FakeBedrock([])),
        world=world,
        system_capabilities=load_system_capabilities(SHIPPED),
    )


def _dmarc() -> Any:
    return load_definition_from_yaml((REPO / "examples/dmarc_ingest/workflow.yaml").read_text())


async def test_dmarc_ingest_delivers_under_the_shipped_layer() -> None:
    world = mock_world()
    report = f"{SPOOL}/1a17cd0ca051a5d6/google.com!quentinspencer.com!1!2.zip"
    await world.fs.write_bytes(report, _zip({"google.com!qs.com!1!2.xml": b"<feedback/>"}))
    instance = await _engine(world).run(_dmarc(), trigger_payload={"attachment_paths": [report]})
    assert instance.state == WorkflowInstanceState.COMPLETED, instance.error
    assert await world.fs.read_bytes(f"{VIEWER}/google.com!qs.com!1!2.xml") == b"<feedback/>"


async def test_dmarc_ingest_fails_loudly_on_a_path_outside_the_layer() -> None:
    """An attachment path the layer does not admit stops the run — it does not
    complete having delivered nothing."""
    world = mock_world()
    stray = "/tmp/elsewhere/report.zip"
    await world.fs.write_bytes(stray, _zip({"x.xml": b"<feedback/>"}))
    instance = await _engine(world).run(_dmarc(), trigger_payload={"attachment_paths": [stray]})
    assert instance.state == WorkflowInstanceState.FAILED
    assert await _files_under(world, VIEWER) == []


async def test_scheduled_health_report_appends_under_the_shipped_layer() -> None:
    definition = load_definition_from_yaml(
        (REPO / "examples/scheduled_health_report/workflow.yaml").read_text()
    )
    world = mock_world()
    engine = _engine(world, bedrock=FakeBedrock([text_response("system: ok")]))
    instance = await engine.run(definition, trigger_payload={})
    assert instance.state == WorkflowInstanceState.COMPLETED, instance.error
    log = "/home/ubuntu/.local/share/workflow-platform/health/scheduled-health-report.log"
    assert b"system: ok" in await world.fs.read_bytes(log)


# --- the matcher and the pattern rule phase 2 depends on -----------------------


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/data/allowed/x/report.zip", True),
        ("/data/allowed/../secret", False),  # admitted by the old matcher
        ("/data/allowed/x/../../secret", False),
        ("/deep/a/b/c.txt", True),  # `**` is recursive now
        ("/deep/a.txt", True),
        ("relative/x", False),
        ("//data/allowed/x/y", False),
        ("/data/allowed/x", False),  # one level short of `*/*`
    ],
)
def test_the_matcher(path: str, allowed: bool) -> None:
    caps = resolve_capabilities(CapabilityPolicy(file_read=["/data/allowed/*/*", "/deep/**"]))
    assert caps.can_read(path) is allowed


@pytest.mark.parametrize("pattern", ["spool/*", "/data/../x/*", "./x/*"])
def test_a_pattern_the_matcher_could_never_honour_is_refused(pattern: str) -> None:
    """A relative pattern used to match from the right — any directory of that
    name, anywhere. Refused loudly, not silently never-matching."""
    with pytest.raises(ValueError, match="absolute"):
        CapabilityPolicy(file_read=[pattern])


# --- a parallel sibling cannot undo a step's narrowing --------------------------


async def test_a_parallel_sibling_cannot_widen_a_steps_capabilities() -> None:
    """Found by an independent red-team (2026-10-29). Step capabilities were
    written onto the SHARED context, and `_require_path` re-reads them after
    every await — so a sibling running in parallel overwrote a step's narrowed
    set mid-function. Their repro relied on timing (a 50 MB read); this forces
    the same interleaving deterministically: the narrowed step's read waits on
    an event only the sibling step sets."""
    import asyncio

    gate = asyncio.Event()

    async def open_gate(
        config: dict[str, Any], context: WorkflowContext, world: World
    ) -> dict[str, Any]:
        gate.set()
        return {}

    world = mock_world()
    await world.fs.write_bytes("/in/agg.xml", b"<feedback/>")
    unguarded_read = world.fs.read_bytes

    async def gated_read(path: str) -> bytes:
        await gate.wait()  # the sibling runs while this step is suspended here
        return await unguarded_read(path)

    world.fs.read_bytes = gated_read  # type: ignore[method-assign]
    functions = default_function_registry()
    functions.register("open_gate", open_gate)
    definition = load_definition_from_yaml(
        """
id: race
name: race
trigger: {type: manual}
capabilities: {file_read: ["/in/*"], file_write: ["/out/*"]}
steps:
  - id: narrow
    type: deterministic
    function: copy_files
    capabilities: {file_read: ["/in/*"], file_write: []}
    config: {paths: ["/in/agg.xml"], dest_dir: "/out"}
  - id: sibling
    type: deterministic
    function: open_gate
    config: {}
"""
    )
    engine = WorkflowEngine(
        repositories=in_memory_repositories(),
        functions=functions,
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=world,
    )
    instance = await engine.run(definition, trigger_payload={})
    assert await _files_under(world, "/out") == [], "the narrowed step wrote after its sibling ran"
    assert instance.state == WorkflowInstanceState.FAILED


# --- the check is on the path ACTUALLY opened -----------------------------------


class _RecordingCapabilities:
    """Duck-typed stand-in that allows everything and logs each check."""

    def __init__(self, log: list[tuple[str, str, str]]) -> None:
        self._log = log

    def can_read(self, path: str) -> bool:
        self._log.append(("check", "read", path))
        return True

    def can_write(self, path: str) -> bool:
        self._log.append(("check", "write", path))
        return True


@pytest.mark.parametrize("name", sorted(_CALLS))
async def test_every_access_is_preceded_by_a_check_of_that_same_path(name: str) -> None:
    """The population test above checks that a function CALLS `_require_path`
    — presence, not correctness. An independent red-team (2026-10-29) showed a
    `copy_files` mutant that checked `dest_dir` instead of the file it wrote
    passed it. This is the property itself: every filesystem access, in order,
    is preceded by a check of the same path in the same mode."""
    call, fields, _ = _CALLS[name]
    world = await _seeded()
    log: list[tuple[str, str, str]] = []
    fs = world.fs
    real = {op: getattr(fs, op) for op in ("read_bytes", "write_bytes", "exists")}

    def recording(op: str, mode: str) -> Callable[..., Awaitable[Any]]:
        async def wrapped(path: str, *args: Any) -> Any:
            log.append(("access", mode, path))
            return await real[op](path, *args)

        return wrapped

    fs.read_bytes = recording("read_bytes", "read")  # type: ignore[method-assign,assignment]
    fs.write_bytes = recording("write_bytes", "write")  # type: ignore[method-assign,assignment]
    fs.exists = recording("exists", "read")  # type: ignore[method-assign,assignment]
    ctx = _ctx(None, **fields)
    ctx.capabilities = _RecordingCapabilities(log)  # type: ignore[assignment]

    await call(ctx, world)

    accesses = [(i, mode, path) for i, (kind, mode, path) in enumerate(log) if kind == "access"]
    assert accesses, f"{name} touched no files — the probe is not exercising it"
    for i, mode, path in accesses:
        assert ("check", mode, path) in log[:i], (
            f"{name}: {mode} of {path!r} happened without a prior {mode} check of that path"
        )
