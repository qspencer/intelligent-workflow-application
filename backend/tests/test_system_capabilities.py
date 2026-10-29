"""The SYSTEM capability layer (G30) — the outermost bound, and that production
cannot quietly run without it.

Before 2026-10-29 production ran with no system layer: `system_capabilities`
was never set, so a definition — including one drafted by the scaffold model —
could declare itself unrestricted. The layer now lives in a tracked file
(`config/system_capabilities.yaml`) that the production launcher names, so CI
tests exactly what production runs with:

- the file loads, with every attribute stated (an omitted one would mean
  UNRESTRICTED, so a typo would fail open);
- every ARMED bundled workflow is accounted for — either it runs untouched
  under the layer, or it is declared blocked with the reason, checked both
  ways so widening the layer forces the declaration to be revisited;
- the launcher sets it, and `/api/health` reports it without leaking contents.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from workflow_platform.engine import default_function_registry
from workflow_platform.main import create_app
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.security.capabilities import (
    SYSTEM_CAPABILITIES_ENV,
    CapabilityPolicy,
    SystemCapabilitiesError,
    load_system_capabilities,
    resolve_capabilities,
    system_capabilities_from_env,
)
from workflow_platform.workflow import WorkflowDefinition, load_definition_from_yaml

REPO = Path(__file__).resolve().parents[2]
SHIPPED = REPO / "config" / "system_capabilities.yaml"
EXAMPLES = REPO / "examples"
LAUNCHER = REPO / "scripts" / "run-local-be.sh"

RUNS = "runs"

#: Every bundled workflow whose trigger the orchestrator ARMS at startup, and
#: what the system layer does to it. Derived set compared both ways below.
_ARMED: dict[str, str] = {
    "dmarc-ingest": RUNS,
    "email-triage-apply": RUNS,
    "scheduled-health-report": RUNS,
    "github-pr-triage": RUNS,
    "research-paper-triage": RUNS,
    "webhook-echo": RUNS,
    "invoice-extraction": (
        "blocked: reads PDFs through `pdf_extract` and sends mail with `email_send`; "
        "never run in production — widen the layer in a reviewed commit to enable it"
    ),
    "pdf-classifier": (
        "blocked: reads PDFs through `pdf_extract`; never run in production — "
        "widen the layer in a reviewed commit to enable it"
    ),
}


def _shipped() -> CapabilityPolicy:
    return load_system_capabilities(SHIPPED)


def _armed_definitions() -> dict[str, WorkflowDefinition]:
    out = {}
    for path in sorted(EXAMPLES.glob("*/workflow.yaml")):
        definition = load_definition_from_yaml(path.read_text())
        if definition.trigger.type != "manual":
            out[definition.id] = definition
    return out


def _functions_that_consult_capabilities() -> set[str]:
    """Registry names of deterministic functions that route through a
    capability-checked tool — DERIVED, so phase 2 (teaching the file-moving
    functions to check) is reflected here without anyone editing a list."""
    tree = ast.parse((REPO / "backend/src/workflow_platform/engine/functions.py").read_text())
    checked = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "ToolContext"
            and any(k.arg == "capabilities" for k in n.keywords)
            for n in ast.walk(fn)
        )
    }
    registry = default_function_registry()
    return {name for name, fn in registry._fns.items() if fn.__name__ in checked}


def _blocked_by(system: CapabilityPolicy, definition: WorkflowDefinition) -> list[str]:
    checked_functions = _functions_that_consult_capabilities()
    reasons = []
    for step in definition.steps:
        resolved = resolve_capabilities(
            system, definition.capabilities, getattr(step, "capabilities", None)
        )
        for tool in getattr(step, "tools", None) or []:
            if not resolved.tool_allowed(tool):
                reasons.append(f"{step.id}: tool {tool}")
        function = getattr(step, "function", None)
        if function in checked_functions and system.file_read == []:
            reasons.append(f"{step.id}: function {function} reads files")
    return reasons


# --- the shipped layer --------------------------------------------------------


def test_the_shipped_layer_loads_with_every_attribute_stated() -> None:
    raw = yaml.safe_load(SHIPPED.read_text())
    assert set(raw) == set(CapabilityPolicy.model_fields)
    policy = _shipped()
    assert policy.tools is not None, "the system layer must restrict tools"


def test_every_armed_workflow_is_accounted_for() -> None:
    armed = set(_armed_definitions())
    assert armed - set(_ARMED) == set(), (
        f"newly armed workflow(s) {sorted(armed - set(_ARMED))}: declare whether the "
        f"system layer lets each run, or why it is blocked"
    )
    assert set(_ARMED) - armed == set(), f"declared but not armed: {sorted(set(_ARMED) - armed)}"


@pytest.mark.parametrize("workflow_id", sorted(_ARMED))
def test_the_layer_does_exactly_what_is_declared_to_each_armed_workflow(workflow_id: str) -> None:
    blocked = _blocked_by(_shipped(), _armed_definitions()[workflow_id])
    if _ARMED[workflow_id] == RUNS:
        assert not blocked, f"{workflow_id} is declared to run but the layer blocks: {blocked}"
    else:
        assert blocked, (
            f"{workflow_id} is declared blocked but now runs — the layer was widened; "
            f"update the declaration to `runs`"
        )


def test_the_live_triage_tool_is_the_only_tool_in_production() -> None:
    """The inventory's finding, pinned: production uses one governed tool."""
    assert _shipped().tools == ["email_label_apply__qspencer_gmail_com"]


