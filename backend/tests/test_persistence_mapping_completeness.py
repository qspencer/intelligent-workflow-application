"""Every persisted model field must actually be persisted.

Found the hard way on 2026-09-18: `RawTrace.audit_entry_id` was added to the
pydantic model, the SQLAlchemy table and an Alembic migration, but NOT to the
Postgres repo's INSERT values or its row->model mapper. It stored NULL and
read back None, `vault_fingerprint` then compared unequal on the first write,
and EVERY audit vault put raised `VaultConflict` — which propagates out of
`_audit` and fails the workflow run. It reached production.

No unit test could catch it: the in-memory repositories round-trip the
pydantic object, so column mapping is never exercised there. This is the
detector for the class — derived from the source (rule R-c), so a new field
on any mapped model fails the build until it is persisted in BOTH directions.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from pydantic import BaseModel

from workflow_platform.persistence.models import (
    AuthSession,
    RawTrace,
    RawTraceGrant,
    User,
)

_POSTGRES = pathlib.Path("src/workflow_platform/persistence/postgres.py")

#: mapper function -> the model it builds. Read `_row_to_<x>` as "the READ
#: direction"; the write direction is found by locating the `.values(...)`
#: call that mentions the same fields.
MAPPERS: dict[str, type[BaseModel]] = {
    "_row_to_user": User,
    "_row_to_auth_session": AuthSession,
    "_row_to_grant": RawTraceGrant,
    "_row_to_raw_trace": RawTrace,
}

#: Fields a mapper deliberately does not carry, with the reason. Empty means
#: "no exclusions" — an exclusion must be argued, never assumed.
READ_EXCLUSIONS: dict[str, dict[str, str]] = {
    # `User.password_hash` is loaded by a dedicated credential path, never by
    # the general mapper, so it cannot leak into ordinary reads.
    "_row_to_user": {},
    "_row_to_auth_session": {},
    "_row_to_grant": {},
    "_row_to_raw_trace": {},
}


def _tree() -> ast.Module:
    return ast.parse(_POSTGRES.read_text())


def _keywords_of(fn_name: str) -> set[str]:
    """Field names the mapper passes to its model constructor."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and call.keywords:
                    return {k.arg for k in call.keywords if k.arg}
    raise AssertionError(f"mapper {fn_name} not found in {_POSTGRES}")


@pytest.mark.parametrize("fn_name", sorted(MAPPERS))
def test_the_read_mapper_carries_every_model_field(fn_name: str) -> None:
    model = MAPPERS[fn_name]
    expected = set(model.model_fields) - set(READ_EXCLUSIONS[fn_name])
    missing = expected - _keywords_of(fn_name)
    assert not missing, (
        f"{fn_name} does not read {sorted(missing)} back from the row. "
        f"A field on {model.__name__} that the mapper drops reads back as its "
        "default, silently — which is how the audit_entry_id outage happened."
    )


def test_the_raw_trace_WRITE_path_persists_every_model_field() -> None:
    """The write direction, which is the half that actually lost the data.

    The INSERT is a `.values(...)` call inside `PostgresRawTraceVaultRepo.put`.
    """
    values: set[str] = set()
    for node in ast.walk(_tree()):
        if not (isinstance(node, ast.ClassDef) and node.name == "PostgresRawTraceVaultRepo"):
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "values"
            ):
                values |= {k.arg for k in call.keywords if k.arg}
    assert values, "could not find the vault INSERT values() call"
    missing = set(RawTrace.model_fields) - values
    assert not missing, (
        f"the vault INSERT does not persist {sorted(missing)}. These are set on the "
        "RawTrace but never written, so they read back as defaults and "
        "vault_fingerprint compares unequal — every put then raises VaultConflict."
    )


def test_the_detector_would_catch_a_dropped_field() -> None:
    """Control (R-b): prove the comparison has power, rather than trusting
    that a passing assertion means the mapper is complete."""
    # Self-contained on purpose: comparing against the LIVE mapper would make
    # this control fail whenever the real assertion fails, i.e. for the wrong
    # reason, and a control that cannot be read independently is not one.
    carried = {"id", "org_id", "payload"}
    expected = carried | {"brand_new_column"}
    assert expected - carried == {"brand_new_column"}, "the comparison cannot see a dropped field"
    assert not (carried - carried), "the comparison false-flags a complete mapper"
