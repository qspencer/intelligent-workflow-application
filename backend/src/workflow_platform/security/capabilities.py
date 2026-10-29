"""Capability model + resolution.

A `CapabilityPolicy` is one *layer* of constraints — system-wide, per-workflow,
or per-step. `ResolvedCapabilities` is the *effective* capability set after
intersecting a stack of layers; it's what a running tool actually checks.

Inheritance rule (most restrictive wins):

    System → Workflow → Step → Runtime

Each layer can independently say "I don't restrict attr X" (`None`) or "here's
my allow-list" (a list — possibly empty for deny-all). A check passes only if
every layer that *does* restrict the attr accepts it.
"""

from __future__ import annotations

import os
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, field_validator


class CapabilityPolicy(BaseModel):
    """One layer of capability constraints.

    For each list-valued attribute: `None` means "this layer imposes no
    constraint"; a list (including empty) means "this layer allows only what's
    listed." Empty list = deny-all.
    """

    tools: list[str] | None = None
    file_read: list[str] | None = None
    file_write: list[str] | None = None
    allowed_hosts: list[str] | None = None
    max_tokens_per_call: int | None = None

    @field_validator("file_read", "file_write")
    @classmethod
    def _patterns_are_absolute(cls, patterns: list[str] | None) -> list[str] | None:
        """Refuse, loudly, a file pattern the matcher could never honour: a
        relative one (it used to match from the right — any directory of that
        name, anywhere) or one with a `..` segment. Silently never matching
        would be fail-closed but would hide an author's mistake."""
        for pattern in patterns or []:
            if not pattern.startswith("/") or ".." in PurePosixPath(pattern).parts:
                raise ValueError(
                    f"file pattern {pattern!r} must be absolute and contain no '..' segment"
                )
        return patterns


class ResolvedCapabilities(BaseModel):
    """Effective capabilities at runtime.

    Built from a stack of `CapabilityPolicy` layers. The instance answers
    membership checks (`tool_allowed`, `can_read`, ...) by AND-ing across the
    layers that actually constrain the attribute.
    """

    layers: list[CapabilityPolicy] = Field(default_factory=list)

    def tool_allowed(self, name: str) -> bool:
        for layer in self.layers:
            if layer.tools is not None and name not in layer.tools:
                return False
        return True

    def can_read(self, path: str) -> bool:
        return self._all_layers_match("file_read", path, _path_match)

    def can_write(self, path: str) -> bool:
        return self._all_layers_match("file_write", path, _path_match)

    @property
    def restricts_hosts(self) -> bool:
        """Whether ANY layer restricts `allowed_hosts`. When none does, callers
        apply no host policy at all — `host_allowed` would vacuously pass every
        http(s) host but a host policy also refuses hostless schemes (`file:`,
        `data:`), and an unrestricted run must behave exactly as it always has."""
        return any(layer.allowed_hosts is not None for layer in self.layers)

    def host_allowed(self, host: str) -> bool:
        return self._all_layers_match("allowed_hosts", host, fnmatch)

    @property
    def max_tokens_per_call(self) -> int | None:
        caps = [
            layer.max_tokens_per_call
            for layer in self.layers
            if layer.max_tokens_per_call is not None
        ]
        return min(caps) if caps else None

    def _all_layers_match(self, attr: str, value: str, matcher: object) -> bool:
        match_fn: Any = matcher
        for layer in self.layers:
            globs: list[str] | None = getattr(layer, attr)
            if globs is None:
                continue
            if not any(match_fn(value, g) for g in globs):
                return False
        return True


def _path_match(path: str, pattern: str) -> bool:
    """File-ACL matcher. `*` matches within one segment; `**` is a real
    recursive wildcard.

    Corrected 2026-10-29 (G30 phase 2). It was `PurePosixPath(path).match`,
    which had three faults, each found by testing it before relying on it:
    `*` matched `..`, so `/data/allowed/*/*` admitted `/data/allowed/../secret`
    — a path OUTSIDE the allowed tree; a relative pattern matched from the
    right, so `spool/*` allowed any `spool` directory anywhere; and `**` acted
    as a single `*`, so a pattern that looked recursive was not.

    Now: the path and the pattern must both be absolute; a path with a `..`
    segment is REFUSED rather than normalized (lexical normalization can
    disagree with how the OS resolves `..` through a symlink, so the path
    checked and the path opened could differ); and the WHOLE path must match
    (`full_match`). Residual, stated: a symlink inside an allowed tree that
    points out of it is followed by the OS — the check is on the path, not on
    what it resolves to."""
    if not path.startswith("/") or not pattern.startswith("/") or "\x00" in path:
        return False
    candidate = PurePosixPath(path)
    if ".." in candidate.parts:
        return False
    return candidate.full_match(pattern)


