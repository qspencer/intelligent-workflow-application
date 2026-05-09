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

from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, Field


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
    """File-ACL matcher: `*` does not cross `/`. Use multiple patterns to allow
    multiple subtrees. Recursive `**` is not supported in Phase 1; if needed,
    list explicit patterns (e.g. `["/processed/*", "/processed/*/*"]`)."""
    return PurePosixPath(path).match(pattern)


def resolve_capabilities(*layers: CapabilityPolicy | None) -> ResolvedCapabilities:
    """Build a resolved capability set from an ordered stack of layers.

    `None` layers are skipped; empty layers contribute nothing (unrestricted).
    """
    return ResolvedCapabilities(layers=[layer for layer in layers if layer is not None])


UNRESTRICTED: ResolvedCapabilities = ResolvedCapabilities(layers=[])
"""A capability set that allows everything — useful for tests and the default
when no policy is configured."""
