"""Audit-detail vaulting (docs/TRACE_AUDIT_VAULT_DESIGN.md Part 1).

The three criteria round 11 named for this build, plus the scope rule and the
writer enumeration. Read the criteria in the design doc; each has a test here
with the same number.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from tests._bedrock_fakes import FakeBedrock
from workflow_platform.engine.executor import ToolCatalog, WorkflowEngine
from workflow_platform.engine.registry import FunctionRegistry
from workflow_platform.persistence import in_memory_repositories
from workflow_platform.persistence.models import (
    RawTraceKind,
    WorkflowInstance,
    WorkflowInstanceState,
)
from workflow_platform.trace_vault import audit_detail_has_raw, audit_idempotency_key
from workflow_platform.world import mock_world

#: An audit detail the FINAL policy strips — the motivating case. The
#: model-chosen tool name is attacker-influenced, so at rest it must be
#: withheld, and withholding is only safe once the raw is in the vault.
RAW_DETAIL = {"tool": "exfiltrate_sk_live_abc", "attempted": "/etc/shadow"}


def _engine(repos: Any = None) -> WorkflowEngine:
    return WorkflowEngine(
        repositories=repos or in_memory_repositories(),
        functions=FunctionRegistry(),
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        trace_safe_only=True,
    )


async def _instance(engine: WorkflowEngine, org_id: str = "acme") -> WorkflowInstance:
    return await engine.repositories.instances.create(
        WorkflowInstance(workflow_id="wf", org_id=org_id, state=WorkflowInstanceState.RUNNING)
    )


async def _vault_rows(engine: WorkflowEngine, instance_id: str) -> list[Any]:
    rows = await engine.repositories.raw_trace_vault.list_by_instance(instance_id)
    return [r for r in rows if r.kind is RawTraceKind.AUDIT_DETAIL]


# --------------------------------------------------------------------------
# Criterion 1 — identity: stable across a retried write, distinct per entry.
# --------------------------------------------------------------------------


async def test_C1_many_entries_in_ONE_step_attempt_each_get_their_own_vault_row() -> None:
    """THE multiplicity case, and the reason the step-attempt key could not be
    reused: one step attempt emits many audit entries, so a key anchored on
    the attempt would collapse every tool call on that attempt onto one row
    and silently lose all but the last."""
    engine = _engine()
    inst = await _instance(engine)

    for i in range(5):
        await engine._audit(
            "tool_param_override_blocked",
            actor_type="agent",
            actor_id="a",
            instance_id=inst.id,
            step_id="the-same-step",
            detail={**RAW_DETAIL, "attempted": f"/etc/shadow/{i}"},
        )

    rows = await _vault_rows(engine, inst.id)
    assert len(rows) == 5, f"5 audit entries on one step attempt produced {len(rows)} vault rows"
    assert len({r.audit_entry_id for r in rows}) == 5, "vault rows collided on audit entry id"
    # Every one is RECOVERABLE, not merely present.
    recovered = {r.payload["attempted"] for r in rows}
    assert recovered == {f"/etc/shadow/{i}" for i in range(5)}


async def test_C1_redriving_the_SAME_entry_id_readdresses_the_same_row() -> None:
    """Stable identity on retry: re-vaulting the same entry must not
    accumulate orphans."""
    engine = _engine()
    inst = await _instance(engine)
    for _ in range(3):
        await engine._vault.record_audit_detail(
            org_id=inst.org_id,
            instance_id=inst.id,
            audit_entry_id="fixed-entry-id",
            action="tool_param_override_blocked",
            detail=RAW_DETAIL,
        )
    assert len(await _vault_rows(engine, inst.id)) == 1


async def test_C1_two_entries_with_IDENTICAL_detail_do_not_collide() -> None:
    """Distinct identity: same content, different entries, two rows. Content
    is not identity — the entry is."""
    engine = _engine()
    inst = await _instance(engine)
    for eid in ("entry-a", "entry-b"):
        await engine._vault.record_audit_detail(
            org_id=inst.org_id,
            instance_id=inst.id,
            audit_entry_id=eid,
            action="tool_param_override_blocked",
            detail=RAW_DETAIL,
        )
    rows = await _vault_rows(engine, inst.id)
    assert len(rows) == 2
    assert {r.audit_entry_id for r in rows} == {"entry-a", "entry-b"}


def test_C1_the_audit_key_is_a_SEPARATE_SPACE_from_the_step_attempt_key() -> None:
    from workflow_platform.trace_vault import idempotency_key

    audit = audit_idempotency_key("o", "i", "e")
    for kind in RawTraceKind:
        assert audit != idempotency_key("o", "i", "e", kind), (
            f"the audit key collides with the step-attempt key for kind {kind}"
        )


# --------------------------------------------------------------------------
# Criterion 2 — recovery when one half of the write fails.
# --------------------------------------------------------------------------


async def test_C2_a_failed_vault_write_FAILS_the_audit_rather_than_dropping_raw() -> None:
    """Durable-or-fail. If the vault write is lost and the append proceeds,
    projection destroys the only copy — the whole defect this design fixes."""
    engine = _engine()
    inst = await _instance(engine)

    async def boom(_trace: Any) -> Any:
        raise RuntimeError("vault unavailable")

    engine.repositories.raw_trace_vault.put = boom  # type: ignore[method-assign,assignment]

    with pytest.raises(RuntimeError, match="vault unavailable"):
        await engine._audit(
            "tool_param_override_blocked",
            actor_type="agent",
            actor_id="a",
            instance_id=inst.id,
            detail=RAW_DETAIL,
        )
    entries = await engine.repositories.audit.list_recent(limit=50)
    assert not [e for e in entries if e.action == "tool_param_override_blocked"], (
        "the audit entry was appended even though its raw was never vaulted"
    )


async def test_C2_a_vault_row_whose_append_FAILED_is_still_recoverable() -> None:
    """The other order, stated rather than left to chance: vault succeeds, the
    append then fails. The vault row is an ORPHAN — it is addressed by an
    audit entry id that no entry carries.

    That is the deliberate trade. The alternative (append first) risks the
    entry existing with its raw destroyed, which is unrecoverable; an orphan
    is merely unreferenced, still org-scoped, still grant-gated, and
    reclaimable by sweeping rows whose audit_entry_id has no entry. We accept
    orphans and never accept loss.
    """
    engine = _engine()
    inst = await _instance(engine)

    async def boom(_entry: Any) -> Any:
        raise RuntimeError("audit store unavailable")

    engine.repositories.audit.append = boom  # type: ignore[method-assign,assignment]

    with pytest.raises(RuntimeError, match="audit store unavailable"):
        await engine._audit(
            "tool_param_override_blocked",
            actor_type="agent",
            actor_id="a",
            instance_id=inst.id,
            detail=RAW_DETAIL,
        )

    rows = await _vault_rows(engine, inst.id)
    assert len(rows) == 1, "the raw was not vaulted before the append was attempted"
    assert rows[0].payload == RAW_DETAIL, "the orphaned row does not hold the recoverable raw"
    entries = await engine.repositories.audit.list_recent(limit=50)
    assert not [e for e in entries if e.id == rows[0].audit_entry_id], (
        "premise of this test: the entry was NOT appended, so the row is an orphan"
    )


async def test_C2_an_unresolvable_org_fails_closed_rather_than_guessing() -> None:
    """A vault row filed under the wrong org is a tenant-isolation violation
    and a silent one. Guessing the default org is not available as a
    fallback."""
    engine = _engine()
    with pytest.raises(ValueError, match="cannot resolve the owning org"):
        await engine._audit(
            "tool_param_override_blocked",
            actor_type="agent",
            actor_id="a",
            instance_id="no-such-instance",
            detail=RAW_DETAIL,
        )


async def test_C2_the_vault_row_inherits_the_INSTANCE_org_not_the_default() -> None:
    engine = _engine()
    inst = await _instance(engine, org_id="tenant-b")
    await engine._audit(
        "tool_param_override_blocked",
        actor_type="agent",
        actor_id="a",
        instance_id=inst.id,
        detail=RAW_DETAIL,
    )
    rows = await _vault_rows(engine, inst.id)
    assert [r.org_id for r in rows] == ["tenant-b"]


# --------------------------------------------------------------------------
# The scope rule — round 11's qualifier.
# --------------------------------------------------------------------------


def test_the_scope_rule_IS_the_final_projection_and_cannot_drift() -> None:
    """ "Vault exactly what projection takes away" is implemented as a CALL
    into the projection, not a restatement of it. Asserted by construction:
    for any detail, the predicate agrees with the policy it names."""
    from workflow_platform.trace_projection import project_audit_detail_final

    samples: list[tuple[str, Any]] = [
        ("tool_param_override_blocked", RAW_DETAIL),
        ("step_started", {"step_id": "a", "attempt": 1}),
        ("tool_call", {"tool": "x", "input": {"q": "secret"}}),
        ("memory_recalled", {"query": "who is alice", "entity": "alice@example.com"}),
        ("anything", {}),
        ("anything", {"outcome": "ok"}),
    ]
    for action, detail in samples:
        assert audit_detail_has_raw(action, detail) == (
            project_audit_detail_final(action, detail) != detail
        ), f"the vaulting scope disagrees with the final policy for {action!r}"


def test_the_verifier_and_the_vaulting_predicate_are_one_question() -> None:
    """Converged by the 2026-09-18 at-rest tightening.

    They were deliberately separate while at-rest was the lenient denylist:
    the verifier asked what the policy IN FORCE still left raw, the vault
    predicate what the FINAL policy would remove. At-rest is now the final
    policy, so a second implementation would only be free to drift."""
    from workflow_platform.trace_migration import _audit_has_raw

    for action, detail in [
        ("tool_param_override_blocked", RAW_DETAIL),
        ("step_started", {"step_id": "a", "attempt": 1}),
        ("tool_call", {"tool": "x", "input": {"q": "secret"}}),
        ("escalation_resolved", {"original_id": "esc-1", "resolution": "done"}),
        ("anything", {"outcome": "ok"}),
    ]:
        assert _audit_has_raw(detail, action) == audit_detail_has_raw(action, detail), (
            f"the verifier and the vaulting predicate disagree for {action!r}"
        )


def test_the_tightening_withholds_the_model_chosen_tool_name_at_rest() -> None:
    """The defect this whole design opened on. At rest used to store the
    attacker-influenced tool name verbatim while the read path withheld it —
    strictly more at rest than any ordinary reader could see."""
    from workflow_platform.trace_projection import project_audit_detail_at_rest

    stored = project_audit_detail_at_rest("tool_param_override_blocked", RAW_DETAIL)
    assert stored != RAW_DETAIL, "at rest still stores the raw detail verbatim"
    assert "exfiltrate_sk_live_abc" not in str(stored), "the tool name survived at rest"
    assert "/etc/shadow" not in str(stored), "the attempted value survived at rest"
    # And it is WITHHELD, not destroyed: vaulting is scoped by the same policy.
    assert audit_detail_has_raw("tool_param_override_blocked", RAW_DETAIL), (
        "at rest strips this but nothing vaults it — that is destruction, not withholding"
    )


def test_at_rest_never_holds_more_than_the_read_path_releases() -> None:
    """The invariant the tightening establishes, stated directly."""
    from workflow_platform.trace_projection import (
        project_audit_detail_at_rest,
        project_audit_detail_final,
    )

    samples: list[tuple[str, Any]] = [
        ("tool_param_override_blocked", RAW_DETAIL),
        ("workflow_started", {"workflow_id": "wf", "trigger": {"x": 1}}),
        ("memory_observed", {"facts": 2, "observation": "alice said hello"}),
        ("workflow_forked", {"source_instance_id": "i", "from_step_id": "b"}),
        ("escalation_resolved", {"original_id": "e", "resolution": "done"}),
    ]
    for action, detail in samples:
        assert project_audit_detail_at_rest(action, detail) == project_audit_detail_final(
            action, detail
        ), f"at rest diverges from the read path for {action!r}"


def test_the_escalation_LINK_survives_the_tightening() -> None:
    """`escalation_resolved.original_id` is how the escalations API knows a
    request was answered. It was undeclared, so the tightening would have
    withheld it — and withholding a link does not hide it, it breaks
    resolution permanently. Untested before the tightening; pinned now."""
    from workflow_platform.trace_projection import project_audit_detail_at_rest

    stored = project_audit_detail_at_rest(
        "escalation_resolved", {"original_id": "esc-1", "resolution": "done"}
    )
    assert stored.get("original_id") == "esc-1", (
        "the escalation link did not survive; GET /api/escalations can no longer "
        "tell a resolved escalation from a pending one"
    )


async def test_a_detail_that_loses_NOTHING_is_not_vaulted() -> None:
    """The scope is exact in both directions: no row for details the final
    policy passes through whole."""
    engine = _engine()
    inst = await _instance(engine)
    safe = {"outcome": "ok"}
    assert not audit_detail_has_raw("some_action", safe)
    await engine._audit(
        "some_action", actor_type="engine", actor_id="e", instance_id=inst.id, detail=safe
    )
    assert await _vault_rows(engine, inst.id) == []


# --------------------------------------------------------------------------
# Criterion 3 — coverage: every audit writer classified. Enumerated from
# SOURCE (rule R-c), so a new writer fails the build until it is classified.
# --------------------------------------------------------------------------

#: Every module that WRITES an audit entry, and its disposition.
#:
#: R12 finding 4: the previous scanner matched a receiver *named* `audit`
#: (after stripping underscores), so `self.audit_repo.append(...)` in the
#: escalation tool was invisible — and the grep I "cross-checked" against
#: searched for `audit.append(`, the SAME assumption in another syntax. Two
#: methods agreeing means nothing when they share a blind spot. Discovery now
#: keys on the TYPE: a module that constructs an `AuditEntry` is a writer.
AUDIT_WRITERS: dict[str, str] = {
    # Chokepoint: vaults raw before appending.
    "engine/executor.py": "vaulted",
    # Vaults its own detail (it writes outside the chokepoint). R12 finding 4:
    # it projected `reason`/`context` at rest while vaulting NOTHING, so the
    # model-authored content the escalation exists to convey was destroyed.
    "tools/escalation.py": "vaulted",
    # Not a writer — it maps the type to and from rows. (`models.py` only
    # DEFINES the class, so it never constructs one and never appears.)
    "persistence/postgres.py": "persists-the-type",
    # Operator/governance metadata about an action a PERSON took, not model-
    # or mail-derived content. If any starts carrying model-authored or
    # third-party text it moves to a vaulting path; that is the trigger.
    "api/raw_trace_audit.py": "governance-metadata",
    "api/organizations.py": "governance-metadata",
    "api/workflows.py": "governance-metadata",
    "api/users.py": "governance-metadata",
    "auth/local.py": "governance-metadata",
    "auth/raw_trace_grants.py": "governance-metadata",
    "auth/bootstrap.py": "governance-metadata",
    "trace_rehydrate.py": "governance-metadata",
    # KNOWN GAP, named rather than hidden: engine-derived alert_* entries
    # written outside the chokepoint; on production data 100% would lose
    # something to the final policy.
    "monitoring/service.py": "UNVAULTED-GAP",
}


def _audit_writers(root: pathlib.Path) -> set[str]:
    """Modules under `root` that CONSTRUCT an `AuditEntry`.

    Keyed on the type, not on what the receiving variable happens to be
    called. That is the whole lesson of R12 finding 4.
    """
    found: set[str] = set()
    for f in root.rglob("*.py"):
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "AuditEntry"
            ):
                found.add(str(f.relative_to(root)))
    return found


def test_C3_every_audit_writer_is_classified() -> None:
    """R-c: an ENUMERATION derived from source. A new writer fails the build
    until someone decides whether its details need vaulting."""
    actual = _audit_writers(pathlib.Path("src/workflow_platform"))
    assert actual, "the enumeration found no audit writers — it has stopped working"
    unclassified = actual - set(AUDIT_WRITERS)
    assert not unclassified, (
        f"new audit writer(s) {sorted(unclassified)} are not classified in AUDIT_WRITERS. "
        "Decide whether their details need vaulting before the raw is projected away."
    )
    stale = set(AUDIT_WRITERS) - actual
    assert not stale, f"AUDIT_WRITERS lists modules that no longer write audit: {sorted(stale)}"


def test_C3_discovery_finds_a_REAL_new_call_site(tmp_path: pathlib.Path) -> None:
    """Control for the scanner — and it is a real one this time.

    The previous control did set arithmetic on an already-computed result,
    which the reviewer correctly said tests nothing about discovery. This
    writes an actual module containing the exact spelling the old scanner
    missed and requires the scanner to PARSE it and find it.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "sneaky.py").write_text(
        "\n".join(
            [
                "from workflow_platform.persistence.models import AuditEntry",
                "class T:",
                "    def __init__(self, audit_repo):",
                "        self.audit_repo = audit_repo",
                "    async def go(self):",
                "        entry = AuditEntry(actor_type='a', actor_id='a', action='x')",
                "        await self.audit_repo.append(entry)",
            ]
        )
    )
    found = _audit_writers(tmp_path)
    assert "pkg/sneaky.py" in found, (
        f"discovery missed a real `self.audit_repo.append(...)` call site: {found}. "
        "This is the exact spelling that hid the escalation tool."
    )