def test_the_browser_stays_off_until_destinations_are_enforced_before_dispatch() -> None:
    """`allowed_hosts` governs agent-initiated requests and returned content, NOT
    destinations: redirect hops, subresources, preconnect and WebSockets can
    still reach a disallowed host (external review, 2026-10-29). Until
    destination enforcement exists at the network layer, no browser tool may
    enter the system layer — so that residual is unreachable in production by
    construction, not by convention."""
    policy = _shipped()
    browser = [t for t in policy.tools or [] if t.startswith("browser_")]
    assert not browser, (
        f"browser tool(s) {browser} enabled in the system layer, but `allowed_hosts` "
        f"does not yet enforce destinations before dispatch (docs/NEXT_STEPS.md G30)"
    )
    assert policy.allowed_hosts == []


def test_the_production_launcher_names_the_shipped_layer() -> None:
    """The guarantee CI can see: what production starts with is this file."""
    text = LAUNCHER.read_text()
    # Anchored at line start: a control showed an unanchored search matched a
    # COMMENTED-OUT export and passed.
    m = re.search(
        rf"^export {SYSTEM_CAPABILITIES_ENV}=\"\$\{{{SYSTEM_CAPABILITIES_ENV}:-([^}}]+)\}}\"",
        text,
        re.M,
    )
    assert m, f"run-local-be.sh no longer exports {SYSTEM_CAPABILITIES_ENV}"
    assert m.group(1) == "$REPO_ROOT/config/system_capabilities.yaml"


# --- the loader refuses anything that could widen the layer --------------------


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("tools: []\nfile_read: []\nfile_write: []\nallowed_hosts: []\n", "missing"),
        (
            "tools: []\nfile_reads: []\nfile_write: []\nallowed_hosts: []\nmax_tokens_per_call: 1\n",
            "a misspelt key in place of a real one",
        ),
        # All five present PLUS a stray key — the case only the unknown-key
        # check catches. An operator adding `allowed_host:` believing it
        # restricts would otherwise have it silently ignored. (A control showed
        # the case above alone could not tell: it also trips `missing`.)
        (
            "tools: []\nfile_read: []\nfile_write: []\nallowed_hosts: []\n"
            "max_tokens_per_call: 1\nallowed_host: [x]\n",
            "an extra key beside complete ones",
        ),
        ("- tools\n", "not a mapping"),
        ("tools: [\n", "malformed YAML"),
        (
            "tools: []\nfile_read: []\nfile_write: []\nallowed_hosts: []\nmax_tokens_per_call: x\n",
            "a wrong type",
        ),
    ],
)
def test_the_loader_refuses_a_layer_that_could_fail_open(
    tmp_path: Path, body: str, why: str
) -> None:
    path = tmp_path / "caps.yaml"
    path.write_text(body)
    with pytest.raises(SystemCapabilitiesError):
        load_system_capabilities(path)