def resolve_capabilities(*layers: CapabilityPolicy | None) -> ResolvedCapabilities:
    """Build a resolved capability set from an ordered stack of layers.

    `None` layers are skipped; empty layers contribute nothing (unrestricted).
    """
    return ResolvedCapabilities(layers=[layer for layer in layers if layer is not None])


UNRESTRICTED: ResolvedCapabilities = ResolvedCapabilities(layers=[])
"""A capability set that allows everything — useful for tests and the default
when no policy is configured."""


def cap_output_tokens(config: dict[str, Any] | None, cap: int | None) -> dict[str, Any] | None:
    """Clamp a model call's `maxTokens` (its OUTPUT ceiling) to `cap`. ONE
    implementation for every model call the capability layer bounds.

    It began in the agent loop alone (G30 phase 1), so `max_tokens_per_call`
    governed agent steps and nothing else. An independent red-team
    (2026-10-29) showed the question-suggestion call sending
    `maxTokens: 100000` — a size a scaffolded draft controls — and
    learned-memory distillation sent none at all. A request that asks for less
    keeps its own figure. With no cap the config is returned UNCHANGED (the
    same object), so a recorded replay request hashes exactly as before."""
    if cap is None:
        return config
    capped = dict(config or {})
    requested = capped.get("maxTokens")
    capped["maxTokens"] = min(requested, cap) if isinstance(requested, int) else cap
    return capped


#: Names the YAML file holding the SYSTEM layer. Unset = no system layer, which
#: `/api/health` reports as `absent`. The production launcher sets it.
SYSTEM_CAPABILITIES_ENV = "WORKFLOW_PLATFORM_SYSTEM_CAPABILITIES"


class SystemCapabilitiesError(ValueError):
    """The configured system layer cannot be loaded. Never ignored: starting
    without the layer an operator configured would fail OPEN."""


def load_system_capabilities(path: Path) -> CapabilityPolicy:
    """Load the system layer, refusing anything that could silently widen it.

    Every attribute must be present (an omitted one means unrestricted, so a
    misspelt key would fail open) and no unknown key is allowed (the misspelt
    key itself). `null` is how a layer says "I do not restrict this" — it has
    to be written down."""
    import yaml

    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise SystemCapabilitiesError(f"cannot read system capabilities {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemCapabilitiesError(f"{path}: expected a mapping of capability attributes")
    expected = set(CapabilityPolicy.model_fields)
    missing, unknown = expected - set(data), set(data) - expected
    if missing or unknown:
        raise SystemCapabilitiesError(
            f"{path}: every attribute must be stated and no other may appear "
            f"(missing: {sorted(missing)}, unknown: {sorted(unknown)})"
        )
    try:
        return CapabilityPolicy.model_validate(data)
    except ValueError as exc:
        raise SystemCapabilitiesError(f"{path}: {exc}") from exc


#: The tracked layer production runs with. capabilities.py lives at
#: <repo>/backend/src/workflow_platform/security/, so parents[4] is the repo
#: root — resolved from this file, not the CWD (the `templates.py` convention).
SHIPPED_SYSTEM_CAPABILITIES = (
    Path(__file__).resolve().parents[4] / "config" / "system_capabilities.yaml"
)


def operator_system_capabilities() -> CapabilityPolicy:
    """The layer for OPERATOR SCRIPTS that build their own engine (`fire.py`,
    the batch runners, `replay.py`, ...). Never None: the layer named by the
    environment, else the shipped file.

    Found in the G30 pre-package review (2026-10-29): seven scripts built a
    `WorkflowEngine` with no system layer, so the config's "OUTERMOST bound on
    every workflow" held only for workflows the SERVICE ran — not for
    `fire.py`, which runs definitions against the production database with live
    tools. A script that needs more than production allows names a different,
    reviewed layer file through the environment; it does not run without one."""
    configured = os.environ.get(SYSTEM_CAPABILITIES_ENV)
    return load_system_capabilities(Path(configured) if configured else SHIPPED_SYSTEM_CAPABILITIES)


def system_capabilities_from_env() -> CapabilityPolicy | None:
    """The system layer named by the environment, or None when none is named."""
    configured = os.environ.get(SYSTEM_CAPABILITIES_ENV)
    if not configured:
        return None
    return load_system_capabilities(Path(configured))


def describe_system_capabilities(policy: CapabilityPolicy | None) -> dict[str, Any]:
    """What `/api/health` shows. COUNTS, never contents: the endpoint is
    unauthenticated and tool names embed mailbox addresses."""
    if policy is None:
        return {"status": "absent"}

    def shape(values: list[str] | None) -> str | int:
        return "unrestricted" if values is None else len(values)

    return {
        "status": "configured",
        "tools": shape(policy.tools),
        "file_read": shape(policy.file_read),
        "file_write": shape(policy.file_write),
        "allowed_hosts": shape(policy.allowed_hosts),
        "max_tokens_per_call": policy.max_tokens_per_call or "unrestricted",
    }