def test_C3_the_old_receiver_name_scanner_would_have_MISSED_it(
    tmp_path: pathlib.Path,
) -> None:
    """Pins WHY the scanner changed, so nobody 'simplifies' it back."""
    src = "\n".join(
        [
            "class T:",
            "    async def go(self):",
            "        await self.audit_repo.append(entry)",
        ]
    )
    tree = ast.parse(src)
    matched = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "append"
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr.lstrip("_") == "audit"
    ]
    assert not matched, (
        "the receiver-name scanner now matches `audit_repo`; if that is deliberate, "
        "this test should be deleted rather than adjusted"
    )


def test_C3_the_known_gap_is_recorded_not_forgotten() -> None:
    """The monitoring loop is unvaulted. Pinned so closing it is a deliberate
    edit to this test, and so the gap cannot quietly become untrue."""
    assert AUDIT_WRITERS["monitoring/service.py"] == "UNVAULTED-GAP"


async def _echo_fn(config: Any, ctx: Any, world: Any) -> dict[str, Any]:
    """A deterministic step output with a free-form field, so the projection
    has something to withhold."""
    return {"note": "SYNTHETIC free-form output", "count": 1}


async def test_INVARIANT_no_audit_entry_loses_content_without_a_vault_row() -> None:
    """The safety property the whole design reduces to, over a REAL run.

    For every detail the engine audits: if at-rest projection takes anything
    away, the vault must hold that raw. Anything else is destruction.

    **This test was written wrong first, and the control caught it.** The
    first version compared `project(entry.detail) != entry.detail` — but
    `entry.detail` is what was ALREADY stored, i.e. already projected, so it
    is a fixed point by construction and the comparison was vacuous. It
    passed with vaulting switched off. The raw never reaches the audit row
    (that is the point), so the raw has to be captured as the engine sees it.
    """
    from workflow_platform.trace_projection import project_audit_detail_at_rest
    from workflow_platform.workflow import load_definition

    seen: list[tuple[str, dict[str, Any]]] = []

    class _CapturingEngine(WorkflowEngine):
        async def _audit(
            self, action: str, *, detail: dict[str, Any] | None = None, **kw: Any
        ) -> None:
            seen.append((action, dict(detail or {})))
            await super()._audit(action, detail=detail, **kw)

    registry = FunctionRegistry()
    registry.register("echo", _echo_fn)
    engine = _CapturingEngine(
        repositories=in_memory_repositories(),
        functions=registry,
        tools=ToolCatalog([]),
        bedrock=FakeBedrock([]),
        world=mock_world(),
        trace_safe_only=True,
    )
    definition = load_definition(
        {
            "id": "wf",
            "name": "wf",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "a", "type": "deterministic", "function": "echo", "config": {}},
                {"id": "b", "type": "deterministic", "function": "echo", "config": {}},
            ],
            "edges": [{"from": "a", "to": "b"}],
        }
    )
    instance = await engine.run(definition, trigger_payload={"secret": "SYNTHETIC-RAW"})

    lossy = [
        (action, raw) for action, raw in seen if project_audit_detail_at_rest(action, raw) != raw
    ]
    assert lossy, (
        "no audited detail lost anything to projection in this run, so the "
        "invariant below is vacuous — the workflow no longer exercises the path"
    )

    rows = await engine.repositories.raw_trace_vault.list_by_instance(instance.id)
    payloads = [r.payload for r in rows if r.kind is RawTraceKind.AUDIT_DETAIL]
    destroyed = [action for action, raw in lossy if raw not in payloads]
    assert not destroyed, (
        f"these audited details lost content with no vault row holding the raw: "
        f"{destroyed}. That is destruction, not withholding."
    )


