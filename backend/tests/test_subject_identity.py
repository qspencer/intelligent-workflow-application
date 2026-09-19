"""Typed subject identity (G-Trace-Subject-Identity stages 1-3).

`docs/TRACE_SUBJECT_IDENTITY_PLAN.md`. The property the whole design turns
on: a reader without a raw-trace grant can tell that two entries are about
the SAME subject, and still cannot tell WHO.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from workflow_platform.main import create_app
from workflow_platform.persistence import User, in_memory_repositories
from workflow_platform.subject_identity import (
    KEY_ENV_VAR,
    SubjectKind,
    subject_from_namespace,
    subject_from_user_id,
)
from workflow_platform.trace_projection import project_audit_detail_at_rest

_ADMIN = {"X-Dev-User": "admin-1", "X-Dev-Groups": "admins"}


@pytest.fixture(autouse=True)
def _keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV_VAR, "test-subject-key")


# --- stage 1: the typed reference ---


def test_a_mailbox_namespace_classifies_as_a_mailbox_not_a_platform_user() -> None:
    """The reviewer's second constraint, which is 6,351 of 6,354 live rows:
    *"Give those subjects their own typed identity instead of assuming
    every `user_id` joins to `users`."*"""
    ref = subject_from_namespace("org:default:user:alice@example.com")
    assert ref.kind is SubjectKind.MAILBOX
    assert ref.org_id == "default"
    assert ref.ref is not None and ref.ref.startswith("subj:")


def test_the_same_address_in_two_orgs_gets_DIFFERENT_refs() -> None:
    """THE tenant-isolation property. Keyed over the address alone the ref
    would be a cross-tenant join key: two orgs corresponding with the same
    person would mint the same value, and a reader entitled to one org's
    audit could correlate into another's."""
    a = subject_from_namespace("org:alpha:user:alice@example.com")
    b = subject_from_namespace("org:beta:user:alice@example.com")
    assert a.ref != b.ref


def test_the_ref_is_stable_and_follows_the_memory_stores_normalization() -> None:
    """Correlation only works if the ref is stable, and it must agree with
    the store about WHO the subject is — a pseudonym that split on
    `User+Tag@x` when the memory namespace does not would disagree with the
    partition it describes."""
    plain = subject_from_namespace("org:default:user:alice@example.com")
    again = subject_from_namespace("org:default:user:alice@example.com")
    folded = subject_from_namespace("org:default:user:Alice+newsletter@Example.com")
    assert plain.ref == again.ref == folded.ref


def test_the_ref_does_not_contain_the_address() -> None:
    """The one thing it must never do."""
    ref = subject_from_namespace("org:default:user:alice@example.com")
    assert ref.ref is not None
    assert "alice" not in ref.ref and "@" not in ref.ref


def test_no_key_configured_means_NO_ref_rather_than_an_unkeyed_hash() -> None:
    """An unkeyed digest over a small address space is reversible by
    anyone who can guess an address — which is the disclosure the
    pseudonym exists to prevent. None is the honest answer."""
    import os

    os.environ.pop(KEY_ENV_VAR, None)
    ref = subject_from_namespace("org:default:user:alice@example.com")
    assert ref.kind is SubjectKind.MAILBOX
    assert ref.ref is None
    assert "ref" not in ref.as_detail(), "an absent ref must not be emitted as null"


def test_a_platform_user_keeps_its_own_id() -> None:
    """`users.id` is already opaque and already stable across a rename, so
    pseudonymizing it would buy nothing and cost the directory its
    resolution."""
    ref = subject_from_user_id("u-123", org_id="alpha")
    assert ref.kind is SubjectKind.PLATFORM_USER
    assert ref.ref == "u-123"
    assert ref.org_id == "alpha"


def test_a_namespace_that_is_not_an_address_is_UNKNOWN_not_guessed() -> None:
    for value in ("org:default:user:some-opaque-key", "not-a-namespace", "org:default:user:"):
        ref = subject_from_namespace(value)
        assert ref.kind is SubjectKind.UNKNOWN, value
        assert ref.ref is None


# --- stage 2: what a grant-less reader sees ---


def test_the_subject_survives_projection_while_the_raw_key_does_not() -> None:
    """THE POINT, in one assertion. Same-subject is answerable below a
    grant; who-is-it is not."""
    ref = subject_from_namespace("org:default:user:alice@example.com")
    detail = {
        "user_id": "org:default:user:alice@example.com",
        "subject": ref.as_detail(),
        "facts": 2,
    }
    projected = project_audit_detail_at_rest("memory_observed", detail)

    assert projected["subject"] == ref.as_detail(), "the correlator was withheld"
    assert "user_id" not in projected, "the raw namespace key survived"
    assert "alice" not in str(projected)


def test_two_entries_about_one_subject_are_correlatable_after_projection() -> None:
    a = project_audit_detail_at_rest(
        "memory_observed",
        {"subject": subject_from_namespace("org:default:user:alice@example.com").as_detail()},
    )
    b = project_audit_detail_at_rest(
        "memory_recalled",
        {"subject": subject_from_namespace("org:default:user:alice@example.com").as_detail()},
    )
    assert a["subject"]["ref"] == b["subject"]["ref"]