def test_an_unreadable_configured_layer_refuses_rather_than_running_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(SYSTEM_CAPABILITIES_ENV, str(tmp_path / "missing.yaml"))
    with pytest.raises(SystemCapabilitiesError):
        system_capabilities_from_env()


def test_unset_means_no_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SYSTEM_CAPABILITIES_ENV, raising=False)
    assert system_capabilities_from_env() is None


# --- /api/health reports it, through the real engine wiring --------------------


def _health(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = create_app(repositories=in_memory_repositories(), start_triggers=False)
    body: dict[str, object] = TestClient(app).get("/api/health").json()
    return body


def test_health_reports_the_layer_as_counts_never_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SYSTEM_CAPABILITIES_ENV, str(SHIPPED))
    body = _health(monkeypatch)
    assert body["system_capabilities"] == {
        "status": "configured",
        "tools": 1,
        "file_read": 0,
        "file_write": 0,
        "allowed_hosts": 0,
        "max_tokens_per_call": 4096,
    }
    # Unauthenticated endpoint: tool names embed mailbox addresses.
    assert "email_label_apply" not in str(body) and "@" not in str(body["system_capabilities"])


def test_health_reports_an_absent_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SYSTEM_CAPABILITIES_ENV, raising=False)
    assert _health(monkeypatch)["system_capabilities"] == {"status": "absent"}


# --- through the REAL engine: the layer lets production in and keeps the rest out ---

_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


async def _file_read_call(system: CapabilityPolicy | None) -> dict[str, object]:
    """Run one agentic step that calls `file_read` on a file that EXISTS in the
    world, and return the raw tool-call record. With the file present, a
    capability refusal and a genuine read are the only two outcomes — a live
    dry-run probe could not tell a refusal from "file not found", because both
    project to the same redacted record. This can."""
    from tests._bedrock_fakes import FakeBedrock, text_response, tool_use_response
    from workflow_platform.engine import ToolCatalog, WorkflowEngine
    from workflow_platform.tools import FileReadTool
    from workflow_platform.workflow import load_definition
    from workflow_platform.world import mock_world

    repos = in_memory_repositories()
    definition = load_definition(
        {
            "id": "g30-engine-probe",
            "name": "g30 engine probe",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "read",
                    "type": "agentic",
                    "goal": "read it",
                    "model": _MODEL,
                    "tools": ["file_read"],
                }
            ],
            "edges": [],
        }
    )
    engine = WorkflowEngine(
        repositories=repos,
        functions=default_function_registry(),
        tools=ToolCatalog([FileReadTool()]),
        bedrock=FakeBedrock(
            [
                tool_use_response(tool_uses=[("c1", "file_read", {"path": "/data/x.txt"})]),
                text_response("done"),
            ]
        ),
        world=mock_world(files={"/data/x.txt": b"hello"}),
        system_capabilities=system,
    )
    instance = await engine.run(definition)
    (step,) = await repos.steps.list_by_instance(instance.id)
    assert step.output is not None
    calls = step.output["tool_calls"]
    assert isinstance(calls, list) and len(calls) == 1
    call: dict[str, object] = calls[0]
    return call


async def test_the_shipped_layer_refuses_a_tool_it_does_not_list() -> None:
    call = await _file_read_call(_shipped())
    assert "Capability denied: tool 'file_read' is not in the allowlist" in str(call)
    assert "hello" not in str(call), "the file was read despite the refusal"


async def test_without_a_layer_the_same_call_reads_the_file() -> None:
    """The twin: same step, same world, no system layer — so the refusal
    above is attributable to the layer and nothing else."""
    call = await _file_read_call(None)
    assert "Capability denied" not in str(call)
    assert "hello" in str(call)