async def test_C1_a_RETRIED_logical_write_reuses_one_vault_row() -> None:
    """R12 finding 5, driven THROUGH `_audit` rather than the helper beneath it.

    The earlier idempotency test called `record_audit_detail` directly with a
    fixed id, which proved the vault key is stable but said nothing about the
    path that actually mints ids. Through `_audit`, every call minted a fresh
    one — so re-driving a write whose append had failed left a second vault
    row behind and only one entry.
    """
    engine = _engine()
    inst = await _instance(engine)
    entry_id = "logical-write-1"

    original = engine.repositories.audit.append
    calls = {"n": 0}

    async def flaky(entry: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("audit store unavailable")
        return await original(entry)

    engine.repositories.audit.append = flaky  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="audit store unavailable"):
        await engine._audit(
            "tool_param_override_blocked",
            actor_type="agent",
            actor_id="a",
            instance_id=inst.id,
            detail=RAW_DETAIL,
            entry_id=entry_id,
        )
    # The retry: same logical write, same identity.
    await engine._audit(
        "tool_param_override_blocked",
        actor_type="agent",
        actor_id="a",
        instance_id=inst.id,
        detail=RAW_DETAIL,
        entry_id=entry_id,
    )

    rows = await _vault_rows(engine, inst.id)
    assert len(rows) == 1, f"the retry orphaned a second vault row: {len(rows)} rows"
    entries = [
        e
        for e in await engine.repositories.audit.list_recent(limit=50)
        if e.action == "tool_param_override_blocked"
    ]
    assert len(entries) == 1, f"the retry duplicated the audit entry: {len(entries)}"
    assert entries[0].id == rows[0].audit_entry_id, "the entry and its vault row disagree"


async def test_C1_WITHOUT_an_explicit_id_two_calls_are_two_events() -> None:
    """The default must stay 'new event', or the multiplicity case breaks:
    two identical tool calls on one step attempt are two records, not one."""
    engine = _engine()
    inst = await _instance(engine)
    for _ in range(2):
        await engine._audit(
            "tool_param_override_blocked",
            actor_type="agent",
            actor_id="a",
            instance_id=inst.id,
            detail=RAW_DETAIL,
        )
    assert len(await _vault_rows(engine, inst.id)) == 2