def test_a_forged_subject_kind_is_redacted() -> None:
    out = project_audit_detail_at_rest(
        "memory_observed",
        {"subject": {"kind": "SYNTHETIC", "ref": "subj:abc", "org_id": "default"}},
    )
    assert out["subject"]["kind"] != "SYNTHETIC"


# --- stage 3: directory resolution ---


def _app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Any]:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repos = in_memory_repositories()
    return TestClient(create_app(repositories=repos, start_triggers=False)), repos


def test_a_platform_user_ref_resolves_to_a_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, repos = _app(monkeypatch)
    user = asyncio.run(
        repos.users.save(
            User(iss="local", sub="s1", email="alice@example.com", display_name="Alice A")
        )
    )
    r = client.post("/api/directory/resolve", headers=_ADMIN, json={"refs": [user.id]})
    assert r.status_code == 200
    assert r.json()["results"] == [
        {
            "ref": user.id,
            "outcome": "resolved",
            "display_name": "Alice A",
            "org_id": user.org_id,
        }
    ]


def test_a_mailbox_ref_is_NOT_resolvable_by_the_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a gap — the boundary between the two permissions doing its job.
    The pseudonym is not reversible and there is deliberately no mapping
    table to reverse it with (plan §4); the address lives in the vault,
    which is grant-gated rather than directory-gated."""
    client, _ = _app(monkeypatch)
    r = client.post(
        "/api/directory/resolve", headers=_ADMIN, json={"refs": ["subj:284a09228dcf85"]}
    )
    assert r.json()["results"][0]["outcome"] == "not_resolvable_by_directory"


def test_the_audit_entry_records_outcomes_and_NEVER_the_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*"Record identifiers and outcomes, never copy display identities
    into the records."* A directory lookup that logged the names would put
    the identities back into the store the subject reference exists to keep
    them out of."""
    client, repos = _app(monkeypatch)
    user = asyncio.run(
        repos.users.save(User(iss="local", sub="s2", email="bob@example.com", display_name="Bob B"))
    )
    client.post(
        "/api/directory/resolve",
        headers=_ADMIN,
        json={"refs": [user.id, "subj:deadbeef", "missing-id"]},
    )
    entries = asyncio.run(repos.audit.list_recent(limit=20))
    entry = next(e for e in entries if e.action == "directory_resolved")
    assert entry.detail == {
        "requested": 3,
        "resolved": 1,
        "not_found": 1,
        "not_resolvable": 1,
    }
    assert "Bob" not in str(entry.detail) and "bob@example.com" not in str(entry.detail)


def test_one_audit_entry_per_REQUEST_not_per_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """*"Request-level access records, batch-friendly — not one event per
    rendered label."* Per-label events would make the audit log a record of
    what a UI happened to draw."""
    client, repos = _app(monkeypatch)
    client.post(
        "/api/directory/resolve",
        headers=_ADMIN,
        json={"refs": [f"missing-{i}" for i in range(25)]},
    )
    entries = asyncio.run(repos.audit.list_recent(limit=50))
    assert len([e for e in entries if e.action == "directory_resolved"]) == 1


def test_the_directory_permission_is_not_the_raw_trace_grant() -> None:
    """*"…under explicit DIRECTORY permissions, independent of raw-trace
    grants."* Reading a person's name is a different question from reading
    a message body; tying them together would over-grant the first or make
    the second routine.

    Checked by IMPORTS rather than by prose: the module must not reach the
    grant machinery at all. A substring scan over the source would trip on
    the comments that explain the separation, which is the proxy-instead-of-
    property mistake in miniature.
    """
    import ast
    import pathlib as _pathlib

    from workflow_platform.api.directory import DIRECTORY_ROLES
    from workflow_platform.auth.rbac import Role

    assert DIRECTORY_ROLES == (Role.ADMINISTRATOR, Role.ORG_ADMIN)

    tree = ast.parse(_pathlib.Path("src/workflow_platform/api/directory.py").read_text())
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    grant_modules = {m for m in imported if "grant" in m or "raw_trace" in m}
    assert not grant_modules, (
        f"the directory endpoint imports raw-trace machinery {sorted(grant_modules)}; "
        "the two permissions are specified as independent"
    )


def test_a_viewer_cannot_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _app(monkeypatch)
    r = client.post(
        "/api/directory/resolve",
        headers={"X-Dev-User": "v", "X-Dev-Groups": "org-viewers"},
        json={"refs": ["x"]},
    )
    assert r.status_code == 403


def test_the_batch_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unbounded directory endpoint is an enumeration endpoint."""
    client, _ = _app(monkeypatch)
    r = client.post(
        "/api/directory/resolve",
        headers=_ADMIN,
        json={"refs": [f"r{i}" for i in range(500)]},
    )
    assert r.status_code == 400
