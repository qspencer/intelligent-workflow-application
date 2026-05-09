"""Tests for capability resolution + ACL enforcement."""

from __future__ import annotations

from workflow_platform.security import (
    UNRESTRICTED,
    CapabilityPolicy,
    resolve_capabilities,
)

# --- tool allowlist ---


def test_unrestricted_allows_anything() -> None:
    assert UNRESTRICTED.tool_allowed("anything")
    assert UNRESTRICTED.can_read("/x/y")
    assert UNRESTRICTED.can_write("/anywhere")
    assert UNRESTRICTED.host_allowed("api.example.com")
    assert UNRESTRICTED.max_tokens_per_call is None


def test_single_layer_tool_allowlist() -> None:
    caps = resolve_capabilities(CapabilityPolicy(tools=["pdf_extract", "file_read"]))
    assert caps.tool_allowed("pdf_extract")
    assert not caps.tool_allowed("file_write")


def test_layered_tool_intersection_most_restrictive_wins() -> None:
    caps = resolve_capabilities(
        CapabilityPolicy(tools=["pdf_extract", "file_read", "file_write"]),
        CapabilityPolicy(tools=["pdf_extract", "file_read"]),
        CapabilityPolicy(tools=["file_read"]),
    )
    assert caps.tool_allowed("file_read")
    assert not caps.tool_allowed("pdf_extract")
    assert not caps.tool_allowed("file_write")


def test_none_layers_dont_constrain() -> None:
    caps = resolve_capabilities(
        CapabilityPolicy(tools=None),
        CapabilityPolicy(tools=["a"]),
    )
    assert caps.tool_allowed("a")
    assert not caps.tool_allowed("b")


def test_runtime_cannot_widen_system() -> None:
    """Even if a step declares broader tools, the system layer wins."""
    caps = resolve_capabilities(
        CapabilityPolicy(tools=["safe_only"]),  # system
        CapabilityPolicy(tools=["safe_only", "dangerous"]),  # workflow
        CapabilityPolicy(tools=["dangerous"]),  # step
    )
    # Step listed `dangerous` but system blocks it.
    assert not caps.tool_allowed("dangerous")
    # `safe_only` excluded by step (intersection).
    assert not caps.tool_allowed("safe_only")


# --- glob ACLs ---


def test_file_read_glob_matching() -> None:
    caps = resolve_capabilities(CapabilityPolicy(file_read=["/inbox/*.pdf"]))
    assert caps.can_read("/inbox/invoice.pdf")
    assert not caps.can_read("/inbox/sub/nested.pdf")
    assert not caps.can_read("/inbox/notes.txt")


def test_file_write_layered_acls() -> None:
    caps = resolve_capabilities(
        CapabilityPolicy(file_write=["/processed/*", "/archive/*"]),
        CapabilityPolicy(file_write=["/processed/*"]),
    )
    assert caps.can_write("/processed/x")
    assert not caps.can_write("/archive/x")  # second layer doesn't allow it
    assert not caps.can_write("/etc/passwd")


def test_empty_list_means_deny_all() -> None:
    caps = resolve_capabilities(CapabilityPolicy(file_read=[]))
    assert not caps.can_read("/anything")


# --- token cap aggregation ---


def test_max_tokens_takes_min_across_layers() -> None:
    caps = resolve_capabilities(
        CapabilityPolicy(max_tokens_per_call=100_000),
        CapabilityPolicy(max_tokens_per_call=50_000),
        CapabilityPolicy(max_tokens_per_call=None),
    )
    assert caps.max_tokens_per_call == 50_000


def test_max_tokens_none_when_no_layer_specifies() -> None:
    caps = resolve_capabilities(
        CapabilityPolicy(),
        CapabilityPolicy(),
    )
    assert caps.max_tokens_per_call is None
